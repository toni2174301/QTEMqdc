"""Update QDC daily EUR quotes from Yahoo Finance. Requires Python 3.10+."""

import argparse
import csv
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

DATA_DIR = Path(__file__).resolve().parent
DAY = timedelta(days=1)
PRICE_FIELDS = (
    "date", "ticker", "close_eur", "dividend_eur", "split_multiplier",
    "provider_close_eur", "close_adjustment",
)
ISSUE_FIELDS = ("through", "date", "ticker", "status", "detail")


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def write_rows(path, fields, rows):
    # Readers see either the whole previous file or the whole new file.
    with NamedTemporaryFile("w", newline="", encoding="utf-8", dir=path.parent,
                            prefix=f".{path.name}.", delete=False) as file:
        temp = Path(file.name)
        try:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            file.close()
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def fetch_chart(ticker, start, through):
    params = urlencode({
        "period1": int(datetime.combine(start, time.min, timezone.utc).timestamp()),
        "period2": int(datetime.combine(through + DAY, time.min, timezone.utc).timestamp()),
        "interval": "1d",
        "events": "div,splits",
    })
    errors = []
    for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{quote(ticker, safe='')}?{params}"
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urlopen(request, timeout=12) as response:
                chart = json.load(response)["chart"]
            if chart.get("error") or not chart.get("result"):
                raise ValueError(chart.get("error") or "empty chart result")
            return chart["result"][0]
        except (URLError, TimeoutError, KeyError, ValueError) as exc:
            errors.append(f"{host}: {exc}")
    raise ValueError(" / ".join(errors))


def parse_chart(result, ticker, start, through):
    meta = result["meta"]
    if meta["symbol"] != ticker or meta["currency"] != "EUR":
        raise ValueError(f"unexpected symbol or currency: {meta['symbol']} / {meta['currency']}")
    zone = ZoneInfo(meta["exchangeTimezoneName"])

    def local_day(timestamp):
        return datetime.fromtimestamp(timestamp, zone).date()

    closes, dividends, splits = {}, {}, {}
    days = result.get("timestamp", [])
    quotes = result["indicators"]["quote"][0].get("close") or []
    for timestamp, price in zip(days, quotes, strict=True):
        day = local_day(timestamp)
        if start <= day <= through:
            if day in closes:
                raise ValueError(f"duplicate daily bar on {day}")
            closes[day] = float(price) if price is not None and math.isfinite(price) and price > 0 else None

    events = result.get("events", {})
    for event in events.get("dividends", {}).values():
        day = local_day(event["date"])
        if start <= day <= through:
            amount = float(event["amount"])
            if not math.isfinite(amount) or amount < 0:
                raise ValueError(f"invalid dividend on {day}")
            dividends[day] = dividends.get(day, 0.0) + amount
    for event in events.get("splits", {}).values():
        day = local_day(event["date"])
        if start <= day <= through:
            factor = float(event["numerator"]) / float(event["denominator"])
            if day in splits or not math.isfinite(factor) or factor <= 0:
                raise ValueError(f"invalid or duplicate split on {day}")
            splits[day] = factor
    return closes, dividends, splits


