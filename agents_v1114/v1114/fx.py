"""Point-in-time selection of EUR reference rates."""
import csv
import math
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateutil.easter import easter

from .market import fetch

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
ECB_ZONE = ZoneInfo("Europe/Brussels")


def target_open(day):
    return (day.weekday() < 5
            and (day.month, day.day) not in {(1, 1), (5, 1), (12, 25), (12, 26)}
            and day not in {easter(day.year) - timedelta(days=2),
                            easter(day.year) + timedelta(days=1)})


def load_ecb():
    root = ET.fromstring(fetch(ECB_URL))
    return {(date.fromisoformat(day.attrib["time"]), rate.attrib["currency"]): float(rate.attrib["rate"])
            for day in root.iter() if "time" in day.attrib
            for rate in day if {"currency", "rate"} <= rate.attrib.keys()}


def load_platform(path):
    if path is None:
        return {}
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            day, currency = date.fromisoformat(row["date"]), row["currency"].upper()
            rate = float(row["units_per_eur"])
            published = datetime.fromisoformat(row["available_at"])
            if (not math.isfinite(rate) or rate <= 0 or published.utcoffset() is None
                    or (day, currency) in result):
                raise ValueError("Invalid or duplicated platform FX row")
            result[day, currency] = rate, published
    return result


def select_rate(day, currency, cutoff, platform, ecb):
    if currency == "EUR":
        return 1.0, day.isoformat(), "native_eur", False
    currency = "GBP" if currency == "GBX" else currency
    entry = platform.get((day, currency))
    if entry and entry[1] <= cutoff:
        return entry[0], day.isoformat(), "platform", False
    rate_day = day
    while (day - rate_day).days <= 4:
        release = datetime.combine(rate_day, time(16), ECB_ZONE)
        if target_open(rate_day) and release <= cutoff:
            rate = ecb.get((rate_day, currency))
            if rate and math.isfinite(rate) and rate > 0:
                return rate, rate_day.isoformat(), "ecb_research", rate_day != day
            # A missing expected fixing is a gap, not a holiday.
            return None, None, "ecb_missing", False
        rate_day -= timedelta(days=1)
    return None, None, "ecb_missing", False
