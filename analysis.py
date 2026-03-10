"""
Pure calculation functions — no network calls, no Streamlit imports.
All functions accept data already fetched from data_fetcher.py.
"""

import pandas as pd
import numpy as np
from typing import Optional

from config import SMA_STRETCH_THRESHOLD, REFERENCE_PE


# ── P&L ──────────────────────────────────────────────────────────────────────

def calc_holding_pnl(holding: dict, current_price: float) -> dict:
    """
    Returns cost_basis, current_value, gain_loss_clp, gain_loss_pct
    for one lot.
    """
    qty       = holding["quantity"]
    buy_price = holding["buy_price"]

    cost_basis    = qty * buy_price
    current_value = qty * current_price
    gain_loss_clp = current_value - cost_basis
    gain_loss_pct = ((current_price - buy_price) / buy_price) * 100 if buy_price else 0.0

    return {
        "cost_basis":    cost_basis,
        "current_value": current_value,
        "gain_loss_clp": gain_loss_clp,
        "gain_loss_pct": gain_loss_pct,
    }


def calc_alpha(stock_return_pct: float, ipsa_return_pct: Optional[float]) -> Optional[float]:
    """Relative alpha = stock return minus IPSA return over the same period."""
    if ipsa_return_pct is None:
        return None
    return stock_return_pct - ipsa_return_pct


# ── Portfolio summary ─────────────────────────────────────────────────────────

def calc_portfolio_summary(holdings: list, prices: dict, cash: float) -> dict:
    """
    Aggregates holdings into:
      total_nav, total_equity_value, total_cost_basis,
      cash, equity_pct, cash_pct, total_pnl, total_pnl_pct
    """
    total_equity  = 0.0
    total_cost    = 0.0

    for h in holdings:
        pd_data = prices.get(h["ticker"], {})
        price   = pd_data.get("price")
        if price:
            total_equity += h["quantity"] * price
            total_cost   += h["quantity"] * h["buy_price"]

    total_nav  = total_equity + cash
    eq_pct     = (total_equity / total_nav * 100) if total_nav > 0 else 0.0
    cash_pct   = (cash / total_nav * 100)         if total_nav > 0 else 0.0
    total_pnl  = total_equity - total_cost
    pnl_pct    = (total_pnl / total_cost * 100)   if total_cost  > 0 else 0.0

    return {
        "total_nav":        total_nav,
        "total_equity_value": total_equity,
        "total_cost_basis": total_cost,
        "cash":             cash,
        "equity_pct":       eq_pct,
        "cash_pct":         cash_pct,
        "total_pnl":        total_pnl,
        "total_pnl_pct":    pnl_pct,
    }


# ── Technical analysis ────────────────────────────────────────────────────────

def calc_sma_signals(hist: pd.DataFrame) -> dict:
    """
    Computes 50-SMA, 200-SMA, Golden/Death cross, stretch from 200-SMA.
    Signal values:
        GOLDEN_CROSS | DEATH_CROSS | BULLISH | BEARISH |
        OVERBOUGHT_EXTREME | OVERSOLD_EXTREME
    Returns dict with all values; None where insufficient data.
    """
    result = {
        "sma50":       None,
        "sma200":      None,
        "signal":      None,
        "stretch_pct": None,
        "current":     None,
    }

    if hist.empty or len(hist) < 50:
        return result

    closes = hist["Close"]
    current = float(closes.iloc[-1])
    result["current"] = current

    sma50 = float(closes.rolling(50).mean().iloc[-1])
    result["sma50"] = sma50

    if len(closes) >= 200:
        sma200 = float(closes.rolling(200).mean().iloc[-1])
        result["sma200"] = sma200

        stretch_pct = ((current - sma200) / sma200) * 100
        result["stretch_pct"] = stretch_pct

        # Detect cross on last two bars
        if len(closes) >= 201:
            prev_sma50  = float(closes.rolling(50).mean().iloc[-2])
            prev_sma200 = float(closes.rolling(200).mean().iloc[-2])

            if prev_sma50 < prev_sma200 and sma50 >= sma200:
                signal = "GOLDEN_CROSS"
            elif prev_sma50 > prev_sma200 and sma50 <= sma200:
                signal = "DEATH_CROSS"
            elif abs(stretch_pct) > SMA_STRETCH_THRESHOLD:
                signal = "OVERBOUGHT_EXTREME" if stretch_pct > 0 else "OVERSOLD_EXTREME"
            elif sma50 > sma200:
                signal = "BULLISH"
            else:
                signal = "BEARISH"
        else:
            if abs(stretch_pct) > SMA_STRETCH_THRESHOLD:
                signal = "OVERBOUGHT_EXTREME" if stretch_pct > 0 else "OVERSOLD_EXTREME"
            elif sma50 > sma200:
                signal = "BULLISH"
            else:
                signal = "BEARISH"

        result["signal"] = signal

    return result


