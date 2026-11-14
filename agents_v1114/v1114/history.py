"""Collect and validate exchange sessions and daily prices for local catch-up."""
import math
from collections import Counter
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from .market import yahoo_prices


@lru_cache(maxsize=32)
def exchange_sessions(mic, start, end):
    import exchange_calendars as xcals

    calendar = xcals.get_calendar(mic, start=start - timedelta(days=7),
                                  end=end + timedelta(days=7))
    result = {}
    for session in calendar.sessions_in_range(start, end):
        day = session.date()
        close = calendar.session_close(session).to_pydatetime()
        if mic == "XFRA":
            close = datetime.combine(day, time(22), ZoneInfo("Europe/Berlin"))
        result[day.isoformat()] = close
    return result


def positive(value):
    return value is not None and math.isfinite(value) and value > 0


def audit(rows, sessions, listing, confirmed_moves):
    """Preserve raw values and distinguish price, range and volume quality."""
    counts = Counter(row["date"] for row in rows)
    clean = {}
    for row in rows:
        day = row["date"]
        if day not in sessions:
            continue
        row = {field: value if value is not None and math.isfinite(value) else None
               for field, value in row.items() if field != "date"}
        issues = []
        valid = positive(row["close"]) and positive(row["adjusted"])
        if counts[day] > 1:
            issues.append("duplicate_session")
            valid = False
        if not valid and "duplicate_session" not in issues:
            issues.append("invalid_close")
        o, h, low, c = (row[k] for k in ("open", "high", "low", "close"))
        range_valid = (valid and all(map(positive, (o, h, low)))
                       and h >= max(o, c) and low <= min(o, c))
        if not range_valid:
            issues.append("invalid_ohlc")
        volume = row["volume"]
        if volume is None or volume <= 0:
            issues.append("missing_or_negative_volume" if volume is None or volume < 0
                          else "zero_volume")
        clean[day] = {**row, "issues": issues, "usable_close": valid, "usable_range": range_valid}
    flat = 0
    previous = None
    for day in sessions:
        row = clean.get(day)
        if row and row["usable_close"] and previous:
            move = row["adjusted"] / previous["adjusted"] - 1
            flat = flat + 1 if row["close"] == previous["close"] else 0
            if flat >= 3:
                row["issues"].append("unchanged_close_4_sessions")
            if abs(move) > .10:
                approval = confirmed_moves.get((listing["symbol"], day))
                if approval is not None and math.isclose(approval, move, abs_tol=1e-6):
                    row["issues"].append("large_move_confirmed")
                elif abs(move) >= .25:
                    row["issues"].append("large_move_quarantined")
                    row["usable_close"] = row["usable_range"] = False
                else:
                    row["issues"].append("large_move_review")
        else:
            flat = 0
        if row:
            row["quality_status"] = ("quarantined" if not row["usable_close"] else
                                     "warning" if row["issues"] else "valid")
        # Keep numerically valid prices available for auditing the next change.
        previous = (row if row and counts[day] == 1 and positive(row["adjusted"])
                    and positive(row["close"]) else None)
    return clean



