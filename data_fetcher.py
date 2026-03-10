"""
Yahoo Finance data fetching with Streamlit caching.
All functions that touch the network are decorated with @st.cache_data so repeated
calls within the same TTL window are served from cache.
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from datetime import datetime
from typing import Optional

from config import MACRO_TICKERS, IPSA_TICKER

FRED_API_KEY = "6cd1756ce64d643f595980392cf50bc1"
FRED_BASE    = "https://api.stlouisfed.org/fred/series/observations"


# ── Single ticker price ───────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def get_current_price(ticker: str) -> dict:
    """
    Returns:
        {price, prev_close, change_pct, volume, timestamp, error}
    Chilean .SN tickers are priced in CLP on Yahoo Finance.
    """
    try:
        t  = yf.Ticker(ticker)
        fi = t.fast_info

        current_price = fi.last_price
        prev_close    = fi.previous_close

        if current_price is None or prev_close is None or prev_close == 0:
            return {"price": None, "timestamp": None, "error": f"No data returned for {ticker}"}

        change_pct = ((current_price - prev_close) / prev_close) * 100

        return {
            "price":      float(current_price),
            "prev_close": float(prev_close),
            "change_pct": change_pct,
            "volume":     None,
            "timestamp":  datetime.now(),
            "error":      None,
        }
    except Exception as exc:
        return {"price": None, "timestamp": None, "error": str(exc)}


# ── Historical OHLCV ──────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def get_historical_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Return OHLCV DataFrame; empty DataFrame on failure."""
    try:
        return yf.Ticker(ticker).history(period=period)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def get_historical_data_since(ticker: str, start_date: str) -> pd.DataFrame:
    """Return OHLCV since a specific date string (YYYY-MM-DD)."""
    try:
        return yf.Ticker(ticker).history(start=start_date)
    except Exception:
        return pd.DataFrame()


# ── Fundamental info ──────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_info(ticker: str) -> dict:
    """Return yfinance .info dict; empty dict on failure."""
    try:
        return yf.Ticker(ticker).info
    except Exception:
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def get_dividends(ticker: str) -> pd.Series:
    """Return dividend history Series; empty Series on failure."""
    try:
        return yf.Ticker(ticker).dividends
    except Exception:
        return pd.Series(dtype=float)


# ── Macro data ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=120, show_spinner=False)
def get_macro_data() -> dict:
    """
    Fetches Copper, USD/CLP, S&P 500, IPSA.
    Returns dict keyed by display name with keys: price, change_pct, timestamp, error.
    """
    results = {}
    for name, sym in MACRO_TICKERS.items():
        try:
            t = yf.Ticker(sym)
            fi = t.fast_info

            price = fi.last_price
            prev  = fi.previous_close

            if price is None or prev is None or prev == 0:
                results[name] = {"price": None, "change_pct": None, "timestamp": None, "error": "No data"}
                continue

            chg_pct   = ((price - prev) / prev) * 100
            timestamp = datetime.now()

            results[name] = {
                "price":      float(price),
                "change_pct": chg_pct,
                "timestamp":  timestamp,
                "error":      None,
            }
        except Exception as exc:
            results[name] = {"price": None, "change_pct": None, "timestamp": None, "error": str(exc)}

    return results


