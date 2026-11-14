"""Run the two agents against a persistent local history."""
import csv
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import fx
from .gemini import AgentError
from .history import apply_verified_corrections, audit, exchange_sessions, fetch_history
from .indicators import RULE_VERSION, classify, period_metrics, risk_metrics, technical_metrics
from .market import BENCHMARK, yahoo_prices
from .store import encode, open_store, read_quotes, save_quotes


def start_date(end, years):
    return (pd.Timestamp(end) - pd.DateOffset(years=years)).date()


def series_id(isin, listing):
    return ":".join([isin, listing["mic"], listing["symbol"], listing["currency"]])


def positive_finite(value):
    return value is not None and 0 < value < float("inf")


def store_ecb(db, listings):
    if all(row["currency"] == "EUR" for row in listings.values()):
        return {}, None
    issue = None
    try:
        rates = fx.load_ecb()
        with db:
            db.executemany("""INSERT INTO fx_ecb VALUES (?, ?, ?)
                              ON CONFLICT(day, currency) DO UPDATE SET rate=excluded.rate""",
                           [(day.isoformat(), currency, rate)
                            for (day, currency), rate in rates.items() if positive_finite(rate)])
    except (OSError, ValueError, ET.ParseError) as exc:
        issue = f"ecb_fetch_failed:{type(exc).__name__}"
    return {(date.fromisoformat(day), currency): rate for day, currency, rate
            in db.execute("SELECT day, currency, rate FROM fx_ecb")}, issue


def collect(db, asset, listing, sessions, full_history, confirmed_moves, get_prices):
    series = series_id(asset["isin"], listing)
    stored = read_quotes(db, series, min(sessions), max(sessions))
    error = None
    for _ in range(2):
        try:
            changes = fetch_history(listing, sessions, {} if full_history else stored, get_prices)
            with db:
                save_quotes(db, series, changes)
            error = None
            stored = read_quotes(db, series, min(sessions), max(sessions))
            break
        except (OSError, ValueError, RuntimeError) as exc:
            error = f"quote_fetch_failed:{type(exc).__name__}"
        except Exception as exc:
            if type(exc).__module__.split(".")[0] not in {"yfinance", "curl_cffi", "requests"}:
                raise
            error = f"quote_fetch_failed:{type(exc).__name__}"
    corrections = json.loads(Path(__file__).with_name("verified_corrections.json").read_text())
    verified = [bar for bar in corrections["quotes"] if bar["isin"] == asset["isin"]]
    corrections["quotes"] = verified
    stored = apply_verified_corrections(stored, listing, sessions, corrections)
    raw = [{"date": day, **row} for day, records in stored.items() for row in records]
    validated = audit(raw, sessions, listing, confirmed_moves)
    for bar in verified:
        row = validated.get(bar["date"])
        if row and row["close"] == bar["close"]:
            row["verified_source"] = bar["verified_source"]
            row["issues"].append("verified_exchange_close_only")
    for split in corrections["splits"]:
        row = validated.get(split["date"])
        if listing["symbol"] == split["symbol"] and row and row["usable_close"]:
            row["verified_split_source"] = split["verified_source"]
    return series, validated, error


def daily_rows(sessions, clean, currency, platform, ecb):
    days = list(sessions)
    index = pd.Index(days)
    local = pd.Series({day: row["adjusted"] for day, row in clean.items() if row["usable_close"]},
                      dtype=float).reindex(index)
    high, low = (pd.Series({day: row[field] * row["adjusted"] / row["close"]
                            for day, row in clean.items() if row["usable_range"]},
                           dtype=float).reindex(index) for field in ("high", "low"))
    unit = 100 if currency == "GBX" else 1
    quotes = [fx.select_rate(date.fromisoformat(day), currency, sessions[day], platform, ecb)
              for day in days]
    rates = pd.DataFrame(quotes, columns=["fx_units_per_eur", "fx_date", "fx_source", "fx_carried"],
                         index=index)
    eur = local / unit / rates["fx_units_per_eur"].astype(float)
    data = pd.DataFrame({"adjusted_close_local": local, "adjusted_close_eur": eur,
                         **risk_metrics(eur), **period_metrics(eur, days),
                         **technical_metrics(local, high, low)}, index=index)
    data = data.join(rates).replace([np.inf, -np.inf], np.nan)
    for column in ("close", "open", "high", "low", "volume", "dividend", "split"):
        data[column] = [clean.get(day, {}).get(column) for day in days]
    data["daily_return_local"] = local.pct_change(fill_method=None)
    data["return_20d_eur"] = eur.pct_change(20, fill_method=None).where(eur.rolling(21).count() == 21)
    data["issues"] = ["|".join(clean[day]["issues"]) if day in clean else "missing_session"
                      for day in days]
    rows = data.astype(object).where(data.notna(), None).to_dict(orient="index")
    snapshot = hashlib.sha256()
    for day, row in rows.items():
        raw = clean.get(day)
        snapshot.update(encode([day, raw, row["fx_units_per_eur"], row["fx_date"],
                                row["fx_source"]]).encode())
        row["input_version"] = snapshot.hexdigest()
        row["quality_status"] = raw["quality_status"] if raw else "missing"
        row["verified_source"] = raw.get("verified_source") if raw else None
        row["verified_split_source"] = raw.get("verified_split_source") if raw else None
        row["signal"], row["trigger"], row["invalidation"] = classify(row)
        row["trade_date"] = day
    return rows


