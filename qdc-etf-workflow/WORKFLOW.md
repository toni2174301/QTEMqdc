# QDC ETF Workflow Map

```mermaid
flowchart TD
    RESEARCH["1. Market data and performance<br/>Review coverage, returns, risk, and correlations"]
    BACKTEST["2. Strategy comparison and backtest<br/>Compare candidate rules on the same historical windows"]

    subgraph LIVE["Live competition · Repeat for each round"]
        PROPOSE["3. Trading notebook · Sections 1–2<br/>Use quotes, RULE, and the opening account to propose orders"]
        PROPOSE -->|"Proposed order list"| REVIEW["Manual confirmation 1 · Review orders<br/>Check ticker, ISIN, side, and share count<br/>Set CONFIRM_APPROVAL = True in section 3"]
        REVIEW -->|"Approved order list"| SUBMIT["Manually submit orders on the competition platform"]
        SUBMIT -->|"Platform fill report"| FILLS["Manual confirmation 2 · Verify fills<br/>Check filled shares and average price for each order<br/>Set CONFIRM_FILL_REPORT = True in section 4"]
        FILLS -->|"Confirmed fills"| LEDGER["Update cash and holdings in the trading ledger"]
        LEDGER -->|"Opening cash and holdings for the next round"| PROPOSE
    end

    RESEARCH -->|"Historical price CSV and candidate rules"| BACKTEST
    RESEARCH -->|"Latest complete quotes available before the trade date"| PROPOSE
    BACKTEST -->|"Team selects settings and copies them into RULE"| PROPOSE

    classDef manual stroke-width:2px,stroke-dasharray:5 3
    class REVIEW,SUBMIT,FILLS manual
```

Run the [market data notebook](qdc-etf-data-refresh.ipynb), then the [backtest](qdc-etf-50-backtest.ipynb). Enter the chosen rule in the [trading notebook](qdc-etf-trading-workflow.ipynb) and rehearse before switching to `MODE = "live"`. Keep the same `qdc-trading-ledger.sqlite3` for every live round. See the [README](README.md) for setup and the full live-round checklist.
