# QDC ETF Workflow

Work through market research, strategy comparison, and trading rehearsal in that order. The notebooks share the same market data format. The backtest and trading workflow also share the code for selecting instruments and calculating order sizes.

| Order | Notebook | What carries forward |
| --- | --- | --- |
| 1 | [Market data and performance](qdc-etf-data-refresh.ipynb) | Check data coverage and compare returns, volatility, drawdowns, and correlations across the 50 instruments. Use the charts to propose rules for the backtest. |
| 2 | [Strategy comparison and backtest](qdc-etf-50-backtest.ipynb) | Use section 5 to compare rebalancing with buy and hold. In section 9, add candidates to `RULES` and compare them on the same historical windows. Copy the selected settings into `RULE` in the trading workflow. |
| 3 | [Trading workflow](qdc-etf-trading-workflow.ipynb) | Generate orders for one round, review them, and record confirmed fills. The next round reads cash and holdings from the same ledger. |

[View the workflow map](WORKFLOW.md) for the notebook handoffs and the two manual confirmations.

## Setup

1. Open a terminal in this folder and use Python 3.10 or later.
2. Put an authorized copy of the team's market history CSV beside the notebooks. Use `qdc-50-daily-eur.csv` for a base snapshot or `qdc-50-daily-eur-updated.csv` for an updated snapshot. The notebooks prefer the updated file when both exist. The [instrument mapping](qdc-50-universe.csv) is included. Keep price files and the trading ledger local.
3. Install the dependencies and launch JupyterLab from this folder.

   ```sh
   python -m pip install -r requirements.txt
   python -m jupyterlab
   ```

The history CSV uses one row per ticker and trading date. Use the tickers in the instrument mapping, EUR prices, ISO dates, `0` for no dividend, and `1` for no split. Keep the headers in this order.

```csv
date,ticker,close_eur,dividend_eur,split_multiplier,provider_close_eur,close_adjustment
```

## Run the notebooks

1. In the market data notebook, choose **Run All** to analyze the local snapshot. Check the latest complete quote date and the data-quality report if present. `REFRESH_FROM_YAHOO` is off by default. Enable it only if you have authorization to use the automated data feed. [Yahoo Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html)
2. In backtest section 9, adjust `signal_sessions`, `core_weight`, and `cash_buffer` in `RULES` to compare candidate lookbacks, allocations, and cash reserves. Use the summary and individual windows to review both return and drawdown.
3. In the trading notebook, keep `MODE = "rehearsal"` for the default walk-through. It uses the team's original October 19, 2026 snapshot and preset review and fills. Use that snapshot for this rehearsal. The next round shows how cash and holdings carry over.

## Each live round

1. In the trading notebook, set `MODE = "live"`, enter the chosen `RULE`, and set `TRADE_DATE` and `EXPECTED_SIGNAL_DATE`. The signal date is the latest close expected to be available before the trade date. Run sections 1 and 2 to generate proposed orders.
2. In section 3, review the ticker, ISIN, side, and share count. Fill in `APPROVED_ORDERS`, `REVIEW_NOTE`, and `APPROVAL_FOR_DATE`, then set `CONFIRM_APPROVAL = True` and run the cell. Use positive shares for buys and negative shares for sells. Submit the approved list manually on the competition platform.
3. In section 4, enter the actual signed shares and average EUR price in `FILL_REPORT`. Use `(0, None)` for an unfilled order. Enter `FILL_REPORT_FOR_DATE`, set `CONFIRM_FILL_REPORT = True`, and run the cell after checking the report.
4. Keep `qdc-trading-ledger.sqlite3` in this folder for the next round. Update the trade and signal dates and reset both confirmation switches to `False`.
