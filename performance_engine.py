"""
performance_engine.py — Backfill automático de NAV diario desde el log de transacciones.

No tiene imports de Streamlit: es un módulo puro de Python que puede llamarse
desde cualquier contexto (UI, cron, script).

Flujo:
  1. Lee todas las transacciones (incluyendo created_at).
  2. Reconstruye los períodos de cada posición (BUY → REMOVE).
  3. Reconstruye la caja histórica (CASH_UPDATE).
  4. Descarga precios de cierre históricos vía yfinance (una sola llamada por ticker).
  5. Calcula el NAV para cada fecha calendario faltante.
  6. Persiste en daily_performance (upsert por lote).
"""

import yfinance as yf
import pandas as pd
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set

import database as db
from config import IPSA_TICKER, CHILE_TZ


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    """Parse YYYY-MM-DD o 'YYYY-MM-DD HH:MM:SS' → date. Toma los primeros 10 chars."""
    return datetime.fromisoformat(s[:10]).date()


# ── Reconstrucción de posiciones ───────────────────────────────────────────────

def _build_position_periods(txns: List[Dict]) -> List[Dict]:
    """
    Convierte BUY + REMOVE en intervalos (open_date, close_date|None).

    Matching REMOVE → BUY:
      - REMOVE almacena: price=buy_price, date=buy_date, created_at=fecha de remoción.
      - Se empareja por (ticker, quantity, buy_price, buy_date), FIFO.

    Returns lista de dicts:
      {ticker, quantity, open_date: date, close_date: date|None}
    """
    open_positions: List[Dict] = []
    closed_positions: List[Dict] = []

    for t in txns:
        if t["action"] == "BUY":
            open_positions.append({
                "ticker":     t["ticker"],
                "quantity":   t["quantity"],
                "buy_price":  t["price"],
                "open_date":  _parse_date(t["date"]),
                "close_date": None,
            })
        elif t["action"] == "REMOVE":
            remove_buy_price = t["price"]
            remove_buy_date  = _parse_date(t["date"])
            remove_date      = _parse_date(t["created_at"])

            for i, pos in enumerate(open_positions):
                if (
                    pos["ticker"]    == t["ticker"]
                    and pos["buy_price"] == remove_buy_price
                    and pos["open_date"] == remove_buy_date
                    and pos["close_date"] is None
                ):
                    closed = dict(pos)
                    closed["close_date"] = remove_date
                    closed_positions.append(closed)
                    open_positions.pop(i)
                    break

    return open_positions + closed_positions


def _positions_on(periods: List[Dict], d: date) -> List[Dict]:
    """Filtra posiciones activas en la fecha d (open_date <= d < close_date)."""
    return [
        p for p in periods
        if p["open_date"] <= d
        and (p["close_date"] is None or p["close_date"] > d)
    ]


# ── Reconstrucción de caja ─────────────────────────────────────────────────────

def _cash_on(txns: List[Dict], d: date) -> float:
    """
    Caja disponible al cierre de la fecha d.
    Toma el último CASH_UPDATE donde date <= d.
    CASH_UPDATE almacena el saldo nuevo (no un delta) en la columna `price`.
    """
    best_cash: float = 0.0
    best_date: Optional[date] = None

    for t in txns:
        if t["action"] != "CASH_UPDATE" or not t["date"] or t["price"] is None:
            continue
        txn_date = _parse_date(t["date"])
        if txn_date <= d and (best_date is None or txn_date >= best_date):
            best_cash = float(t["price"])
            best_date = txn_date

    return best_cash


# ── Descarga de precios ────────────────────────────────────────────────────────