def merge_ticker(ticker, existing, closes, dividends, splits):
    # Keep the hand-verified history as the anchor for share units.
    known_splits = {
        day: float(row["split_multiplier"])
        for day, row in existing.items() if float(row["split_multiplier"]) != 1
    }
    for day, factor in splits.items():
        if day in known_splits and not math.isclose(factor, known_splits[day]):
            raise ValueError(f"split conflicts with saved ratio on {day}")
    split_days = known_splits | splits

    for day in dividends.keys() | splits.keys():
        if day not in existing and closes.get(day) is None:
            raise ValueError(f"corporate action on {day} has no usable close")

    valid_closes = {day: price for day, price in closes.items() if price is not None}
    factors = {
        day: math.prod(factor for split_day, factor in split_days.items() if split_day > day)
        for day in valid_closes
    }
    anchors = {}
    for day in existing.keys() & valid_closes.keys():
        # Use only the same split regime when checking whether Yahoo rebased the bars.
        observed = float(existing[day]["close_eur"]) / valid_closes[day]
        anchors.setdefault(factors[day], []).append(observed)

    merged = existing.copy()
    issues = []
    for day, price in valid_closes.items():
        previous = existing.get(day)
        factor = factors[day]
        scale = 1.0
        if previous is None and factor != 1:
            candidates = [
                multiplier for multiplier in (1.0, factor)
                if anchors.get(factor) and all(
                    math.isclose(observed, multiplier, rel_tol=0.002) for observed in anchors[factor]
                )
            ]
            if len(candidates) != 1 or (candidates[0] != 1 and dividends.get(day, 0)):
                raise ValueError(f"pre-split quote or dividend units need review on {day}")
            scale = candidates[0]
        if previous is not None:
            old_dividend = float(previous["dividend_eur"])
            amount = dividends.get(day, old_dividend)
            if old_dividend and not math.isclose(old_dividend, amount, rel_tol=0.002, abs_tol=1e-6):
                raise ValueError(f"dividend conflicts with saved value on {day}")
            if not old_dividend and amount and factor != 1:
                raise ValueError(f"pre-split dividend units need review on {day}")
            stored_price = float(previous["close_eur"])
            if not any(math.isclose(stored_price, p, rel_tol=0.002) for p in (price, price * factor)):
                issues.append((day, "price_revision", "source close differs from saved close; saved value retained"))
            if (not old_dividend and amount) or (day in splits and day not in known_splits):
                merged[day] = {
                    **previous,
                    "dividend_eur": old_dividend or amount,
                    "split_multiplier": split_days.get(day, 1),
                }
            continue

        merged[day] = {
            "date": day.isoformat(),
            "ticker": ticker,
            "close_eur": price * scale,
            "dividend_eur": dividends.get(day, 0.0),
            "split_multiplier": split_days.get(day, 1),
            "provider_close_eur": price,
            "close_adjustment": f"restore_pre_split_units_x{scale:g}" if scale != 1 else "",
        }
    # Existing rows can still carry a confirmed action if today's response omitted the close.
    for day in (dividends.keys() | splits.keys()) - valid_closes.keys():
        old = merged[day]
        amount = float(old["dividend_eur"])
        if day in dividends and not math.isclose(dividends[day], amount, rel_tol=0.002, abs_tol=1e-6):
            raise ValueError(f"new or conflicting dividend on {day} without a usable close")
        merged[day] = {**old, "split_multiplier": split_days.get(day, 1)}
    return merged, issues


def refresh_one(ticker, existing, start, through):
    try:
        result = fetch_chart(ticker, start, through)
        closes, dividends, splits = parse_chart(result, ticker, start, through)
    except (ValueError, KeyError, TypeError, IndexError, ArithmeticError) as exc:
        return existing, set(), [(through, "fetch_failed", str(exc))]
    issues = [
        (day, "missing_source_close", "provider returned an empty or invalid close")
        for day, price in closes.items() if price is None
    ]
    try:
        merged, revisions = merge_ticker(ticker, existing, closes, dividends, splits)
        return merged, set(closes), issues + revisions
    except ValueError as exc:
        return existing, set(closes), issues + [(through, "action_review", str(exc))]



def latest_completed_date():
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    return now.date() if now.hour >= 19 else now.date() - DAY


def history_path(data_dir):
    for filename in ("qdc-50-daily-eur-updated.csv", "qdc-50-daily-eur.csv"):
        path = data_dir / filename
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"Add qdc-50-daily-eur.csv or qdc-50-daily-eur-updated.csv to {data_dir}. "
        "See README.md for the required history format."
    )


