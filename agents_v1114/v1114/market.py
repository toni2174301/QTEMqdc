"""Fresh market data and deterministic data-quality checks."""
import csv
import math
import re
import statistics
from datetime import timedelta
from html.parser import HTMLParser
from io import StringIO
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen

DOWNLOADS = "https://www.cashmarket.deutsche-boerse.com/cash-en/trading/Tradable-Instruments-Xetra/Downloads"
VENUES = {
    "XETR": ("xetra-downloads", ".DE"),
    "XFRA": ("frankfurt-downloads", ".F"),
}
BENCHMARK = "IE00B4L5Y983"
# EUR listings confirmed against the issuer/exchange; quotes are still fetched fresh.
OTHER_LISTINGS = {
    "IE00BLRPRL42": {
        "symbol": "QQQ3.MI", "mic": "XMIL", "currency": "EUR",
        "identity_source": "https://www.wisdomtree.eu/de-de/etps/equities/wisdomtree-nasdaq-100-3x-daily-leveraged",
    },
    "FR0000021842": {
        "symbol": "BEL.BR", "mic": "XBRU", "currency": "EUR",
        "identity_source": "https://www.amundietf.de/de/privatanleger/products/equity/amundi-bel-20-ucits-etf-dist/fr0000021842",
    },
    "IE00B8JVMZ80": {
        "symbol": "3HCL.MI", "mic": "XMIL", "currency": "EUR",
        "identity_source": "https://www.borsaitaliana.it/borsa/etc-etn/scheda/IE00B8JVMZ80-ETFP.html?lang=en",
    },
    "IE00B94QLN63": {
        "symbol": "2PAL.MI", "mic": "XMIL", "currency": "EUR",
        "identity_source": "https://www.borsaitaliana.it/borsa/etc-etn/scheda/IE00B94QLN63-ETFP.html?lang=en",
    },
}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.extend(value for key, value in attrs if key == "href" and value)


def fetch(url):
    with urlopen(url, timeout=20) as response:
        return response.read()


def parse_reference(data, mic, wanted):
    """Read the exchange's reference file in memory."""
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = data.decode("cp1252")
    lines = content.splitlines()
    if not lines or not re.fullmatch(rf"Market:[;\s]*{mic}(?:[ /-]*BF)?[;\s]*", lines[0]):
        raise ValueError(f"Unexpected {mic} reference file market")
    for index, line in enumerate(lines[:10]):
        headers = {field.strip().lower() for field in next(csv.reader([line], delimiter=";"))}
        if {"isin", "mnemonic"} <= headers and headers & {"trading currency", "currency"}:
            break
    else:
        raise ValueError(f"Missing ISIN, Mnemonic or Currency in {mic} reference file")
    matches = {}
    reader = csv.DictReader(StringIO("\n".join(lines[index:])), delimiter=";")
    for raw in reader:
        row = {key.strip().lower(): (value or "").strip() for key, value in raw.items() if key}
        isin, symbol = row.get("isin"), row.get("mnemonic", "")
        active = all(row.get(key, "active").lower() == "active"
                     for key in ("product status", "instrument status"))
        currency = row.get("trading currency", row.get("currency"))
        if (isin in wanted and active and currency == "EUR"
                and row.get("mic code", mic) == mic and re.fullmatch(r"[A-Z0-9-]+", symbol)):
            matches.setdefault(isin, set()).add(symbol)
    return {isin: next(iter(symbols)) for isin, symbols in matches.items() if len(symbols) == 1}