def calc_support_resistance(hist: pd.DataFrame, lookback: int = 60) -> dict:
    """
    Derives key S/R levels from recent price action.
    support  = 25th percentile of recent lows
    resistance = 75th percentile of recent highs
    Also returns period_low and period_high (absolute extremes).
    """
    if hist.empty or len(hist) < 10:
        return {"support": None, "resistance": None, "period_low": None, "period_high": None}

    recent = hist.tail(lookback)
    return {
        "support":     float(recent["Low"].quantile(0.25)),
        "resistance":  float(recent["High"].quantile(0.75)),
        "period_low":  float(recent["Low"].min()),
        "period_high": float(recent["High"].max()),
    }


def calc_rsi(hist: pd.DataFrame, period: int = 14) -> Optional[float]:
    """
    14-period RSI using Wilder's exponential smoothing.
    Returns None if insufficient data.
    """
    if hist.empty or len(hist) < period + 1:
        return None
    closes = hist["Close"]
    delta  = closes.diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs  = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if not np.isnan(val) else None


def calc_memory_zones(
    hist: pd.DataFrame,
    lookback: int = 126,
    tolerance: float = 0.02,
    min_touches: int = 2,
) -> dict:
    """
    Identifies horizontal price memory zones (support and resistance) where
    price has bounced at least min_touches times in the last ~6 months (126 bars).

    Uses swing high/low detection (5-bar wing) and clusters nearby levels
    within tolerance % of each other.

    Returns:
        support_zones:    list of {level, touches} sorted by touch count desc
        resistance_zones: list of {level, touches} sorted by touch count desc
    """
    result = {"support_zones": [], "resistance_zones": []}
    if hist.empty or len(hist) < 20:
        return result

    recent = hist.tail(lookback)
    highs  = recent["High"].values
    lows   = recent["Low"].values
    n      = len(recent)
    wing   = 5  # bars on each side to confirm a swing point

    swing_highs, swing_lows = [], []
    for i in range(wing, n - wing):
        if highs[i] == max(highs[i - wing: i + wing + 1]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i - wing: i + wing + 1]):
            swing_lows.append(lows[i])

    def cluster(levels):
        if not levels:
            return []
        sorted_lvls = sorted(levels)
        clusters, group = [], [sorted_lvls[0]]
        for lvl in sorted_lvls[1:]:
            if (lvl - group[0]) / group[0] <= tolerance:
                group.append(lvl)
            else:
                clusters.append(group)
                group = [lvl]
        clusters.append(group)
        return [(float(np.mean(g)), len(g)) for g in clusters]

    def touch_count(level, price_arr):
        return int(np.sum(np.abs(price_arr - level) / level <= tolerance))

    for level, _ in cluster(swing_lows):
        touches = touch_count(level, lows)
        if touches >= min_touches:
            result["support_zones"].append({"level": round(level, 1), "touches": touches})
    result["support_zones"].sort(key=lambda x: x["touches"], reverse=True)

    for level, _ in cluster(swing_highs):
        touches = touch_count(level, highs)
        if touches >= min_touches:
            result["resistance_zones"].append({"level": round(level, 1), "touches": touches})
    result["resistance_zones"].sort(key=lambda x: x["touches"], reverse=True)

    return result