def load_history(data_dir):
    """Validate the EUR universe and load the latest local price file."""
    universe_path = data_dir / "qdc-50-universe.csv"
    assets = read_rows(universe_path)
    tickers = [row["ticker"] for row in assets]
    if len(tickers) != 50 or len(set(tickers)) != 50 or len({row["isin"] for row in assets}) != 50:
        raise ValueError("expected 50 distinct ISINs and quote tickers")
    if any(row["currency"] != "EUR" for row in assets):
        raise ValueError("all quote listings must use EUR")

    history = {ticker: {} for ticker in tickers}
    for row in read_rows(history_path(data_dir)):
        day = date.fromisoformat(row["date"])
        ticker = row["ticker"]
        if ticker not in history or day in history[ticker] or tuple(row) != PRICE_FIELDS:
            raise ValueError("unexpected ticker, duplicate bar, or incompatible price CSV schema")
        history[ticker][day] = row
    if any(not rows for rows in history.values()):
        raise ValueError("the seed price CSV must contain at least one row for every ticker")
    return history


def refresh_prices(history, through):
    """Fetch and reconcile daily prices and corporate actions for all tickers."""
    if through > latest_completed_date():
        raise ValueError("use a completed session; today's quotes can be requested from 19:00 Europe/Berlin")
    latest = max(max(rows) for rows in history.values())
    if through < latest:
        raise ValueError(f"existing snapshot already goes through {latest}; choose a date on or after it")

    tickers = list(history)
    # Revisit a week of overlap and all dates after the least recent listing.
    start = min(max(rows) for rows in history.values()) - 7 * DAY
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {ticker: pool.submit(refresh_one, ticker, history[ticker], start, through)
                   for ticker in tickers}
        for ticker, future in futures.items():
            results[ticker] = future.result()

    issues = []
    updated = {ticker: result[0] for ticker, result in results.items()}
    observed_days = set().union(*(result[1] for result in results.values()))
    observed_days.update(day for rows in updated.values() for day in rows if start <= day <= through)
    for ticker, (rows, _, problems) in results.items():
        issues.extend((day, ticker, status, detail) for day, status, detail in problems)
        issues.extend(
            (day, ticker, "missing_coverage", "no usable saved close; listing may be closed or data delayed")
            for day in observed_days - rows.keys()
        )
    if through.weekday() < 5 and through not in observed_days:
        issues.append((through, "", "no_observed_session", "no listing returned a bar; check holiday or provider delay"))

    return updated, sorted(set(issues))


def save_refresh(data_dir, updated, issues, through):
    """Write the merged price file and the data quality report."""
    output_path = data_dir / "qdc-50-daily-eur-updated.csv"
    issues_path = data_dir / "qdc-50-data-issues.csv"
    write_rows(output_path, PRICE_FIELDS, (
        rows[day] for rows in updated.values() for day in sorted(rows)
    ))
    write_rows(issues_path, ISSUE_FIELDS, (
        dict(zip(ISSUE_FIELDS, (through, day, ticker, status, detail)))
        for day, ticker, status, detail in issues
    ))
    return output_path, issues_path


def refresh_summary(history, updated, issues):
    """Report new rows, tickers requiring attention, and the latest common session."""
    new_rows = sum(len(rows) - len(history[ticker]) for ticker, rows in updated.items())
    failed_tickers = {ticker for _, ticker, status, _ in issues if status in {"fetch_failed", "action_review"}}
    complete_days = set.intersection(*(set(rows) for rows in updated.values()))
    latest_complete = max(complete_days, default=None)
    return new_rows, failed_tickers, latest_complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    latest_allowed = latest_completed_date()
    parser.add_argument("--through", type=date.fromisoformat, default=latest_allowed,
                        help="inclusive date (YYYY-MM-DD); defaults to today after 19:00 Berlin time, otherwise yesterday")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="directory containing the QDC CSVs")
    args = parser.parse_args()
    through = args.through
    try:
        history = load_history(args.data_dir)
        updated, issues = refresh_prices(history, through)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    output_path, issues_path = save_refresh(args.data_dir, updated, issues, through)
    new_rows, failed_tickers, latest_complete = refresh_summary(history, updated, issues)
    print(f"Requested through {through} | {new_rows} new rows | {50 - len(failed_tickers)}/50 checked")
    print(f"Latest complete 50-ticker session: {latest_complete}")
    print(f"Prices: {output_path}\nIssues: {issues_path}")
    return int(bool(failed_tickers))


if __name__ == "__main__":
    sys.exit(main())
