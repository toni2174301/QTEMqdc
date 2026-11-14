# QDC Agents v1114

The Data Agent updates market data for 50 instruments. The Technical Analysis Agent reads that data from the same local SQLite database, computes indicators and signals, and produces an English report. Each local run catches up through the last completed trading session.

## Run locally

Use Python 3.11 or later on macOS or Linux. From the repository root:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp -n v1114/local_config.example.py v1114/local_config.py
```

Enter your Gemini API key in `v1114/local_config.py` once, or set the `GEMINI_API_KEY` environment variable. Then run:

```sh
.venv/bin/python -m v1114
```

Use `--as-of YYYY-MM-DD` to set a cutoff, `--model MODEL` to select a Gemini model, or `--data-only` to update quotes and export the coverage CSV. The first run builds the local database and requests up to ten years of available history.

## Workflow and outputs

| Stage | Inputs | Outputs |
| --- | --- | --- |
| Data Agent | Instrument list, verified listings, Yahoo Finance daily quotes, local history and verified corrections | Daily market data in `v1114/data/qdc.sqlite3` and coverage by instrument in `v1114/reports/coverage.csv` |
| Technical Analysis Agent | Market data from the same SQLite database, verified corrections and available FX rates | Daily indicator and signal history in SQLite, current details in `v1114/reports/latest.csv`, and an English report in `v1114/reports/latest.md` with a dated archive |

Unverified price gaps stay blank. For currency conversion, platform rates take priority and ECB rates serve as a fallback. Use `--platform-fx PATH` or place a CSV at `v1114/data/platform_fx.csv` with the headers `date,currency,units_per_eur,available_at`. The availability timestamp must include a time zone. If the ECB is closed or has yet to publish that day's rate, the prior rate can carry forward for up to four calendar days.

The technical indicators include 20-, 126-, and 252-session SMA and EMA, 14-session RSI, ATR and ADX, and 20-session close breakouts. VaR, CVaR, skewness and excess kurtosis require at least 240 valid EUR daily returns in the last 252 expected trading sessions and a valid return for the latest session. Python calculates the figures and counts the signals. Gemini writes the English commentary. If Gemini cannot provide commentary, the report marks it as pending and the CSV and SQLite results are still saved.

Local credentials, SQLite data and generated reports are excluded by `.gitignore`. A backup quote provider remains an open decision. The release includes individually verified corrections with their sources.