def add_benchmark(rows_by_isin, closes):
    benchmark = rows_by_isin.get(BENCHMARK, {})
    for isin, rows in rows_by_isin.items():
        for day, row in rows.items():
            row["relative_return_20d_eur"] = None
            start = row.pop("comparison_start", None)
            end = benchmark.get(day, {})
            prior = benchmark.get(start, {})
            level, base = end.get("adjusted_close_eur"), prior.get("adjusted_close_eur")
            value = row["return_20d_eur"]
            if (value is not None and level is not None and base is not None
                    and closes[BENCHMARK][day] <= closes[isin][day]
                    and closes[BENCHMARK][start] <= closes[isin][start]):
                row["relative_return_20d_eur"] = value - (level / base - 1)
                row["benchmark_input_version"] = end["input_version"]


def atomic(path, write):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                     dir=path.parent, delete=False) as handle:
        tmp = Path(handle.name)
        try:
            write(handle)
            handle.flush()
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def write_table(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    def write(handle):
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    atomic(path, write)


def publish(db, directory, day, coverage, latest, model_name, interpret, data_only):
    write_table(directory / "coverage.csv", coverage)
    if data_only or not latest:
        return False
    write_table(directory / day / "signals.csv", latest)
    write_table(directory / "latest.csv", latest)
    evidence = [{key: row.get(key) for key in (
        "number", "isin", "name", "symbol", "currency", "expected_session", "last_valid_session", "update_status",
        "signal", "trigger", "invalidation", "trend", "adx14", "rsi14", "atr_pct",
        "risk_samples", "risk_status", "var_95_1d", "cvar_95_1d", "issues",
        "relative_return_20d_eur", "fx_source", "leveraged_or_inverse", "quality_status"
    )} for row in latest]
    fingerprint = hashlib.sha256(
        encode(["narrative-en-v1", RULE_VERSION, model_name, day, latest]).encode()
    ).hexdigest()
    cached = db.execute("SELECT report FROM interpretations WHERE fingerprint=?",
                        (fingerprint,)).fetchone()
    pending = False
    if cached:
        report = cached[0]
    elif any(row.get("last_valid_session") for row in latest):
        try:
            report = interpret(evidence)
            with db:
                db.execute("INSERT INTO interpretations VALUES (?, ?)", (fingerprint, report))
        except AgentError as exc:
            report = f"Gemini commentary pending. {exc}"
            pending = True
    else:
        report = "No market data available. Check coverage.csv and rerun."
        pending = True
    counts = Counter(row.get("signal") or "Not assessable" for row in latest)
    summary = [
        "## Signal distribution (from the CSV)", "", f"**{len(latest)}** instruments.", "", "| Signal | Count |", "| --- | ---: |",
        *(f"| {signal} | {count} |" for signal, count in sorted(counts.items())),
    ]
    text = (f"# QDC Technical Report · {day}\n\n" + "\n".join(summary)
            + f"\n\n## Gemini commentary\n\n{report}\n")
    for path in (directory / day / "report.md", directory / "latest.md"):
        atomic(path, lambda handle: handle.write(text))
    return pending


def run(directory, database, assets, listings, now, end, model_name, interpret, *,
        platform_path=None, confirmed_moves=None, years=10, full_history=False,
        data_only=False, get_prices=yahoo_prices):
    start = start_date(end, years)
    confirmed_moves = confirmed_moves or {}
    platform = fx.load_platform(platform_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    with database.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AgentError("A QDC run is already updating this database.") from None
        with closing(open_store(database)) as db:
            ecb, fx_issue = store_ecb(db, listings)
            return update(db, directory, assets, listings, now, start, end, years, model_name, interpret,
                          platform, ecb, fx_issue, confirmed_moves, full_history, data_only, get_prices)


def update(db, directory, assets, listings, now, start, end, years, model_name, interpret,
           platform, ecb, fx_issue, confirmed_moves, full_history, data_only, get_prices):
    coverage, latest = [], []
    rows_by_isin, closes, series_ids = {}, {}, {}
    for asset in assets:
        isin = asset["isin"]
        listing = listings.get(isin)
        if listing is None:
            coverage.append({**asset, "update_status": "no_verified_listing"})
            continue
        print(f"Data Agent {asset['number']}/{len(assets)}: {listing['symbol']}", flush=True)
        sessions = {day: close for day, close in exchange_sessions(listing["mic"], start - timedelta(days=28), end).items()
                    if close <= now}
        if not sessions:
            coverage.append({**asset, **listing, "update_status": "no_completed_session"})
            continue
        expected = max(sessions)
        anniversary = start_date(date.fromisoformat(expected), years)
        first_session = max(day for day in sessions if day <= anniversary.isoformat())
        sessions = {day: close for day, close in sessions.items() if day >= first_session}
        series, clean, error = collect(db, asset, listing, sessions, full_history,
                                       confirmed_moves, get_prices)
        source = f"https://finance.yahoo.com/quote/{listing['symbol']}/history/"
        master = {"theme": asset.get("contest_label"), "geographic_exposure": None,
                  "inception_date": None, "fund_base_currency": None, "fx_hedged": None,
                  **asset, **listing, "price_source": source}
        with db:
            db.execute("""INSERT INTO instruments VALUES (?, ?)
                          ON CONFLICT(isin) DO UPDATE SET metadata=excluded.metadata""",
                       (isin, encode(master)))
        valid = [day for day in sessions if clean.get(day, {}).get("usable_close")]
        first_observed = min(clean, default=None)
        last_valid = valid[-1] if valid else None
        status = ("current" if last_valid == expected else
                  "quarantined" if expected in clean else "pending_or_missing")
        listing_start = listing.get("listing_date")
        coverage_start = max(first_session, listing_start) if listing_start else first_observed
        active = [day for day in sessions if coverage_start is not None and day >= coverage_start]
        gaps = [day for day in active if day not in clean or not clean[day]["usable_close"]]
        coverage.append({**master, "requested_start": first_session,
                         "first_observed_session": first_observed,
                         "first_valid_session": valid[0] if valid else None,
                         "expected_session": expected, "last_valid_session": last_valid,
                         "valid_sessions": len(valid), "sessions_since_first_observation": len(active),
                         "missing_or_invalid_sessions": len(gaps),
                         "missing_or_invalid_dates": "|".join(gaps),
                         "short_history": first_observed != min(sessions),
                         "coverage_since_first_observation": len(valid) / len(active) if active else None,
                         "flagged_sessions": sum(bool(row["issues"]) for row in clean.values()),
                         "flagged_details": "; ".join(
                             f"{day}={','.join(row['issues'])}" for day, row in clean.items() if row["issues"]
                         ),
                         "update_status": status, "fetch_issue": error,
                         "fx_fetch_issue": fx_issue if listing["currency"] != "EUR" else None})
        if not data_only:
            rows = daily_rows(sessions, clean, listing["currency"], platform, ecb)
            dates = list(sessions)
            for i, day in enumerate(dates):
                rows[day]["comparison_start"] = dates[i - 20] if i >= 20 else None
            rows_by_isin[isin], closes[isin], series_ids[isin] = rows, sessions, series
    if not data_only:
        print("Technical Analysis Agent: saving daily indicators and signals", flush=True)
        add_benchmark(rows_by_isin, closes)
        for listing_report in coverage:
            isin = listing_report["isin"]
            rows = rows_by_isin.get(isin)
            if rows is None:
                latest.append(listing_report)
                continue
            first = listing_report["first_observed_session"] or max(rows)
            with db:
                db.executemany("""INSERT INTO daily_analysis VALUES (?, ?, ?, ?)
                                  ON CONFLICT(series, day, rule_version) DO UPDATE
                                  SET payload=excluded.payload WHERE payload <> excluded.payload""",
                               ((series_ids[isin], day, RULE_VERSION, encode(row))
                                for day, row in rows.items() if day >= first))
            row = rows[listing_report["expected_session"]]
            last_risk = next((day for day in reversed(rows) if rows[day]["risk_status"] == "valid"), None)
            latest.append({**listing_report, **row, "last_valid_risk_session": last_risk,
                           "rule_version": RULE_VERSION})
    report_day = max((row["expected_session"] for row in coverage if "expected_session" in row),
                     default=end.isoformat())
    pending = publish(db, directory, report_day, coverage, latest, model_name, interpret, data_only)
    print(f"Coverage: {directory / 'coverage.csv'}", flush=True)
    if not data_only:
        print(f"Latest report: {directory / 'latest.md'}", flush=True)
    if pending:
        print("Gemini report pending. Local data and analysis were saved.", file=sys.stderr)
    if not any(row.get("last_valid_session") for row in coverage):
        return 2
    return int(pending)
