"""Durable storage for raw observations, revisions, and daily analysis."""
import json
import sqlite3
from datetime import datetime, timezone


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def utc_stamp():
    return datetime.now(timezone.utc).isoformat()


def open_store(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS instruments (
            isin TEXT PRIMARY KEY, metadata TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS quotes (
            series TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL,
            fetched_at TEXT NOT NULL, PRIMARY KEY (series, day));
        CREATE TABLE IF NOT EXISTS quote_revisions (
            series TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL,
            fetched_at TEXT NOT NULL, replaced_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS daily_analysis (
            series TEXT NOT NULL, day TEXT NOT NULL, rule_version INTEGER NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY (series, day, rule_version));
        CREATE TABLE IF NOT EXISTS fx_ecb (
            day TEXT NOT NULL, currency TEXT NOT NULL, rate REAL NOT NULL,
            PRIMARY KEY (day, currency));
        CREATE TABLE IF NOT EXISTS interpretations (
            fingerprint TEXT PRIMARY KEY, report TEXT NOT NULL);
    """)
    return db


def read_quotes(db, series, start, end):
    result = {}
    for day, payload in db.execute(
        "SELECT day, payload FROM quotes WHERE series=? AND day BETWEEN ? AND ? ORDER BY day",
        (series, start, end)
    ):
        result[day] = json.loads(payload)
    return result


def save_quotes(db, series, changes):
    stamp = utc_stamp()
    for day, rows in changes.items():
        values = [{key: value if value is not None and isinstance(value, (int, float))
                             and abs(value) < float("inf") else None
                   for key, value in row.items() if key != "date"} for row in rows]
        payload = encode(values)
        prior = db.execute("SELECT payload, fetched_at FROM quotes WHERE series=? AND day=?",
                           (series, day)).fetchone()
        if prior and prior[0] == payload:
            continue
        if prior:
            db.execute("INSERT INTO quote_revisions VALUES (?, ?, ?, ?, ?)",
                       (series, day, *prior, stamp))
        db.execute("""INSERT INTO quotes VALUES (?, ?, ?, ?)
                      ON CONFLICT(series, day) DO UPDATE SET
                      payload=excluded.payload, fetched_at=excluded.fetched_at""",
                   (series, day, payload, stamp))