def load_reference(mic, wanted):
    page, suffix = VENUES[mic]
    index_url = f"{DOWNLOADS}/{page}"
    links = Links()
    links.feed(fetch(index_url).decode("utf-8"))
    filename = {"XETR": "t7-xetr-alltradableinstruments.csv",
                "XFRA": "t7-xfra-bf-alltradableinstruments.csv"}[mic]
    for href in links.hrefs:
        url = urljoin(index_url, href)
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname in {
            "www.cashmarket.deutsche-boerse.com", "cashmarket.deutsche-boerse.com", "www.xetra.com"
        } and parsed.path.lower().endswith("/" + filename)):
            return {isin: {"symbol": name + suffix, "mic": mic, "currency": "EUR", "identity_source": url}
                    for isin, name in parse_reference(fetch(url), mic, wanted).items()}
    raise ValueError(f"Could not find the official {mic} download link")


def session_dates(mic, start, end):
    import exchange_calendars as xcals

    calendar = xcals.get_calendar(mic, start=start, end=end)
    return [stamp.date().isoformat() for stamp in calendar.sessions]


def yahoo_prices(listing, start, end):
    import yfinance as yf

    ticker = yf.Ticker(listing["symbol"])
    table = ticker.history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                           interval="1d", actions=True, auto_adjust=False, timeout=12, raise_errors=True)
    metadata = ticker.history_metadata
    if metadata.get("currency") != listing["currency"] or metadata.get("symbol") != listing["symbol"]:
        raise ValueError("Yahoo symbol or currency does not match the verified listing")
    if "Adj Close" not in table or "Close" not in table:
        raise ValueError("Missing adjusted or provider closing prices")
    fields = {"open": "Open", "high": "High", "low": "Low", "close": "Close",
              "adjusted": "Adj Close", "volume": "Volume", "dividend": "Dividends",
              "split": "Stock Splits"}
    return [{"date": stamp.date().isoformat(), **{
                name: float(row.get(column, float("nan"))) for name, column in fields.items()
            }} for stamp, row in table.iterrows()]


def returns(values):
    return [current / prior - 1 for prior, current in zip(values, values[1:])]


def audit_prices(rows, sessions):
    """Use the last 61 consecutive exchange closes for up to 60 daily returns."""
    by_date, invalid_dates, duplicates = {}, set(), set()
    for row in rows:
        day = row["date"]
        if day in by_date or day in invalid_dates:
            duplicates.add(day)
        values = [row["adjusted"], row["close"], row["dividend"], row["split"]]
        if not all(math.isfinite(value) for value in values) or min(values[:2]) <= 0:
            invalid_dates.add(day)
        else:
            by_date[day] = row
    window = sessions[-61:]
    expected = window[-1]
    valid = {day: by_date[day] for day in window
             if day in by_date and day not in duplicates and day not in invalid_dates}
    tail = []
    for day in reversed(window):
        if day not in valid:
            break
        tail.append(valid[day])
    tail.reverse()
    adjusted = [row["adjusted"] for row in tail]
    changes = returns(adjusted)
    jumps = [row["date"] for row, change in zip(tail[1:], changes) if abs(change) >= 0.25]
    actions = [row for row in tail if row["dividend"] or row["split"]]
    issues = []
    if len(tail) < 61:
        issues.append("fewer_than_61_consecutive_sessions")
    if expected not in valid:
        issues.append("latest_expected_session_missing_or_invalid")
    if jumps:
        issues.append("large_adjusted_move_requires_review")
    status = "unusable" if issues else "usable"
    peak, drawdown = 0, 0
    for price in adjusted:
        peak = max(peak, price)
        drawdown = min(drawdown, price / peak - 1)
    return {"status": status, "issues": issues, "expected_session": expected,
            "last_valid_session": max(valid, default=None),
            "missing_sessions": [day for day in window if day not in valid],
            "duplicate_sessions": sorted(duplicates & set(window)),
            "invalid_sessions": sorted(invalid_dates & set(window)),
            "large_moves": jumps, "corporate_actions": actions, "series": tail,
            "metrics": {
                "latest_daily_return": changes[-1] if changes else None,
                "annualized_volatility_20d": statistics.stdev(changes[-20:]) * math.sqrt(252)
                if len(changes) >= 20 else None,
                "max_drawdown_60d": drawdown if len(changes) >= 60 else None,
            }}