def _fetch_price_matrix(
    tickers: List[str],
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    Descarga cierres históricos para todos los tickers + IPSA.
    Retorna DataFrame con índice de objetos `date` y columnas por ticker.
    Los días sin datos (fin de semana, feriados) se rellenan hacia adelante (ffill).

    Timezone: convierte a America/Santiago antes de normalizar a fecha,
    para evitar desfase de un día en datos UTC.
    """
    all_tickers = list(dict.fromkeys(tickers + [IPSA_TICKER]))  # deduplica, mantiene orden
    series_dict: Dict[str, pd.Series] = {}

    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)

    for ticker in all_tickers:
        try:
            hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)

            # ^IPSA (y otros índices) a veces devuelve muy pocos datos con rango de fechas.
            # Fallback: pedir 2 años y filtrar manualmente al rango necesario.
            if len(hist) < 5:
                hist = yf.Ticker(ticker).history(period="2y", auto_adjust=True)

            if hist.empty:
                continue

            raw_idx = hist.index
            # Convertir a Santiago para evitar desfase UTC/Chile
            if hasattr(raw_idx, "tz") and raw_idx.tz is not None:
                raw_idx = raw_idx.tz_convert(CHILE_TZ).tz_localize(None)
            norm_idx = pd.to_datetime(raw_idx).normalize()  # DatetimeIndex sin tz

            # Filtrar al rango [start, end) solicitado
            mask = (norm_idx >= start_ts) & (norm_idx < end_ts)
            if not mask.any():
                continue

            series_dict[ticker] = pd.Series(
                hist["Close"].values[mask],
                index=norm_idx[mask].date,
                name=ticker,
                dtype=float,
            )
        except Exception:
            pass  # ticker no disponible → columna ausente, NAV parcial

    if not series_dict:
        return pd.DataFrame()

    # Rango completo de fechas calendario
    full_range = pd.date_range(start=start, end=end, freq="D").date
    matrix = pd.DataFrame(series_dict, index=full_range)
    matrix.sort_index(inplace=True)
    matrix.ffill(inplace=True)  # propaga viernes al sábado y domingo
    matrix.bfill(inplace=True)  # rellena hacia atrás en T=0 si es feriado (para IPSA base)
    return matrix


# ── Motor principal ────────────────────────────────────────────────────────────

def run_backfill(user_id: str = None) -> int:
    """
    Calcula y persiste el NAV diario para todas las fechas calendario faltantes.

    Retorna el número de nuevos registros guardados.

    Llamada segura de repetir: si no hay fechas faltantes, retorna 0 sin hacer
    ninguna llamada de red.
    """
    txns = db.get_all_transactions_raw(user_id=user_id)
    if not txns:
        return 0

    # ── Reconstruir posiciones ────────────────────────────────────────────────
    periods = _build_position_periods(txns)
    if not periods:
        return 0

    # ── Rango de fechas ───────────────────────────────────────────────────────
    first_date = min(p["open_date"] for p in periods)
    last_date  = date.today()

    # ── Fechas faltantes (incrementalmente) ──────────────────────────────────
    already: Set[str] = db.get_daily_performance_dates(user_id=user_id)
    full_range = [
        first_date + timedelta(days=i)
        for i in range((last_date - first_date).days + 1)
    ]
    missing = [d for d in full_range if d.isoformat() not in already]

    if not missing:
        return 0

    # ── Descarga de precios (un solo bloque para todo el período faltante) ───
    tickers     = list({p["ticker"] for p in periods})
    fetch_start = missing[0].isoformat()
    fetch_end   = (missing[-1] + timedelta(days=1)).isoformat()  # end es exclusivo en yfinance
    price_matrix = _fetch_price_matrix(tickers, fetch_start, fetch_end)

    # ── Cálculo de NAV por fecha ──────────────────────────────────────────────
    records: List[Dict] = []

    for d in missing:
        # Posiciones activas
        equity = 0.0
        for pos in _positions_on(periods, d):
            ticker = pos["ticker"]
            if (
                not price_matrix.empty
                and ticker in price_matrix.columns
                and d in price_matrix.index
            ):
                px = price_matrix.loc[d, ticker]
                if pd.notna(px):
                    equity += pos["quantity"] * float(px)

        cash = _cash_on(txns, d)
        nav  = equity + cash

        # Cierre IPSA
        ipsa_close: Optional[float] = None
        if (
            not price_matrix.empty
            and IPSA_TICKER in price_matrix.columns
            and d in price_matrix.index
        ):
            val = price_matrix.loc[d, IPSA_TICKER]
            if pd.notna(val):
                ipsa_close = float(val)

        records.append({
            "date":         d.isoformat(),
            "nav":          nav,
            "equity_value": equity,
            "cash":         cash,
            "ipsa_close":   ipsa_close,
        })

    # ── Persistir ─────────────────────────────────────────────────────────────
    if records:
        db.save_daily_performance_batch(records, user_id=user_id)

    return len(records)


def clear_and_rebuild(user_id: str = None) -> int:
    """
    Borra daily_performance y reconstruye desde cero.
    Usar después de agregar posiciones retroactivas o modificar CASH_UPDATEs.
    """
    db.clear_daily_performance(user_id=user_id)
    return run_backfill(user_id=user_id)