def apply_verified_corrections(stored, listing, sessions, corrections):
    """Overlay documented closes and normalize the known LYBK split for analysis."""
    symbol = listing["symbol"]
    for bar in corrections["quotes"]:
        day = bar["date"]
        if (bar["symbol"] != symbol or day not in sessions
                or any(bar[k] != listing[k] for k in ("mic", "currency"))):
            continue
        existing = stored.get(day, [])
        if len(existing) == 1 and positive(existing[0]["close"]) and positive(existing[0]["adjusted"]):
            if not (symbol == "LYBK.DE" and day == "2026-10-26"
                    and existing[0]["close"] < 10):
                continue
            pre = stored.get("2026-10-23", [])
            if len(pre) != 1 or not positive(pre[0]["adjusted"]) or pre[0]["adjusted"] < 300:
                continue
        row = {k: bar[k] for k in ("close", "adjusted", "open", "high", "low",
                                    "volume", "dividend", "split")}
        # Retain the supplier's current adjustment scale when both verified
        # neighbouring closes identify the same factor.
        anchors = (("2026-03-05", "2026-03-09") if day == "2026-03-06"
                   else ("2026-11-10", "2026-11-11") if symbol == "DJAD.DE"
                   else ())
        if anchors:
            adjacent = [stored.get(d, []) for d in anchors]
            if not all(len(a) == 1 and positive(a[0]["close"])
                       and positive(a[0]["adjusted"]) for a in adjacent):
                continue
            factors = [a[0]["adjusted"] / a[0]["close"] for a in adjacent]
            if not math.isclose(*factors, rel_tol=1e-6):
                continue
            row["adjusted"] = row["close"] * factors[0]
        stored[day] = [row]
    for split in corrections["splits"]:
        if split["symbol"] != symbol or split["date"] not in sessions:
            continue
        pre = stored.get(split["pre_date"], [])
        post = stored.get(split["date"], [])
        if len(pre) != 1 or len(post) != 1:
            continue
        prior, after = pre[0]["adjusted"], post[0]["adjusted"]
        if not (positive(prior) and positive(after)
                and math.isclose(prior / after, split["ratio"], rel_tol=.1)):
            continue
        for day, entries in stored.items():
            if day < split["date"]:
                for row in entries:
                    if positive(row["adjusted"]):
                        row["adjusted"] /= split["ratio"]
    return stored


def fetch_history(listing, sessions, stored, get_prices=yahoo_prices):
    """Fetch missing sessions and recent revisions without mixing adjustment scales."""
    days = list(sessions)
    if not days:
        return {}
    # Before the first observed bar the listing may not have existed. Revisit
    # that range with --full-history when a better source becomes available.
    first = min(stored, default=days[0])
    def needs_retry(day):
        records = stored.get(day, [])
        return (len(records) != 1 or not positive(records[0]["adjusted"])
                or not positive(records[0]["close"]))

    end = days[-1]
    if not stored:
        ranges = [(days[0], end)]
    else:
        # Keep old isolated gaps from turning every startup into a full download.
        needed = [i for i, day in enumerate(days)
                  if day >= first and needs_retry(day)]
        # Existing databases may begin just after the corrected anniversary.
        # Recover those few earlier bars without repeatedly querying years
        # before a newer listing first appeared.
        if first <= (date.fromisoformat(days[0]) + timedelta(days=14)).isoformat():
            needed.extend(i for i, day in enumerate(days) if day < first)
        needed.extend(range(max(0, len(days) - 11), len(days)))
        intervals = []
        for i in sorted(set(needed)):
            # Fetch a nearby known bar as an adjustment-scale check.
            left, right = max(0, i - 1), min(len(days) - 1, i + 1)
            if intervals and left <= intervals[-1][1] + 1:
                intervals[-1][1] = right
            else:
                intervals.append([left, right])
        ranges = [(days[left], days[right]) for left, right in intervals]
    downloaded = [row for first_day, last_day in ranges
                  for row in get_prices(listing, date.fromisoformat(first_day),
                                        date.fromisoformat(last_day))]
    changed_basis = any(
        row["date"] in stored
        and all(positive(point.get(k)) for point in (row, stored[row["date"]][0])
                for k in ("adjusted", "close"))
        and not math.isclose(row["adjusted"] / row["close"],
                             stored[row["date"]][0]["adjusted"] / stored[row["date"]][0]["close"],
                             rel_tol=5e-6)
        for row in downloaded
    )
    # Corporate actions can change the adjusted scale of older observations.
    if changed_basis and ranges != [(days[0], end)]:
        downloaded = get_prices(listing, date.fromisoformat(days[0]), date.fromisoformat(end))
        if not {day for day in stored if day in sessions} <= {row["date"] for row in downloaded}:
            raise ValueError("Adjustment refresh omitted previously stored sessions")
    by_day = {}
    for row in downloaded:
        if row["date"] in sessions:
            normalized = {key: value if key == "date" or (value is not None and math.isfinite(value))
                          else None for key, value in row.items()}
            by_day.setdefault(row["date"], []).append(normalized)
    for day, rows in by_day.items():
        if len(rows) > 1 and all(row == rows[0] for row in rows):
            by_day[day] = rows[:1]
    return by_day
