# QDC Technical Report · 2026-11-13

## Signal distribution (from the CSV)

**50** instruments.

| Signal | Count |
| --- | ---: |
| Downside breakout | 3 |
| Not assessable | 28 |
| Reversal warning | 2 |
| Trend continuation | 1 |
| Upside breakout | 6 |
| Watch | 10 |

## Gemini commentary

### Data Integrity & Quality Issues

Across the evaluated universe, all instruments reflect the expected session date of **2026-11-13**. However, two specific instruments exhibit data quality flags or missing indicators:

*   **Amundi US Treasury Bond Long Dated UCITS ETF (Dist) (`DJAD.DE`)**: Flagged with a `warning` quality status due to multiple data issues, including invalid OHLC data, missing or negative volume, and verified exchange-close-only pricing. Consequently, its 14-period Average Directional Index (ADX) and Average True Range (ATR) percentage are unavailable.
*   **Missing ADX Metrics**: Moving-average and directionality indicators could not compute an ADX value for **Amundi BEL 20 UCITS ETF Dist (`BEL.BR`)**, **Amundi Euro Stoxx Banks UCITS ETF Acc (`LYBK.DE`)**, leveraged product **WisdomTree Copper 3x Daily Leveraged (`3HCL.MI`)**, and leveraged product **WisdomTree Palladium 2x Daily Leveraged (`2PAL.MI`)**. 

---

### Up to Five Primary Changes to Watch

#### 1. Upside Breakout: Amundi IBEX 35 UCITS ETF Acc (`AMES.DE`)
*   **Signal**: Upside breakout
*   **Trigger**: Close exceeds the highest close of the previous 20 sessions.
*   **Invalidation Level**: Close returns to or falls below 520.1.
*   **Technical Context**: The ETF trades in a Rangebound structure with an ADX of 25.26 and an RSI of 63.97. It delivered a 20-day relative return of 4.11 percentage points versus EUR cash, paired with a 1-day Value at Risk (VaR 95%) of 1.61%.

#### 2. Overbought Upside Momentum: iShares Edge MSCI World Minimum Volatility UCITS ETF USD (Acc) (`IQQ0.DE`)
*   **Signal**: Upside breakout
*   **Trigger**: Close exceeds the highest close of the previous 20 sessions.
*   **Invalidation Level**: Close returns to or falls below 73.21.
*   **Technical Context**: Demonstrating strong momentum, the fund shows an elevated ADX of 42.11 and an RSI of 76.93. Its 20-day relative return stands at 4.96 percentage points with a low daily VaR 95% of 0.67%.

#### 3. Moving Average Bearish Crossover: iShares MSCI Brazil UCITS ETF USD (Dist) (`IQQB.DE`)
*   **Signal**: Reversal warning
*   **Trigger**: SMA20 crosses below SMA126.
*   **Invalidation Level**: The two moving averages return to their prior order.
*   **Technical Context**: The fund exhibits an ADX of 41.70 and an RSI of 36.80, accompanied by significant underperformance (-21.07 percentage points over 20 sessions). Its 1-day VaR 95% is 2.54% with a CVaR 95% of 4.38%.

#### 4. Leveraged Downside Breakout: WisdomTree Copper 3x Daily Leveraged (`3HCL.MI`)
*   **Leveraged Product**: 3x Daily Leveraged
*   **Signal**: Downside breakout
*   **Trigger**: Close falls below the lowest close of the previous 20 sessions.
*   **Invalidation Level**: Close returns to or rises above 13.612.
*   **Technical Context**: The product has experienced a 20-day relative return of -20.19 percentage points. Its RSI is 33.57 and daily volatility metrics are elevated, showing an ATR percentage of 5.95%, a VaR 95% of 7.41%, and a CVaR 95% of 9.34%.

#### 5. Commodity ETP Downside Breakout: iShares Bitcoin ETP (`IB1T.DE`)
*   **Signal**: Downside breakout
*   **Trigger**: Close falls below the lowest close of the previous 20 sessions.
*   **Invalidation Level**: Close returns to or rises above 6.468.
*   **Technical Context**: The instrument reached a fresh 20-session low with an RSI of 30.42 and an ADX of 23.25. It records a 20-day relative return of -14.13 percentage points, supported by a 1-day VaR 95% of 4.05% and a CVaR 95% of 5.20%.

---

### Unassessable Instruments

A substantial portion of the monitored portfolio is flagged as **Not assessable** due to insufficient historical price action required to establish a clear trend, breakout, or moving-average crossover. These include:

*   **Broad Market & Sector Equity ETFs**: `EUNL.DE`, `SXR8.DE`, `LYP6.DE`, `SXRT.DE`, `IUSN.DE`, `JEQP.DE`, `SEC0.DE`, `NBTK.DE`, `2B76.DE`, `JEDI.DE`, `QUTM.DE`, `LYBK.DE`, `XDWH.DE`, `IS3N.DE`, `QDV5.DE`, `LKOR.DE`, `36BZ.DE`, `XDW0.DE`, `IS0D.DE`, `NUKL.DE`, `RENW.DE`, `AMEE.DE`, `VVMX.DE`, `DFEN.DE`.
*   **Commodities & Thematic Holdings**: `PPFB.DE`, `SLVR.DE`.
*   **Leveraged Products**: Leveraged product **WisdomTree Palladium 2x Daily Leveraged (`2PAL.MI`)** and leveraged product **WisdomTree NASDAQ 100 3x Daily Leveraged (`QQQ3.MI`)**.
