"""Daily calculations. Missing exchange sessions stay in the index."""
import math

import numpy as np
import pandas as pd

RISK_WINDOW = 252
MIN_RETURNS = 240
RULE_VERSION = 1


def tail_loss(values):
    losses = np.sort(-values.dropna().to_numpy())
    # Integrate the empirical quantile over the worst 5%, including a
    # fractional boundary observation when the sample size requires one.
    tail = len(losses) * .05
    whole = int(tail)
    total = losses[-whole:].sum() + (tail - whole) * losses[-whole - 1]
    return total / tail


def wilder(values, periods=14):
    result = pd.Series(np.nan, index=values.index, dtype=float)
    count = 0
    seed = 0.0
    for i, value in enumerate(values):
        if pd.isna(value):
            count, seed = 0, 0.0
            continue
        count += 1
        if count <= periods:
            seed += value
            if count == periods:
                result.iat[i] = seed / periods
        else:
            result.iat[i] = (result.iat[i - 1] * (periods - 1) + value) / periods
    return result


def risk_metrics(price):
    returns = price.pct_change(fill_method=None)
    window = returns.rolling(RISK_WINDOW, min_periods=MIN_RETURNS)
    samples = returns.rolling(RISK_WINDOW, min_periods=1).count().astype(int)
    ready = (samples >= MIN_RETURNS) & returns.notna()
    ready.iloc[:RISK_WINDOW] = False
    result = {
        "daily_return": returns,
        "log_return": np.log1p(returns),
        "risk_samples": samples,
        "risk_coverage": samples / RISK_WINDOW,
        "risk_status": np.select([returns.isna(), ready],
                                 ["latest_return_invalid", "valid"], default="insufficient_coverage"),
        "var_95_1d": (-returns).rolling(RISK_WINDOW, min_periods=MIN_RETURNS)
            .quantile(.95, interpolation="higher").where(ready),
        "cvar_95_1d": window.apply(tail_loss, raw=False).where(ready),
        "skewness_252": window.skew().where(ready),
        "excess_kurtosis_252": window.kurt().where(ready),
        "current_drawdown": price / price.cummax() - 1,
    }
    for n in (20, 126, 252):
        result[f"annualized_volatility_{n}d"] = returns.rolling(n).std() * math.sqrt(252)
        result[f"annualized_downside_{n}d"] = (
            returns.clip(upper=0).pow(2).rolling(n).mean().pow(.5) * math.sqrt(252)
        )
    return result


def period_metrics(price, dates):
    """Calendar endpoints and complete-window drawdown statistics."""
    result = {}
    index = pd.DatetimeIndex(dates)
    values = price.to_numpy()
    empty = np.full(len(index), np.nan)
    periods = {"1w": pd.DateOffset(weeks=1), "1m": pd.DateOffset(months=1),
               "3m": pd.DateOffset(months=3)}
    periods.update({f"{y}y": pd.DateOffset(years=y) for y in (1, 3, 5, 10)})
    valid_dates = index[price.notna()]
    if len(valid_dates) == 0:
        return result
    ytd = pd.DatetimeIndex([f"{day.year - 1}-12-31" for day in index])
    for label, boundaries in {**{key: index - offset for key, offset in periods.items()},
                              "ytd": ytd}.items():
        valid_starts = valid_dates.searchsorted(boundaries, side="right") - 1
        starts = np.where(valid_starts >= 0,
                          index.get_indexer(valid_dates[valid_starts.clip(min=0)]), -1)
        exists = starts >= 0
        safe = starts.clip(min=0)
        period_return = np.where(exists, values / values[safe] - 1, np.nan)
        result[f"return_{label}"] = period_return
        if not label.endswith("y"):
            continue
        duration = np.array((index - index[safe]).days) / 365.2425
        result[f"cagr_{label}"] = np.expm1(np.log1p(period_return) / np.where(duration > 0, duration, np.nan))
        vol, mdd, length, recovery_length = [empty.copy() for _ in range(4)]
        peak_dates, trough_dates, recovered_dates = [[None] * len(index) for _ in range(3)]
        for i, j in enumerate(starts):
            if j < 0 or i <= j:
                continue
            segment = values[j:i + 1]
            if not np.isfinite(segment).all():
                continue
            returns = segment[1:] / segment[:-1] - 1
            if len(returns) > 1:
                vol[i] = returns.std(ddof=1) * math.sqrt(252)
            drawdowns = segment / np.maximum.accumulate(segment) - 1
            trough = int(drawdowns.argmin())
            mdd[i] = drawdowns[trough]
            if mdd[i] >= 0:
                continue
            peak = int(np.flatnonzero(segment[:trough + 1] == segment[:trough + 1].max())[-1])
            peak_dates[i], trough_dates[i] = dates[j + peak], dates[j + trough]
            length[i] = trough - peak
            recovery = np.flatnonzero(segment[trough + 1:] >= segment[peak])
            if recovery.size:
                recovered = int(recovery[0]) + trough + 1
                recovered_dates[i] = dates[j + recovered]
                recovery_length[i] = recovered - trough
        for field, data in {"annualized_volatility": vol, "max_drawdown": mdd,
                            "drawdown_peak": peak_dates, "drawdown_trough": trough_dates,
                            "drawdown_recovered": recovered_dates, "drawdown_sessions": length,
                            "drawdown_recovery_sessions": recovery_length}.items():
            result[f"{field}_{label}"] = data
    return result