def calc_volume_confirmation(hist: pd.DataFrame, window: int = 20) -> dict:
    """
    Checks if the most recent session's volume is >= 1.2x the prior 20-session average.
    Returns ratio, confirmed flag, and raw volumes.
    """
    if hist.empty or len(hist) < window + 1 or "Volume" not in hist.columns:
        return {"confirmed": False, "ratio": None, "current_vol": None, "avg_vol": None}

    vol         = hist["Volume"]
    current_vol = float(vol.iloc[-1])
    avg_vol     = float(vol.iloc[-(window + 1):-1].mean())

    if avg_vol == 0:
        return {"confirmed": False, "ratio": None, "current_vol": current_vol, "avg_vol": avg_vol}

    ratio = current_vol / avg_vol
    return {
        "confirmed":   ratio >= 1.2,
        "ratio":       round(ratio, 2),
        "current_vol": current_vol,
        "avg_vol":     avg_vol,
    }


def calc_buy_zone_alert(
    current_price: float,
    sma_data: dict,
    rsi: Optional[float],
    memory_zones: dict,
    price_tol: float = 0.03,
    sma_tol: float = 0.05,
) -> dict:
    """
    Issues a confluent BUY ZONE alert when multiple conditions align:
      1. Price within price_tol % of a memory support zone (>= 2 touches)
      2. Price within sma_tol % of the 200-day SMA
      3. RSI < 35 (oversold)

    Alert levels:
      STRONG   — all 3 conditions
      MODERATE — 2 conditions
      WATCH    — 1 condition
      None     — no conditions
    """
    conditions = []
    details    = {}

    # Condition 1 — near a memory support zone
    nearest_support = None
    for zone in memory_zones.get("support_zones", []):
        if abs(current_price - zone["level"]) / zone["level"] <= price_tol:
            nearest_support = zone
            break
    details["nearest_support"] = nearest_support
    if nearest_support:
        conditions.append("support_zone")

    # Condition 2 — near 200-SMA
    near_sma200 = False
    if sma_data.get("sma200"):
        near_sma200 = abs(current_price - sma_data["sma200"]) / sma_data["sma200"] <= sma_tol
    details["near_sma200"] = near_sma200
    if near_sma200:
        conditions.append("sma200")

    # Condition 3 — RSI oversold
    oversold = rsi is not None and rsi < 35
    details["rsi"]      = rsi
    details["oversold"] = oversold
    if oversold:
        conditions.append("rsi_oversold")

    n = len(conditions)
    alert_level = {3: "STRONG", 2: "MODERATE", 1: "WATCH"}.get(n)

    return {"alert_level": alert_level, "conditions": conditions, "details": details}


# ── Valuation ─────────────────────────────────────────────────────────────────

def estimate_valuation(info: dict) -> dict:
    """
    Attempts two valuation methods in order:
      1. P/E relative: fair_value = trailing_EPS * REFERENCE_PE
      2. P/B: fair_value = book_value_per_share * 1.0  (placeholder parity)
    Returns method name, fair_value, current_price, upside_pct.
    All values may be None if data is unavailable.
    """
    current_price = (
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose")
    )

    # Method 1 — P/E relative
    trailing_pe  = info.get("trailingPE")
    trailing_eps = info.get("trailingEps")

    if trailing_eps and trailing_eps > 0 and current_price:
        fair_value  = trailing_eps * REFERENCE_PE
        upside_pct  = ((fair_value - current_price) / current_price) * 100
        return {
            "method":        f"P/E Relative (ref {REFERENCE_PE}x)",
            "fair_value":    fair_value,
            "current_price": current_price,
            "upside_pct":    upside_pct,
            "trailing_pe":   trailing_pe,
        }

    # Method 2 — P/B parity
    book_per_share = info.get("bookValue")
    if book_per_share and book_per_share > 0 and current_price:
        fair_value = float(book_per_share)
        upside_pct = ((fair_value - current_price) / current_price) * 100
        return {
            "method":        "P/B Parity (1x book)",
            "fair_value":    fair_value,
            "current_price": current_price,
            "upside_pct":    upside_pct,
            "trailing_pe":   None,
        }

    return {
        "method":        "N/A",
        "fair_value":    None,
        "current_price": current_price,
        "upside_pct":    None,
        "trailing_pe":   trailing_pe,
    }


