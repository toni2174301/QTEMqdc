"""Catch up QDC prices and analysis through the latest completed sessions."""
import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .gemini import AgentError, Gemini
from .pipeline import run

ROOT = Path(__file__).resolve().parent


def api_key():
    try:
        from .local_config import GEMINI_API_KEY
    except ModuleNotFoundError as exc:
        if exc.name != f"{__package__}.local_config":
            raise
        return os.getenv("GEMINI_API_KEY", "").strip()
    return (GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, help="Optional latest exchange date")
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"))
    parser.add_argument("--output", type=Path, help="Override the report directory")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "qdc.sqlite3")
    parser.add_argument("--data-only", action="store_true", help="Fetch quotes and produce the coverage CSV only")
    parser.add_argument("--full-history", action="store_true", help="Recheck older missing history")
    parser.add_argument("--platform-fx", type=Path, help="CSV with date,currency,units_per_eur,available_at")
    args = parser.parse_args()
    output = args.output or ROOT / "reports"
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo("Europe/Amsterdam")).date()
    if args.as_of and args.as_of > today:
        parser.error("--as-of cannot be a future date")
    key = api_key() if not args.data_only else ""
    if not key and not args.data_only:
        parser.error("Paste your Gemini API key into v1114/local_config.py once, then rerun.")

    def interpret(evidence):
        return Gemini(key, args.model).interpret(evidence)

    try:
        assets = json.loads((ROOT / "universe.json").read_text(encoding="utf-8"))
        listings = json.loads((ROOT / "listings.json").read_text(encoding="utf-8"))
        # Optional local review file. A confirmation applies only to the verified move.
        reviews = ROOT / "data" / "confirmed_moves.json"
        confirmed = {}
        if reviews.exists():
            for row in json.loads(reviews.read_text(encoding="utf-8")):
                value = float(row["adjusted_return"])
                if not math.isfinite(value):
                    raise ValueError("Invalid confirmed adjusted return")
                confirmed[row["symbol"], row["date"]] = value
        platform = args.platform_fx
        default_platform = ROOT / "data" / "platform_fx.csv"
        if platform is None and default_platform.exists():
            platform = default_platform
        return run(output, args.database, assets, listings, now, args.as_of or today,
                   args.model, interpret, platform_path=platform, confirmed_moves=confirmed,
                   full_history=args.full_history, data_only=args.data_only)
    except (AgentError, OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