def technical_metrics(price, high, low):
    out = {}
    for n in (20, 60, 126, 252):
        out[f"sma{n}"] = price.rolling(n).mean()
    segments = price.isna().cumsum()
    for n in (20, 126, 252):
        out[f"ema{n}"] = price.groupby(segments).transform(
            lambda series: series.ewm(span=n, min_periods=n, adjust=False).mean()
        ).where(price.notna())

    prior = price.shift()
    change = price.diff()
    gain, loss = wilder(change.clip(lower=0)), wilder(-change.clip(upper=0))
    total = gain + loss
    out["rsi14"] = (100 * gain / total).mask(total.eq(0), 50)
    true_range = pd.concat([high - low, (high - prior).abs(), (low - prior).abs()], axis=1)
    true_range = true_range.max(axis=1, skipna=False)
    atr = wilder(true_range)
    out["atr14"] = atr
    out["atr_pct"] = atr / price
    percentile = out["atr_pct"].rolling(126, min_periods=100).rank(pct=True)
    out["volatility_state"] = np.select(
        [percentile.isna(), percentile <= .2, percentile >= .8],
        ["Insufficient history", "Low", "Elevated"], default="Normal")
    out["volatility_rising"] = (out["atr_pct"] > out["atr_pct"].shift(5)).where(
        out["atr_pct"].notna() & out["atr_pct"].shift(5).notna())
    up, down = high.diff(), -low.diff()
    has_range = up.notna() & down.notna() & prior.notna()
    pos = wilder(up.where((up > down) & (up > 0), 0).where(has_range))
    neg = wilder(down.where((down > up) & (down > 0), 0).where(has_range))
    pos_di, neg_di = (100 * pos / atr).mask(atr.eq(0), 0), (100 * neg / atr).mask(atr.eq(0), 0)
    out["plus_di14"], out["minus_di14"] = pos_di, neg_di
    out["adx14"] = wilder((100 * (pos_di - neg_di).abs() / (pos_di + neg_di))
                          .mask((pos_di + neg_di).eq(0), 0))
    sma20, sma60, sma126, sma252 = (out[f"sma{n}"] for n in (20, 60, 126, 252))
    spread = sma20 - sma126
    out["sma20_126_cross"] = np.select(
        [(spread > 0) & (spread.shift() <= 0), (spread < 0) & (spread.shift() >= 0)],
        ["crosses above", "crosses below"], default="")
    out["trend_strength"] = np.select(
        [out["adx14"].isna(), out["adx14"] < 20, out["adx14"] > 25],
        ["Insufficient history", "Weak", "Strong"], default="Moderate")
    momentum_shift = out["rsi14"] - out["rsi14"].shift(5)
    out["momentum_state"] = np.select(
        [momentum_shift.isna(), (out["rsi14"] >= 50) & (momentum_shift > 0),
         (out["rsi14"] < 50) & (momentum_shift < 0)],
        ["Insufficient history", "Upward momentum strengthens", "Downward momentum strengthens"], default="Momentum does not strengthen")
    bull = (price > sma20) & (sma20 > sma126) & (sma126 > sma252)
    bear = (price < sma20) & (sma20 < sma126) & (sma126 < sma252)
    slope = sma126.pct_change(5, fill_method=None)
    out["sma126_slope_5d"] = slope
    out["trend"] = np.select(
        [price.isna() | sma252.isna(), bull, bear, slope.abs() <= .005],
        ["Not assessable", "Uptrend", "Downtrend", "Rangebound"], default="Transitional")
    change20 = price.pct_change(20, fill_method=None)
    out["baseline_20_60"] = np.select(
        [price.rolling(61).count() < 61, price.isna() | sma60.isna(),
         (price > sma20) & (sma20 > sma60) & (change20 > 0),
         (price < sma20) & (sma20 < sma60) & (change20 < 0)],
        ["Not assessable", "Not assessable", "Bullish", "Bearish"], default="Neutral")
    out["prior_high_20d"] = price.shift().rolling(20).max()
    out["prior_low_20d"] = price.shift().rolling(20).min()
    out["return_20d_local"] = change20.where(price.rolling(21).count() == 21)
    return out


def classify(row):
    price = row["adjusted_close_local"]
    upper, lower = row["prior_high_20d"], row["prior_low_20d"]
    trend, cross, rsi, adx = (row[k] for k in ("trend", "sma20_126_cross", "rsi14", "adx14"))
    if price is None:
        return "Not assessable", "No usable adjusted close for the session", ""
    if upper is not None and price > upper:
        return "Upside breakout", "Close exceeds the highest close of the previous 20 sessions", f"Close returns to or falls below {upper:.6g}"
    if lower is not None and price < lower:
        return "Downside breakout", "Close falls below the lowest close of the previous 20 sessions", f"Close returns to or rises above {lower:.6g}"
    if cross:
        return "Reversal warning", f"SMA20 {cross} SMA126", "The two moving averages return to their prior order"
    if (trend == "Uptrend" and rsi is not None and rsi < 50
            or trend == "Downtrend" and rsi is not None and rsi > 50):
        return "Weakening momentum", "RSI is on the opposite side of 50 from the moving-average trend", "RSI returns to the side of 50 consistent with the trend"
    if adx is not None and adx > 25 and rsi is not None and (
            trend == "Uptrend" and rsi >= 50 or trend == "Downtrend" and rsi <= 50):
        return "Trend continuation", f"{trend} confirmed by moving averages, ADX above 25, and RSI alignment", "Moving-average alignment breaks or ADX drops to 25"
    if trend == "Not assessable":
        return "Not assessable", "Insufficient history to identify a trend, breakout, or crossover", ""
    return "Watch", "Current indicators do not trigger a signal", ""