# ── Macro narrative helpers ───────────────────────────────────────────────────

def macro_narrative(macro_data: dict) -> list[str]:
    """
    Convert raw macro data into human-readable bullet points for the
    Morning Briefing. Returns a list of markdown strings.
    """
    lines = []

    copper = macro_data.get("Copper (USD/lb)", {})
    usdclp = macro_data.get("USD/CLP", {})
    spx    = macro_data.get("S&P 500", {})
    ipsa   = macro_data.get("IPSA", {})

    if copper.get("price") and copper.get("change_pct") is not None:
        c = copper["change_pct"]
        price = copper["price"]
        if c > 1.0:
            lines.append(
                f"**Copper +{c:.2f}% at ${price:.3f}/lb** — positive for mining sector "
                f"(CAP, COPEC) and CLP support."
            )
        elif c < -1.0:
            lines.append(
                f"**Copper {c:.2f}% at ${price:.3f}/lb** — headwind for mining and CLP."
            )
        else:
            lines.append(f"Copper flat at ${price:.3f}/lb ({c:+.2f}%) — neutral for miners.")

    if usdclp.get("price") and usdclp.get("change_pct") is not None:
        c = usdclp["change_pct"]
        price = usdclp["price"]
        if c > 0.5:
            lines.append(
                f"**USD/CLP +{c:.2f}% at {price:,.1f}** — peso weakness. Exporters benefit; "
                f"importers and dollar-debt companies face pressure."
            )
        elif c < -0.5:
            lines.append(
                f"**USD/CLP {c:.2f}% at {price:,.1f}** — peso strength. "
                f"Import costs fall; export revenues squeezed."
            )
        else:
            lines.append(f"USD/CLP stable at {price:,.1f} ({c:+.2f}%).")

    if spx.get("price") and spx.get("change_pct") is not None:
        c = spx["change_pct"]
        price = spx["price"]
        if c > 1.0:
            lines.append(
                f"**S&P 500 +{c:.2f}% at {price:,.0f}** — risk-on globally; "
                f"supportive of EM inflows including Chile."
            )
        elif c < -1.0:
            lines.append(
                f"**S&P 500 {c:.2f}% at {price:,.0f}** — risk-off; "
                f"watch for EM outflows and IPSA pressure."
            )
        else:
            lines.append(f"S&P 500 flat at {price:,.0f} ({c:+.2f}%) — muted global signal.")

    if ipsa.get("price") and ipsa.get("change_pct") is not None:
        c = ipsa["change_pct"]
        price = ipsa["price"]
        lines.append(f"**IPSA at {price:,.1f} ({c:+.2f}%)** — local benchmark reference.")
    elif ipsa.get("error"):
        lines.append(f"IPSA data unavailable ({ipsa['error']}).")

    return lines


SIGNAL_LABELS = {
    "GOLDEN_CROSS":       "Golden Cross (50-SMA crossed above 200-SMA) — strong bullish momentum",
    "DEATH_CROSS":        "Death Cross (50-SMA crossed below 200-SMA) — bearish structural signal",
    "BULLISH":            "Above 200-SMA — uptrend structure intact",
    "BEARISH":            "Below 200-SMA — downtrend structure",
    "OVERBOUGHT_EXTREME": f"> {SMA_STRETCH_THRESHOLD}% above 200-SMA — extended, risk of pullback",
    "OVERSOLD_EXTREME":   f"> {SMA_STRETCH_THRESHOLD}% below 200-SMA — oversold, watch for bounce",
}
