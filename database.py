"""
database.py — Supabase backend (multi-user).

Replaces the old SQLite implementation.  All operations are scoped to a
user_id so each user sees only their own portfolio data.

user_id resolution (in order of priority):
  1. Explicit `user_id` kwarg (used by performance_engine.py).
  2. st.session_state["user_id"]  (set after login in app.py).
  3. RuntimeError — called outside an authenticated session.
"""

import streamlit as st
from datetime import date as date_cls
from typing import Any, Dict, List, Optional, Set

from supabase_client import get_admin_client

# Kept for import-compatibility with performance_engine.py (value ignored)
DB_PATH = "supabase"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _uid(user_id: Optional[str] = None) -> str:
    """Resolve the current user's UUID."""
    if user_id:
        return user_id
    uid = st.session_state.get("user_id")
    if not uid:
        raise RuntimeError("No authenticated user in session.")
    return uid


def _sb():
    return get_admin_client()


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def init_db(**kwargs) -> None:
    """No-op: tables are created once via supabase_schema.sql."""
    pass


# ── Holdings ──────────────────────────────────────────────────────────────────

def get_holdings(user_id: Optional[str] = None, **kwargs) -> List[Dict[str, Any]]:
    uid = _uid(user_id)
    res = _sb().table("holdings").select("*").eq("user_id", uid).order("buy_date").execute()
    return res.data or []


def add_holding(
    ticker: str,
    company_name: str,
    quantity: float,
    buy_price: float,
    buy_date: str,
    user_id: Optional[str] = None,
    **kwargs,
) -> None:
    uid = _uid(user_id)
    sb = _sb()
    sb.table("holdings").insert({
        "user_id":      uid,
        "ticker":       ticker,
        "company_name": company_name,
        "quantity":     float(quantity),
        "buy_price":    float(buy_price),
        "buy_date":     buy_date,
    }).execute()
    sb.table("transactions").insert({
        "user_id":  uid,
        "action":   "BUY",
        "ticker":   ticker,
        "quantity": float(quantity),
        "price":    float(buy_price),
        "date":     buy_date,
        "notes":    "Position opened",
    }).execute()


def delete_holding(holding_id: int, user_id: Optional[str] = None, **kwargs) -> None:
    uid = _uid(user_id)
    sb = _sb()
    res = sb.table("holdings").select("*").eq("id", holding_id).eq("user_id", uid).execute()
    if res.data:
        row = res.data[0]
        sb.table("holdings").delete().eq("id", holding_id).eq("user_id", uid).execute()
        sb.table("transactions").insert({
            "user_id":  uid,
            "action":   "REMOVE",
            "ticker":   row["ticker"],
            "quantity": row["quantity"],
            "price":    row["buy_price"],
            "date":     row["buy_date"],
            "notes":    "Position removed",
        }).execute()


# ── Cash Reserve ──────────────────────────────────────────────────────────────

def get_cash(user_id: Optional[str] = None, **kwargs) -> float:
    uid = _uid(user_id)
    res = _sb().table("cash_reserve").select("amount").eq("user_id", uid).execute()
    return float(res.data[0]["amount"]) if res.data else 0.0


def set_cash(amount: float, user_id: Optional[str] = None, **kwargs) -> None:
    uid = _uid(user_id)
    sb = _sb()
    sb.table("cash_reserve").upsert({"user_id": uid, "amount": float(amount)}).execute()
    sb.table("transactions").insert({
        "user_id": uid,
        "action":  "CASH_UPDATE",
        "price":   float(amount),
        "date":    date_cls.today().isoformat(),
        "notes":   "Cash reserve updated",
    }).execute()


# ── Transactions ──────────────────────────────────────────────────────────────

def get_transactions(limit: int = 100, user_id: Optional[str] = None, **kwargs) -> List[Dict[str, Any]]:
    uid = _uid(user_id)
    res = (
        _sb()
        .table("transactions")
        .select("date,action,ticker,quantity,price,notes")
        .eq("user_id", uid)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def get_all_transactions_raw(user_id: Optional[str] = None, **kwargs) -> List[Dict[str, Any]]:
    uid = _uid(user_id)
    res = (
        _sb()
        .table("transactions")
        .select("id,action,ticker,quantity,price,date,notes,created_at")
        .eq("user_id", uid)
        .order("created_at")
        .execute()
    )
    return res.data or []


# ── Daily Performance ─────────────────────────────────────────────────────────

def save_daily_performance_batch(
    records: List[Dict[str, Any]],
    user_id: Optional[str] = None,
    **kwargs,
) -> None:
    uid = _uid(user_id)
    rows = [
        {
            "user_id":      uid,
            "date":         r["date"],
            "nav":          r["nav"],
            "equity_value": r["equity_value"],
            "cash":         r["cash"],
            "ipsa_close":   r.get("ipsa_close"),
        }
        for r in records
    ]
    if rows:
        _sb().table("daily_performance").upsert(rows, on_conflict="user_id,date").execute()


def get_daily_performance(user_id: Optional[str] = None, **kwargs) -> List[Dict[str, Any]]:
    uid = _uid(user_id)
    res = (
        _sb()
        .table("daily_performance")
        .select("date,nav,equity_value,cash,ipsa_close")
        .eq("user_id", uid)
        .order("date")
        .execute()
    )
    return res.data or []


def get_daily_performance_dates(user_id: Optional[str] = None, **kwargs) -> Set[str]:
    uid = _uid(user_id)
    res = _sb().table("daily_performance").select("date").eq("user_id", uid).execute()
    return {row["date"] for row in (res.data or [])}


def clear_daily_performance(user_id: Optional[str] = None, **kwargs) -> None:
    uid = _uid(user_id)
    _sb().table("daily_performance").delete().eq("user_id", uid).execute()


# ── Capital Flows ─────────────────────────────────────────────────────────────

def add_capital_flow(
    date_str: str,
    amount: float,
    notes: str = "",
    user_id: Optional[str] = None,
    **kwargs,
) -> None:
    uid = _uid(user_id)
    _sb().table("capital_flows").insert({
        "user_id": uid,
        "date":    date_str,
        "amount":  float(amount),
        "notes":   notes,
    }).execute()


def get_capital_flows(user_id: Optional[str] = None, **kwargs) -> List[Dict[str, Any]]:
    uid = _uid(user_id)
    res = (
        _sb()
        .table("capital_flows")
        .select("id,date,amount,notes")
        .eq("user_id", uid)
        .order("date")
        .execute()
    )
    return res.data or []


def delete_capital_flow(flow_id: int, user_id: Optional[str] = None, **kwargs) -> None:
    uid = _uid(user_id)
    _sb().table("capital_flows").delete().eq("id", flow_id).eq("user_id", uid).execute()


# ── Legacy stubs (kept for import-compat) ─────────────────────────────────────

def save_nav_snapshot(*args, **kwargs) -> None:
    pass


def get_nav_history(**kwargs) -> List[Dict[str, Any]]:
    return []
