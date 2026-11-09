"""Shared QDC decision rule and manually confirmed trading ledger."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import json
from numbers import Integral
import sqlite3

import pandas as pd

from update_qdc_prices import history_path


MAX_ORDERS = 5
STARTING_CASH = "10000"


def load_market(data_dir):
    assets = pd.read_csv(data_dir / "qdc-50-universe.csv").set_index("ticker")
    quotes = pd.read_csv(history_path(data_dir), parse_dates=["date"])
    if assets["isin"].nunique() != 50 or len(assets) != 50 or not assets.index.is_unique:
        raise ValueError("Expected 50 distinct instrument identifiers and tickers")
    if not assets["currency"].eq("EUR").all() or set(quotes["ticker"]) != set(assets.index):
        raise ValueError("Price history must match the official EUR universe")
    all_closes = quotes.pivot(index="date", columns="ticker", values="close_eur")
    close = all_closes.dropna().reindex(columns=assets.index)
    dividend = quotes.pivot(index="date", columns="ticker", values="dividend_eur").reindex_like(close)
    split = quotes.pivot(index="date", columns="ticker", values="split_multiplier").reindex_like(close)
    if dividend.isna().any().any() or split.isna().any().any():
        raise ValueError("Corporate action fields are missing for a complete close")
    return assets, all_closes, close, dividend, split


def market_metrics(price_history, dividend_history, split_history):
    growth = ((price_history + dividend_history) * split_history / price_history.shift()).iloc[1:]
    wealth = growth.cumprod()
    peak = wealth.cummax().clip(lower=1)
    return pd.DataFrame({
        "累计收益": wealth.iloc[-1] - 1,
        "年化波动率": (growth - 1).std() * 252 ** 0.5,
        "最大回撤": (1 - wealth / peak).max(),
    })


def plan_round(day, holdings, cash, *, close, dividend, split, equity_tickers,
               core_weight=0.70, cash_buffer=0.01, signal_sessions=5):
    if not 0 < core_weight < 1 or not 0 <= cash_buffer < 1 or signal_sessions < 2:
        raise ValueError("Invalid rule settings")
    history = close.loc[close.index < day].tail(max(5, signal_sessions) + 1)
    if len(history) < max(5, signal_sessions) + 1:
        raise ValueError("Not enough complete closes before the decision date")
    core_history = history.tail(signal_sessions + 1)
    core_metrics = market_metrics(core_history, dividend.loc[core_history.index], split.loc[core_history.index])
    core = core_metrics.loc[equity_tickers, "累计收益"].idxmax()
    partner_history = history.tail(6)
    partner_metrics = market_metrics(
        partner_history, dividend.loc[partner_history.index], split.loc[partner_history.index]
    )
    partner = partner_metrics.drop(index=core)["年化波动率"].idxmin()

    reference = history.iloc[-1]
    portfolio_value = cash + (holdings * reference).sum()
    weights = pd.Series(0.0, index=close.columns)
    weights.loc[[core, partner]] = [core_weight, round(1 - core_weight, 12)]
    target = (portfolio_value * (1 - cash_buffer) * weights // reference).astype(int)
    delta = target - holdings
    orders = pd.DataFrame({
        "delta": delta,
        "reference": reference,
        "reference_amount": delta.abs() * reference,
    }).loc[delta.ne(0)]
    orders["is_buy"] = orders["delta"].gt(0)
    orders = orders.sort_values(
        ["is_buy", "reference_amount"], ascending=[True, False], kind="stable"
    ).head(MAX_ORDERS)
    return history.index[-1], core, partner, target, orders


def _amount(value):
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, ValueError) as exc:
        raise ValueError(f"Invalid monetary amount: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError("Monetary amounts must be finite")
    return amount


def _units(value):
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError("Share quantities must be integers")
    return int(value)


@contextmanager
def _ledger(path, *, write=False):
    db = sqlite3.connect(path)
    try:
        if write:
            db.execute("BEGIN IMMEDIATE")
        db.execute("""
            CREATE TABLE IF NOT EXISTS rounds (
                trade_date TEXT PRIMARY KEY,
                approval TEXT NOT NULL,
                settlement TEXT
            )
        """)
        yield db
        db.commit()
    finally:
        db.close()


def _get_round(db, trade_date):
    row = db.execute("SELECT approval, settlement FROM rounds WHERE trade_date = ?", (trade_date,)).fetchone()
    if row is None:
        return None, None
    return json.loads(row[0]), json.loads(row[1]) if row[1] is not None else None


def _opening_account(db, trade_date):
    previous = db.execute("""
        SELECT trade_date, settlement FROM rounds
        WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1
    """, (trade_date,)).fetchone()
    if previous is None:
        return {"cash": STARTING_CASH, "holdings": {}}
    if previous[1] is None:
        raise ValueError(f"Settle the approved round for {previous[0]} first")
    return json.loads(previous[1])["account_after"]


def read_round(path, trade_date):
    date.fromisoformat(trade_date)
    with _ledger(path) as db:
        approval, settlement = _get_round(db, trade_date)
        opening = approval["account_before"] if approval is not None else _opening_account(db, trade_date)
        return opening, approval, settlement


def approve_round(path, trade_date, opening, signal_date, rule, suggested, approved, references, note):
    """Validate and freeze the reviewed order list for manual submission."""
    if date.fromisoformat(signal_date) >= date.fromisoformat(trade_date):
        raise ValueError("The signal close must precede the trade date")
    if not note.strip():
        raise ValueError("Record the review decision before freezing the orders")
    if len(approved) > MAX_ORDERS:
        raise ValueError(f"At most {MAX_ORDERS} orders per round")

    signed = {}
    cash = _amount(opening["cash"])
    for ticker, quantity in approved.items():
        quantity = _units(quantity)
        if ticker not in references or quantity == 0:
            raise ValueError(f"Unknown ticker or zero order: {ticker}")
        if opening["holdings"].get(ticker, 0) + quantity < 0:
            raise ValueError(f"Sell order exceeds holdings: {ticker}")
        signed[ticker] = quantity
        cash -= quantity * _amount(references[ticker])
    if cash < 0:
        raise ValueError("Orders exceed cash at the reference prices")

    record = {
        "trade_date": trade_date,
        "signal_date": signal_date,
        "rule": rule,
        "account_before": opening,
        "suggested": {ticker: _units(quantity) for ticker, quantity in suggested.items()},
        "approved": signed,
        "reference_prices": {ticker: str(references[ticker]) for ticker in signed},
        "review_note": note.strip(),
    }
    with _ledger(path, write=True) as db:
        existing, _ = _get_round(db, trade_date)
        if existing is not None:
            if existing != record:
                raise ValueError("An approved order list is already recorded for this date")
            return existing
        if db.execute("SELECT 1 FROM rounds WHERE trade_date > ?", (trade_date,)).fetchone():
            raise ValueError("An earlier round cannot be added after a later approval")
        if opening != _opening_account(db, trade_date):
            raise ValueError("The opening account changed. Reload it before approval")
        db.execute("INSERT INTO rounds (trade_date, approval) VALUES (?, ?)",
                   (trade_date, json.dumps(record, ensure_ascii=False)))
    return record


def settle_round(path, trade_date, confirmations, *, cash_adjustment="0", share_adjustments=None):
    """Apply a complete fill report once, including optional confirmed account adjustments."""
    share_adjustments = {ticker: _units(units) for ticker, units in (share_adjustments or {}).items()}
    with _ledger(path, write=True) as db:
        approval, existing = _get_round(db, trade_date)
        if approval is None:
            raise ValueError("Freeze the manually reviewed orders before settlement")
        approved = approval["approved"]
        if set(confirmations) != set(approved):
            raise ValueError("Record a fill or a zero fill for every approved order")

        cash = _amount(approval["account_before"]["cash"]) + _amount(cash_adjustment)
        holdings = approval["account_before"]["holdings"].copy()
        for ticker, change in share_adjustments.items():
            if ticker not in holdings or holdings[ticker] + change < 0:
                raise ValueError(f"Invalid confirmed share adjustment: {ticker}")
            holdings[ticker] += change

        filled = {}
        for ticker, (quantity, raw_price) in confirmations.items():
            quantity = _units(quantity)
            requested = approved[ticker]
            if quantity * requested < 0 or abs(quantity) > abs(requested):
                raise ValueError(f"Fill exceeds or reverses the approved order: {ticker}")
            price = _amount(raw_price) if quantity else None
            if (quantity and price <= 0) or (quantity == 0 and raw_price is not None):
                raise ValueError(f"Invalid execution price for {ticker}")
            holdings[ticker] = holdings.get(ticker, 0) + quantity
            if holdings[ticker] < 0:
                raise ValueError(f"Confirmed sell exceeds adjusted holdings: {ticker}")
            if price is not None:
                cash -= quantity * price
            filled[ticker] = [quantity, str(price.normalize()) if price is not None else None]

        if cash < 0:
            raise ValueError("Confirmed fills produce negative cash. Check the report and adjustments")
        record = {
            "fills": filled,
            "cash_adjustment": str(_amount(cash_adjustment).normalize()),
            "share_adjustments": share_adjustments,
            "account_after": {"cash": str(cash), "holdings": {t: q for t, q in holdings.items() if q}},
        }
        if existing is not None:
            if existing != record:
                raise ValueError("This round was already settled with a different confirmation")
            return existing
        db.execute("UPDATE rounds SET settlement = ? WHERE trade_date = ?",
                   (json.dumps(record, ensure_ascii=False), trade_date))
    return record