# ── FRED macro data ───────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_fred_data() -> dict:
    """
    Fetches from FRED:
      - DGS10: US 10-Year Treasury yield (daily, %)
      - IRLTLT01CLM156N: Chile 10Y government bond yield (monthly, %)
    Returns dict with keys 'DGS10' and 'INTDSRCLM193N', each containing
    value, prev, change_bps (DGS10 only), date, error.
    """
    result = {}

    # US 10Y Treasury yield
    try:
        r = requests.get(
            FRED_BASE,
            params={
                "series_id": "DGS10",
                "api_key":   FRED_API_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 10,
            },
            timeout=10,
        )
        obs = [o for o in r.json()["observations"] if o["value"] != "."]
        if len(obs) >= 2:
            current = float(obs[0]["value"])
            prev    = float(obs[1]["value"])
            result["DGS10"] = {
                "value":      current,
                "prev":       prev,
                "change_bps": round((current - prev) * 100),
                "date":       obs[0]["date"],
                "error":      None,
            }
        else:
            result["DGS10"] = {"value": None, "error": "Insufficient data"}
    except Exception as exc:
        result["DGS10"] = {"value": None, "error": str(exc)}

    # US Federal Funds Rate (monthly effective rate)
    try:
        r = requests.get(
            FRED_BASE,
            params={
                "series_id": "FEDFUNDS",
                "api_key":   FRED_API_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 3,
            },
            timeout=10,
        )
        obs = [o for o in r.json()["observations"] if o["value"] != "."]
        if len(obs) >= 2:
            current = float(obs[0]["value"])
            prev    = float(obs[1]["value"])
            result["FEDFUNDS"] = {
                "value":      current,
                "prev":       prev,
                "change_bps": round((current - prev) * 100),
                "date":       obs[0]["date"],
                "error":      None,
            }
        elif obs:
            result["FEDFUNDS"] = {
                "value": float(obs[0]["value"]),
                "prev": None, "change_bps": 0,
                "date": obs[0]["date"], "error": None,
            }
        else:
            result["FEDFUNDS"] = {"value": None, "error": "No data"}
    except Exception as exc:
        result["FEDFUNDS"] = {"value": None, "error": str(exc)}

    # BCCh short-term policy rate (monthly)
    try:
        r = requests.get(
            FRED_BASE,
            params={
                "series_id": "IRSTCI01CLM156N",
                "api_key":   FRED_API_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 3,
            },
            timeout=10,
        )
        obs = [o for o in r.json()["observations"] if o["value"] != "."]
        if len(obs) >= 2:
            current = float(obs[0]["value"])
            prev    = float(obs[1]["value"])
            result["IRSTCI01CLM156N"] = {
                "value":      current,
                "prev":       prev,
                "change_bps": round((current - prev) * 100),
                "date":       obs[0]["date"],
                "error":      None,
            }
        elif obs:
            result["IRSTCI01CLM156N"] = {
                "value": float(obs[0]["value"]),
                "prev": None, "change_bps": 0,
                "date": obs[0]["date"], "error": None,
            }
        else:
            result["IRSTCI01CLM156N"] = {"value": None, "error": "No data"}
    except Exception as exc:
        result["IRSTCI01CLM156N"] = {"value": None, "error": str(exc)}

    # Chile 10Y government bond yield (monthly)
    try:
        r = requests.get(
            FRED_BASE,
            params={
                "series_id": "IRLTLT01CLM156N",
                "api_key":   FRED_API_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 3,
            },
            timeout=10,
        )
        obs = [o for o in r.json()["observations"] if o["value"] != "."]
        if obs:
            result["IRLTLT01CLM156N"] = {
                "value": float(obs[0]["value"]),
                "date":  obs[0]["date"],
                "error": None,
            }
        else:
            result["IRLTLT01CLM156N"] = {"value": None, "error": "No data"}
    except Exception as exc:
        result["IRLTLT01CLM156N"] = {"value": None, "error": str(exc)}

    return result


# ── IPSA return for alpha ─────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def get_ipsa_return_since(start_date: str) -> Optional[float]:
    """
    Calculate IPSA total return (%) from start_date (YYYY-MM-DD) to today.
    Returns None if data is unavailable.
    """
    try:
        hist = yf.Ticker(IPSA_TICKER).history(start=start_date)
        if hist.empty or len(hist) < 2:
            return None
        start_price = float(hist["Close"].iloc[0])
        end_price   = float(hist["Close"].iloc[-1])
        return ((end_price - start_price) / start_price) * 100
    except Exception:
        return None


# ── Batch price helper ────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def get_prices_batch(tickers: tuple) -> dict:
    """
    Fetch current prices for multiple tickers.
    Accepts a tuple (hashable) so Streamlit can cache it.
    Returns dict keyed by ticker.
    """
    return {t: get_current_price(t) for t in tickers}
