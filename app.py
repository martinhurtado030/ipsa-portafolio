"""
IPSA Portfolio Manager — Streamlit Application
Santiago Stock Exchange | Data: Yahoo Finance | Storage: SQLite
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
from datetime import datetime, date, timedelta
import pytz
import feedparser
from supabase_client import get_anon_client

import database as db
import data_fetcher as df_
import performance_engine as pe
from analysis import (
    calc_holding_pnl,
    calc_alpha,
    calc_portfolio_summary,
    calc_sma_signals,
    calc_support_resistance,
    calc_rsi,
    calc_memory_zones,
    calc_volume_confirmation,
    calc_buy_zone_alert,
    macro_narrative,
    SIGNAL_LABELS,
)
from config import IPSA_CONSTITUENTS, IPSA_SECTORS, CHILE_TZ, MARKET_OPEN_HOUR, MARKET_CLOSE_HOUR, IPSA_TICKER


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="IPSA Portfolio Manager",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Bootstrap DB ──────────────────────────────────────────────────────────────

db.init_db()

# ══════════════════════════════════════════════════════════════════════════════
# AUTH GATE — must pass before any DB call or dashboard render
# ══════════════════════════════════════════════════════════════════════════════

def _show_auth_screen() -> None:
    """Full-page login / register form. Sets st.session_state on success."""
    st.title("📈 IPSA Portfolio Manager")
    st.markdown("#### Acceso — Bolsa de Santiago")
    st.divider()

    col_auth, _ = st.columns([1, 1])
    with col_auth:
        tab_in, tab_reg = st.tabs(["Iniciar Sesión", "Crear Cuenta"])

        with tab_in:
            email    = st.text_input("Email", key="li_email", placeholder="tu@email.com")
            password = st.text_input("Contraseña", type="password", key="li_pw")
            if st.button("Entrar", type="primary", use_container_width=True, key="btn_li"):
                if not email or not password:
                    st.warning("Completa email y contraseña.")
                else:
                    try:
                        sb  = get_anon_client()
                        res = sb.auth.sign_in_with_password({"email": email, "password": password})
                        st.session_state["user_id"]    = res.user.id
                        st.session_state["user_email"] = res.user.email
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Error al iniciar sesión: {exc}")

        with tab_reg:
            st.caption(
                "Cada cuenta tiene su portafolio independiente. "
                "Comparte la URL con tu familia y que cada uno cree la suya."
            )
            r_email = st.text_input("Email", key="reg_email", placeholder="tu@email.com")
            r_pw    = st.text_input("Contraseña (mín. 6 caracteres)", type="password", key="reg_pw")
            if st.button("Crear Cuenta", type="primary", use_container_width=True, key="btn_reg"):
                if not r_email or not r_pw:
                    st.warning("Completa email y contraseña.")
                elif len(r_pw) < 6:
                    st.warning("La contraseña debe tener al menos 6 caracteres.")
                else:
                    try:
                        sb  = get_anon_client()
                        res = sb.auth.sign_up({"email": r_email, "password": r_pw})
                        if res.user:
                            st.success(
                                "✅ Cuenta creada. "
                                "Si Supabase tiene confirmación de email activada, "
                                "revisa tu bandeja de entrada antes de iniciar sesión. "
                                "Si no, ya puedes entrar en la pestaña 'Iniciar Sesión'."
                            )
                        else:
                            st.info("Revisa tu email para confirmar la cuenta.")
                    except Exception as exc:
                        st.error(f"Error al registrar: {exc}")


if "user_id" not in st.session_state:
    _show_auth_screen()
    st.stop()

# ── Chile clock ───────────────────────────────────────────────────────────────

chile_tz    = pytz.timezone(CHILE_TZ)
now_chile   = datetime.now(chile_tz)
market_open = (
    now_chile.weekday() < 5
    and MARKET_OPEN_HOUR <= now_chile.hour < MARKET_CLOSE_HOUR
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_clp(value: float) -> str:
    return f"${value:,.0f}"


def fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def pnl_color(value: float) -> str:
    return "green" if value >= 0 else "red"


def _compute_twr_b100(df: pd.DataFrame, buy_flows_by_date: dict) -> pd.Series:
    """
    Unit Pricing basado en equity_value (valor de acciones a precios de mercado).

    Reglas:
    - La Caja es INVISIBLE: solo los precios de las acciones mueven el rendimiento.
    - Cada BUY nuevo emite cuotas al valor unitario del día anterior → el valor
      cuota no cambia en el día de la compra (solo por variaciones de precios).
    - buy_flows_by_date: {fecha: qty*buy_price} detectado automáticamente desde
      las transacciones BUY del log.

    Invariante: si los precios de todas las acciones no se mueven, B100 = 100.
    """
    equities = df["equity_value"].to_numpy(dtype=float)
    dates    = df["date"].dt.strftime("%Y-%m-%d").tolist()
    n        = len(equities)
    result   = np.full(n, 100.0)

    if n == 0 or equities[0] <= 0:
        return pd.Series(result, index=df.index)

    total_units     = 1.0
    unit_value      = equities[0]
    base_unit_value = equities[0]

    for i in range(1, n):
        external = buy_flows_by_date.get(dates[i], 0.0)

        # Nuevas cuotas al valor unitario del día anterior — la compra no mueve el precio
        if external > 0 and unit_value > 0:
            total_units += external / unit_value

        if equities[i] > 0 and total_units > 0:
            unit_value = equities[i] / total_units
            result[i]  = (unit_value / base_unit_value) * 100.0
        else:
            result[i] = result[i - 1]   # sin equity (todo en caja): mantener último valor

    return pd.Series(result, index=df.index)


def ts_str(ts) -> str:
    """Format a pandas Timestamp for display in Chile time."""
    if ts is None:
        return "N/A"
    try:
        import pytz as _pytz
        tz = _pytz.timezone(CHILE_TZ)
        if ts.tzinfo is None:
            ts = _pytz.utc.localize(ts)
        return ts.astimezone(tz).strftime("%Y-%m-%d %H:%M CLT")
    except Exception:
        return str(ts)


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("IPSA Portfolio")
    st.caption(f"Santiago: {now_chile.strftime('%Y-%m-%d %H:%M CLT')}")

    # ── User session ──────────────────────────────────────────────────────────
    st.caption(f"👤 {st.session_state.get('user_email', '')}")
    if st.button("Cerrar Sesión", key="btn_logout", use_container_width=True):
        st.session_state.pop("user_id", None)
        st.session_state.pop("user_email", None)
        st.cache_data.clear()
        st.rerun()

    if market_open:
        st.success("Market Open")
    else:
        st.error("Market Closed")

    st.divider()

    # Cash reserve
    st.subheader("Cash Reserve (CLP)")
    current_cash = db.get_cash()
    st.metric("Balance", fmt_clp(current_cash))

    with st.expander("Update Cash"):
        new_cash = st.number_input(
            "New amount (CLP)",
            min_value=0.0,
            value=float(current_cash),
            step=100_000.0,
            format="%.0f",
        )
        if st.button("Save", key="save_cash"):
            db.set_cash(new_cash)
            st.success("Cash updated.")
            st.rerun()

    st.divider()

    if st.button("Refresh Market Data", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.caption("Cache TTL: prices 5 min | macro 2 min | fundamentals 1 hr")


# ── Load portfolio ────────────────────────────────────────────────────────────

holdings = db.get_holdings()
cash     = db.get_cash()

tickers  = tuple(dict.fromkeys(h["ticker"] for h in holdings))   # dedup, keep order
prices   = df_.get_prices_batch(tickers) if tickers else {}

summary  = calc_portfolio_summary(holdings, prices, cash)


# ── Main header ───────────────────────────────────────────────────────────────

st.title("IPSA Portfolio Manager")
st.caption(
    "Santiago Stock Exchange (SSE) | Real-time data: Yahoo Finance | "
    "Strictly Chilean focus — CMF regulated securities only."
)

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_overview, tab_manage, tab_sieve, tab_briefing, tab_charts, tab_performance, tab_noticias = st.tabs([
    "Overview",
    "Manage Holdings",
    "Universal Sieve",
    "Morning Briefing",
    "Charts",
    "Performance",
    "Noticias",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════

with tab_overview:

    # Top KPIs
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total NAV (CLP)",   fmt_clp(summary["total_nav"]))
    k2.metric("Equity Value",      fmt_clp(summary["total_equity_value"]))
    k3.metric("Cash Reserve",      fmt_clp(summary["cash"]))
    k4.metric(
        "Total P&L",
        fmt_clp(summary["total_pnl"]),
        delta=fmt_pct(summary["total_pnl_pct"]),
        delta_color="normal",
    )
    k5.metric(
        "Investment Level",
        f"{summary['equity_pct']:.1f}% Equities",
        delta=f"{summary['cash_pct']:.1f}% Cash",
        delta_color="off",
    )

    st.divider()

    col_pie1, col_pie2, col_table = st.columns([1, 1, 2])

    # Build shared allocation data once
    alloc_rows = []
    for h in holdings:
        pd_data = prices.get(h["ticker"], {})
        price   = pd_data.get("price")
        if price:
            short = h["ticker"].replace(".SN", "")
            alloc_rows.append({
                "label":  short,
                "sector": IPSA_SECTORS.get(short, "Other"),
                "value":  h["quantity"] * price,
            })
    if cash > 0:
        alloc_rows.append({"label": "Cash", "sector": "Cash", "value": cash})

    # Donut 1 — by position
    with col_pie1:
        st.subheader("Por Posición")
        if alloc_rows:
            df_alloc = pd.DataFrame(alloc_rows)
            fig1 = px.pie(
                df_alloc,
                values="value",
                names="label",
                color_discrete_sequence=px.colors.qualitative.Set2,
                hole=0.38,
            )
            fig1.update_layout(
                height=340,
                margin=dict(t=10, b=10, l=10, r=10),
                showlegend=False,
            )
            fig1.update_traces(textposition="inside", textinfo="percent+label", textfont_size=11)
            st.plotly_chart(fig1, use_container_width=True)
        else:
            st.info("Add holdings to see allocation.")

    # Donut 2 — by sector
    with col_pie2:
        st.subheader("Por Sector")
        if alloc_rows:
            df_sector = (
                pd.DataFrame(alloc_rows)
                .groupby("sector", as_index=False)["value"]
                .sum()
            )
            fig2 = px.pie(
                df_sector,
                values="value",
                names="sector",
                color_discrete_sequence=px.colors.qualitative.Pastel,
                hole=0.38,
            )
            fig2.update_layout(
                height=340,
                margin=dict(t=10, b=10, l=10, r=10),
                showlegend=False,
            )
            fig2.update_traces(textposition="inside", textinfo="percent+label", textfont_size=11)
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Add holdings to see sector allocation.")

    # Holdings table
    with col_table:
        st.subheader("Holdings Summary")
        if holdings:
            rows = []
            for h in holdings:
                pd_data       = prices.get(h["ticker"], {})
                current_price = pd_data.get("price")
                ts            = pd_data.get("timestamp")
                err           = pd_data.get("error")

                if current_price:
                    pnl = calc_holding_pnl(h, current_price)
                    rows.append({
                        "Ticker":       h["ticker"].replace(".SN", ""),
                        "Company":      h["company_name"],
                        "Qty":          int(h["quantity"]),
                        "Buy (CLP)":    h["buy_price"],
                        "Current":      current_price,
                        "Value (CLP)":  pnl["current_value"],
                        "P&L CLP":      pnl["gain_loss_clp"],
                        "P&L %":        pnl["gain_loss_pct"],
                        "As of":        ts_str(ts),
                    })
                else:
                    rows.append({
                        "Ticker":      h["ticker"].replace(".SN", ""),
                        "Company":     h["company_name"],
                        "Qty":         int(h["quantity"]),
                        "Buy (CLP)":   h["buy_price"],
                        "Current":     None,
                        "Value (CLP)": None,
                        "P&L CLP":     None,
                        "P&L %":       None,
                        "As of":       err or "N/A",
                    })

            df_holdings = pd.DataFrame(rows)

            def style_row(row):
                styles = [""] * len(row)
                idx = df_holdings.columns.tolist()
                for col in ["P&L CLP", "P&L %"]:
                    if col in idx:
                        i = idx.index(col)
                        val = row[col]
                        if pd.notna(val):
                            styles[i] = f"color: {'green' if val >= 0 else 'red'}"
                return styles

            styled = (
                df_holdings.style
                .apply(style_row, axis=1)
                .format({
                    "Buy (CLP)":   "{:,.1f}",
                    "Current":     lambda x: f"{x:,.1f}" if pd.notna(x) else "N/A",
                    "Value (CLP)": lambda x: fmt_clp(x) if pd.notna(x) else "N/A",
                    "P&L CLP":     lambda x: fmt_clp(x) if pd.notna(x) else "N/A",
                    "P&L %":       lambda x: fmt_pct(x) if pd.notna(x) else "N/A",
                })
            )
            st.dataframe(styled, use_container_width=True, height=420)
        else:
            st.info("No holdings yet. Use the 'Manage Holdings' tab to add positions.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — MANAGE HOLDINGS
# ══════════════════════════════════════════════════════════════════════════════

with tab_manage:

    col_add, col_existing = st.columns([1, 1])

    # ── Add position ──────────────────────────────────────────────────────────
    with col_add:
        st.subheader("Add Position")

        options = [f"{t} — {n}" for t, n in IPSA_CONSTITUENTS.items()]
        selected_opt = st.selectbox("IPSA Constituent", options, key="ticker_select")

        ticker_sel  = selected_opt.split(" — ")[0]
        company_sel = IPSA_CONSTITUENTS[ticker_sel]

        # Live price preview
        price_preview = df_.get_current_price(ticker_sel)
        if price_preview.get("price"):
            p   = price_preview["price"]
            chg = price_preview.get("change_pct", 0)
            ts  = price_preview.get("timestamp")
            st.success(
                f"Current price: **{p:,.1f} CLP** ({chg:+.2f}%) "
                f"| as of {ts_str(ts)}"
            )
            default_price = float(p)
        else:
            st.warning(f"Price fetch failed: {price_preview.get('error', 'Unknown')}")
            default_price = 0.0

        quantity  = st.number_input("Quantity (shares)", min_value=1, step=1, value=100)
        buy_price = st.number_input(
            "Buy Price (CLP)",
            min_value=0.01,
            step=1.0,
            value=default_price,
            format="%.2f",
        )
        buy_date  = st.date_input("Purchase Date", value=date.today())

        total_inv = quantity * buy_price
        st.info(f"Total investment: **{fmt_clp(total_inv)}**")

        if st.button("Add Position", type="primary", key="btn_add"):
            db.add_holding(
                ticker=ticker_sel,
                company_name=company_sel,
                quantity=float(quantity),
                buy_price=float(buy_price),
                buy_date=str(buy_date),
            )
            st.success(f"Added {quantity:,} shares of {ticker_sel} at {buy_price:,.2f} CLP.")
            st.cache_data.clear()
            st.rerun()

    # ── Existing positions ────────────────────────────────────────────────────
    with col_existing:
        st.subheader("Current Positions")
        if holdings:
            for h in holdings:
                pd_data = prices.get(h["ticker"], {})
                price   = pd_data.get("price")
                label   = (
                    f"{h['ticker'].replace('.SN','')} — {int(h['quantity']):,} shares"
                    + (f" | Current: {price:,.1f} CLP" if price else "")
                )
                with st.expander(label):
                    st.write(f"**Company:** {h['company_name']}")
                    st.write(f"**Buy Price:** {h['buy_price']:,.2f} CLP")
                    st.write(f"**Buy Date:** {h['buy_date']}")
                    st.write(f"**Cost Basis:** {fmt_clp(h['quantity'] * h['buy_price'])}")
                    if price:
                        pnl = calc_holding_pnl(h, price)
                        color = "green" if pnl["gain_loss_clp"] >= 0 else "red"
                        st.markdown(
                            f"**P&L:** :{color}[{fmt_clp(pnl['gain_loss_clp'])} "
                            f"({fmt_pct(pnl['gain_loss_pct'])})]"
                        )
                    if st.button("Remove Position", key=f"del_{h['id']}", type="secondary"):
                        db.delete_holding(h["id"])
                        st.cache_data.clear()
                        st.rerun()
        else:
            st.info("No positions yet.")

    st.divider()

    # Transaction log
    st.subheader("Transaction Log")
    txns = db.get_transactions()
    if txns:
        st.dataframe(pd.DataFrame(txns), use_container_width=True, height=250)
    else:
        st.info("No transactions recorded.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — UNIVERSAL SIEVE
# ══════════════════════════════════════════════════════════════════════════════

with tab_sieve:
    st.subheader("The Universal Sieve — Full Position Analysis")
    st.caption(
        "P&L | Alpha vs IPSA | 50/200-SMA Signals | "
        "Valuation (P/E or P/B) | Support & Resistance | CMF Links"
    )

    if not holdings:
        st.info("Add holdings in 'Manage Holdings' to run the sieve.")
    else:
        for h in holdings:
            ticker    = h["ticker"]
            short_tkr = ticker.replace(".SN", "")

            st.markdown("---")
            st.markdown(f"## {short_tkr} — {h['company_name']}")
            st.caption(
                f"Held since {h['buy_date']} | "
                f"Buy price: {h['buy_price']:,.2f} CLP | "
                f"Qty: {int(h['quantity']):,}"
            )

            pd_data       = prices.get(ticker, {})
            current_price = pd_data.get("price")
            ts            = pd_data.get("timestamp")

            if not current_price:
                st.error(
                    f"Price unavailable (last verified: {ts_str(ts)}). "
                    f"Error: {pd_data.get('error', 'Unknown')}"
                )
                continue

            # --- Row 1: Performance | Technical | Fundamentals
            col_perf, col_tech, col_fund = st.columns(3)

            # Performance
            with col_perf:
                st.markdown("### Performance")
                pnl = calc_holding_pnl(h, current_price)

                st.metric(
                    "Current Price (CLP)",
                    f"{current_price:,.2f}",
                    delta=fmt_pct(pnl["gain_loss_pct"]),
                    delta_color="normal",
                    help=f"Last verified: {ts_str(ts)}",
                )
                st.metric("P&L (CLP)", fmt_clp(pnl["gain_loss_clp"]))
                st.metric("Current Value", fmt_clp(pnl["current_value"]))
                st.metric("Cost Basis", fmt_clp(pnl["cost_basis"]))

                # Alpha vs IPSA
                stock_ret   = pnl["gain_loss_pct"]
                ipsa_ret    = df_.get_ipsa_return_since(h["buy_date"])
                alpha       = calc_alpha(stock_ret, ipsa_ret)

                if alpha is not None:
                    st.metric(
                        "Alpha vs IPSA",
                        fmt_pct(alpha),
                        delta=f"Stock {fmt_pct(stock_ret)} | IPSA {fmt_pct(ipsa_ret)}",
                        delta_color="off",
                        help="Relative performance since buy date",
                    )
                else:
                    st.metric("Alpha vs IPSA", "N/A", help="IPSA data unavailable")

            # Technical
            with col_tech:
                st.markdown("### Technical Signals")
                hist     = df_.get_historical_data(ticker, "2y")
                sma_data = calc_sma_signals(hist)
                sr_data  = calc_support_resistance(hist)

                if sma_data["sma50"]:
                    st.metric("50-Day SMA", f"{sma_data['sma50']:,.2f}")
                else:
                    st.metric("50-Day SMA", "Insufficient data (<50 bars)")

                if sma_data["sma200"]:
                    st.metric("200-Day SMA", f"{sma_data['sma200']:,.2f}")
                    st.metric("Stretch from 200-SMA", fmt_pct(sma_data["stretch_pct"]))
                else:
                    st.metric("200-Day SMA", "Insufficient data (<200 bars)")

                if sma_data["signal"]:
                    label = SIGNAL_LABELS.get(sma_data["signal"], sma_data["signal"])
                    signal_color = {
                        "GOLDEN_CROSS":       "success",
                        "BULLISH":            "success",
                        "OVERSOLD_EXTREME":   "success",
                        "DEATH_CROSS":        "error",
                        "BEARISH":            "error",
                        "OVERBOUGHT_EXTREME": "warning",
                    }.get(sma_data["signal"], "info")
                    getattr(st, signal_color)(f"Signal: {label}")

                st.divider()
                if sr_data["support"]:
                    st.metric("Support (60-day)", f"{sr_data['support']:,.2f} CLP")
                if sr_data["resistance"]:
                    st.metric("Resistance (60-day)", f"{sr_data['resistance']:,.2f} CLP")
                if sr_data["period_low"]:
                    st.caption(
                        f"60-day range: {sr_data['period_low']:,.1f} — "
                        f"{sr_data['period_high']:,.1f} CLP"
                    )

            # Fundamentals
            with col_fund:
                st.markdown("### Fundamentals & Catalysts")
                info      = df_.get_stock_info(ticker)
                dividends = df_.get_dividends(ticker)

                pe_ratio = info.get("trailingPE")
                pb       = info.get("priceToBook")
                dy       = info.get("dividendYield")
                mcap     = info.get("marketCap")

                if pe_ratio:
                    st.metric("P/E (trailing)", f"{pe_ratio:.1f}x")
                if pb:
                    st.metric("P/B Ratio", f"{pb:.2f}x")
                if dy:
                    # Yahoo Finance returns dividendYield already as a percentage for .SN tickers
                    # (e.g. 0.56 means 0.56%, 3.2 means 3.2%) — no multiplication needed.
                    st.metric("Dividend Yield", f"{dy:.2f}%")
                if mcap:
                    unit = "B" if mcap >= 1e9 else "M"
                    val  = mcap / 1e9 if mcap >= 1e9 else mcap / 1e6
                    st.metric("Market Cap", f"${val:.1f}{unit}")

                st.divider()
                # Dividends
                if not dividends.empty:
                    last_div     = float(dividends.iloc[-1])
                    last_div_dt  = dividends.index[-1]
                    st.success(
                        f"Last dividend: {last_div:,.2f} CLP "
                        f"({last_div_dt.strftime('%Y-%m-%d')})"
                    )
                else:
                    st.caption("No dividend history available.")

                # CMF filings link
                cmf_url = (
                    f"https://www.cmfchile.cl/sitio/aplic/serdoc/ver_sgd.php"
                    f"?s=emiso&q={short_tkr}"
                )
                st.markdown(f"[CMF Filings for {short_tkr}]({cmf_url})")

            # --- Row 2: Zonas de Confluencia ──────────────────────────────────
            st.markdown("#### Zonas de Confluencia & Señal de Entrada")

            hist_6m      = df_.get_historical_data(ticker, "1y")
            rsi_val      = calc_rsi(hist_6m)
            memory_zones = calc_memory_zones(hist_6m)
            vol_conf     = calc_volume_confirmation(hist_6m)
            buy_alert    = calc_buy_zone_alert(current_price, sma_data, rsi_val, memory_zones)

            zc1, zc2, zc3 = st.columns(3)

            # — Zonas de Memoria (S/R con >= 2 toques)
            with zc1:
                st.markdown("**Zonas de Memoria (6M)**")
                supports    = memory_zones.get("support_zones", [])
                resistances = memory_zones.get("resistance_zones", [])

                if supports:
                    st.markdown("*Soportes:*")
                    for z in supports[:3]:
                        dist_pct = ((current_price - z["level"]) / z["level"]) * 100
                        st.markdown(
                            f"&nbsp;&nbsp;`{z['level']:,.1f}` — "
                            f"{z['touches']} toques · {dist_pct:+.1f}% desde precio actual",
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("Sin soportes con ≥ 2 toques en 6M.")

                if resistances:
                    st.markdown("*Resistencias:*")
                    for z in resistances[:3]:
                        dist_pct = ((z["level"] - current_price) / current_price) * 100
                        st.markdown(
                            f"&nbsp;&nbsp;`{z['level']:,.1f}` — "
                            f"{z['touches']} toques · {dist_pct:+.1f}% hasta resistencia",
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("Sin resistencias con ≥ 2 toques en 6M.")

            # — RSI + Buy Zone Alert
            with zc2:
                st.markdown("**RSI (14) & Punto de Entrada**")

                if rsi_val is not None:
                    rsi_color = (
                        "🟢" if rsi_val < 35
                        else "🔴" if rsi_val > 70
                        else "⚪"
                    )
                    rsi_label = (
                        "Sobrevendido" if rsi_val < 35
                        else "Sobrecomprado" if rsi_val > 70
                        else "Neutral"
                    )
                    st.metric("RSI 14", f"{rsi_val:.1f}", delta=rsi_label, delta_color="off")
                else:
                    st.metric("RSI 14", "N/A")

                alert = buy_alert["alert_level"]
                conds = buy_alert["conditions"]
                det   = buy_alert["details"]

                ALERT_UI = {
                    "STRONG":   ("🟢 BUY ZONE — FUERTE",   "success"),
                    "MODERATE": ("🟡 BUY ZONE — MODERADA", "warning"),
                    "WATCH":    ("⚪ En Vigilancia",        "info"),
                }
                if alert:
                    label, fn = ALERT_UI[alert]
                    getattr(st, fn)(label)
                    for c in conds:
                        if c == "support_zone" and det.get("nearest_support"):
                            ns = det["nearest_support"]
                            st.markdown(
                                f"✓ Cerca de soporte `{ns['level']:,.1f}` · {ns['touches']} toques"
                            )
                        elif c == "sma200" and sma_data.get("sma200"):
                            st.markdown(f"✓ Cerca de SMA 200 ({sma_data['sma200']:,.1f})")
                        elif c == "rsi_oversold" and rsi_val is not None:
                            st.markdown(f"✓ RSI < 35 ({rsi_val:.1f})")
                else:
                    st.info("Sin confluencia activa.")

            # — Confirmación de Volumen
            with zc3:
                st.markdown("**Confirmación de Volumen**")

                if vol_conf["ratio"] is not None:
                    ratio = vol_conf["ratio"]
                    conf  = vol_conf["confirmed"]
                    st.metric(
                        "Vol. actual / Promedio 20s",
                        f"{ratio:.2f}x",
                        delta="Confirmado ✓" if conf else "Sin confirmar",
                        delta_color="normal" if conf else "off",
                    )
                    avg_m = vol_conf["avg_vol"] / 1e6
                    cur_m = vol_conf["current_vol"] / 1e6
                    st.caption(
                        f"Sesión: {cur_m:.1f}M · Promedio 20s: {avg_m:.1f}M"
                    )
                    if conf:
                        st.success(
                            "Volumen > 1.2x promedio — flujo institucional probable."
                        )
                    else:
                        st.caption("Volumen insuficiente para confirmar rebote.")
                else:
                    st.caption("Datos de volumen no disponibles.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — MORNING BRIEFING
# ══════════════════════════════════════════════════════════════════════════════

with tab_briefing:
    st.subheader(f"Morning Briefing — {now_chile.strftime('%A, %B %d, %Y')}")
    st.caption(f"Santiago: {now_chile.strftime('%H:%M CLT')} | Market {'Open' if market_open else 'Closed'}")

    # ── Macro panel ───────────────────────────────────────────────────────────
    st.markdown("### Global Macro Signals")

    macro = df_.get_macro_data()

    m1, m2, m3, m4 = st.columns(4)
    macro_cols_map = {
        "Copper (USD/lb)": m1,
        "USD/CLP":         m2,
        "S&P 500":         m3,
        "IPSA":            m4,
    }
    icons = {
        "Copper (USD/lb)": "Copper",
        "USD/CLP":         "USD/CLP",
        "S&P 500":         "S&P 500",
        "IPSA":            "IPSA",
    }

    for name, col in macro_cols_map.items():
        data = macro.get(name, {})
        with col:
            if data.get("price"):
                chg = data.get("change_pct", 0) or 0
                col.metric(
                    name,
                    f"{data['price']:,.3f}" if name == "Copper (USD/lb)" else f"{data['price']:,.2f}",
                    delta=fmt_pct(chg),
                    delta_color="normal",
                )
                col.caption(f"Last: {ts_str(data.get('timestamp'))}")
            else:
                col.metric(name, "N/A")
                col.caption(data.get("error", "Data unavailable"))

    st.divider()

    # ── FRED Macroeconomic Snapshot ────────────────────────────────────────────
    st.markdown("### Snapshot Macroeconómico")

    fred = df_.get_fred_data()
    dgs10   = fred.get("DGS10", {})
    fedfunds = fred.get("FEDFUNDS", {})
    bcch    = fred.get("IRSTCI01CLM156N", {})
    clrate  = fred.get("IRLTLT01CLM156N", {})

    fs1, fs2, fs3, fs4, fs5 = st.columns(5)

    # Fed Funds Rate
    with fs1:
        if fedfunds.get("value") is not None:
            bps = fedfunds.get("change_bps", 0)
            bps_str = f"{bps:+d} bps" if bps != 0 else "sin cambio"
            st.metric(
                "Fed Funds Rate",
                f"{fedfunds['value']:.2f}%",
                delta=bps_str,
                delta_color="inverse",
            )
            st.caption(f"FRED · {fedfunds['date']}")
        else:
            st.metric("Fed Funds Rate", "N/A")
            st.caption(fedfunds.get("error", "Unavailable"))

    # US 10Y Treasury
    with fs2:
        if dgs10.get("value") is not None:
            bps = dgs10["change_bps"]
            bps_str = f"{bps:+d} bps" if bps != 0 else "0 bps"
            st.metric(
                "US Treasury 10Y",
                f"{dgs10['value']:.2f}%",
                delta=bps_str,
                delta_color="inverse",
            )
            st.caption(f"FRED · {dgs10['date']}")
        else:
            st.metric("US Treasury 10Y", "N/A")
            st.caption(dgs10.get("error", "Unavailable"))

    # BCCh policy rate
    with fs3:
        if bcch.get("value") is not None:
            bps = bcch.get("change_bps", 0)
            bps_str = f"{bps:+d} bps" if bps != 0 else "sin cambio"
            st.metric(
                "Tasa BCCh (TPM)",
                f"{bcch['value']:.2f}%",
                delta=bps_str,
                delta_color="inverse",
            )
            st.caption(f"FRED · {bcch['date']}")
        else:
            st.metric("Tasa BCCh (TPM)", "N/A")
            st.caption(bcch.get("error", "Unavailable"))

    # Chile 10Y bond
    with fs4:
        if clrate.get("value") is not None:
            st.metric("Bono Chile 10Y", f"{clrate['value']:.2f}%")
            st.caption(f"FRED · {clrate['date']}")
        else:
            st.metric("Bono Chile 10Y", "N/A")
            st.caption(clrate.get("error", "Unavailable"))

    # Risk status
    with fs5:
        if dgs10.get("value") is not None:
            bps = dgs10["change_bps"]
            val = dgs10["value"]
            if bps > 5:
                status, color = "⚠ ADVERSO", "red"
            elif bps < -5:
                status, color = "✓ FAVORABLE", "green"
            elif val > 4.5:
                status, color = "⚠ ADVERSO", "red"
            elif val < 3.5:
                status, color = "✓ FAVORABLE", "green"
            else:
                status, color = "➖ NEUTRAL", "gray"
            st.markdown("**Estatus de Riesgo**")
            st.markdown(
                f"<span style='font-size:1.4rem;font-weight:700;color:{color}'>{status}</span>",
                unsafe_allow_html=True,
            )
            st.caption("Basado en tendencia US10Y")
        else:
            st.markdown("**Estatus de Riesgo:** N/A")

    # US10Y bond analysis
    if dgs10.get("value") is not None:
        bps  = dgs10["change_bps"]
        val  = dgs10["value"]
        date_str = dgs10["date"]
        st.markdown("---")
        st.markdown("**Análisis de Bonos del Tesoro (US10Y)**")
        if bps > 0:
            st.markdown(
                f"La tasa del bono del Tesoro a 10 años subió **{bps:+d} bps** hasta **{val:.2f}%** "
                f"(última lectura FRED: {date_str}). "
                f"Un alza en el US10Y eleva la tasa de descuento global, **presionando a la baja el valor "
                f"presente de los flujos de caja** de las acciones chilenas — especialmente utilities "
                f"reguladas (ENELAM, COLBUN) y real estate (MALLPLAZA, PARAUCO). "
                f"El spread con el bono chileno se comprime, reduciendo el atractivo relativo del IPSA frente "
                f"a renta fija en dólares."
            )
        elif bps < 0:
            st.markdown(
                f"La tasa del bono del Tesoro a 10 años cayó **{bps:+d} bps** hasta **{val:.2f}%** "
                f"(última lectura FRED: {date_str}). "
                f"Una baja en el US10Y **mejora el apetito por riesgo globalmente** y libera presión sobre "
                f"los múltiplos del IPSA — los flujos de caja futuros se descuentan a tasas menores, "
                f"elevando el valor presente de las acciones. Contexto favorable para posiciones en utilities "
                f"y acciones con dividendos estables."
            )
        else:
            st.markdown(
                f"El bono del Tesoro a 10 años se mantiene estable en **{val:.2f}%** "
                f"(última lectura FRED: {date_str}). "
                f"Sin variación en la tasa de descuento global — señal neutra para el IPSA."
            )

        # Strategy commentary
        st.markdown("**Comentario de Estrategia**")
        if bps > 5 or val > 4.5:
            st.markdown(
                f"> **Viento en contra.** El entorno de tasas globales presenta un desafío para el portafolio "
                f"familiar: con el US10Y en {val:.2f}%, la renta fija estadounidense compite directamente con "
                f"el retorno esperado del IPSA. Se recomienda priorizar posiciones con catalizadores "
                f"idiosincráticos fuertes (commodities, exportadoras beneficiadas por CLP débil) y reducir "
                f"exposición a sectores sensibles a tasas (utilities reguladas, retail endeudado)."
            )
        elif bps < -5 or val < 3.5:
            st.markdown(
                f"> **Viento a favor.** Con el US10Y bajando a {val:.2f}%, el contexto global favorece "
                f"el riesgo en mercados emergentes. El IPSA se beneficia del rebalanceo hacia activos de mayor "
                f"rendimiento. Momento oportuno para mantener o incrementar exposición en acciones con "
                f"dividendos consistentes y alto beta al ciclo económico chileno."
            )
        else:
            st.markdown(
                f"> **Entorno neutral.** El US10Y en {val:.2f}% no genera presiones netas sobre el portafolio. "
                f"Los retornos del IPSA dependerán principalmente de fundamentos locales: precio del cobre, "
                f"tipo de cambio y expectativas de la actividad económica en Chile."
            )

    st.divider()

    # ── Narrative ─────────────────────────────────────────────────────────────
    st.markdown("### Market Context & Chilean Bell")
    bullets = macro_narrative(macro)
    if bullets:
        for b in bullets:
            st.markdown(f"- {b}")
    else:
        st.info("Macro data unavailable. Use 'Refresh Market Data' in the sidebar.")

    st.divider()

    # ── Portfolio holdings watch ───────────────────────────────────────────────
    st.markdown("### Holdings Watch — Key Levels & Overnight Context")

    if not holdings:
        st.info("No holdings to brief on. Add positions in 'Manage Holdings'.")
    else:
        # Group multiple purchases of the same ticker into one row
        grouped: dict = {}
        for h in holdings:
            t = h["ticker"]
            if t not in grouped:
                grouped[t] = {
                    "ticker":       t,
                    "company_name": h["company_name"],
                    "quantity":     h["quantity"],
                    "cost_basis":   h["quantity"] * h["buy_price"],
                    "earliest_date": h["buy_date"],
                }
            else:
                grouped[t]["quantity"]   += h["quantity"]
                grouped[t]["cost_basis"] += h["quantity"] * h["buy_price"]
                if h["buy_date"] < grouped[t]["earliest_date"]:
                    grouped[t]["earliest_date"] = h["buy_date"]

        for ticker, g in grouped.items():
            short_tkr  = ticker.replace(".SN", "")
            pd_data    = prices.get(ticker, {})
            price      = pd_data.get("price")
            ts         = pd_data.get("timestamp")

            if not price:
                st.warning(
                    f"**{short_tkr}** — price unavailable "
                    f"(last verified: {ts_str(ts)}). Error: {pd_data.get('error', 'Unknown')}"
                )
                continue

            qty        = g["quantity"]
            cost_basis = g["cost_basis"]
            avg_cost   = cost_basis / qty if qty else 0
            mkt_value  = qty * price
            gain_clp   = mkt_value - cost_basis
            gain_pct   = (gain_clp / cost_basis * 100) if cost_basis else 0

            hist     = df_.get_historical_data(ticker, "1y")
            sma_data = calc_sma_signals(hist)
            sr_data  = calc_support_resistance(hist)
            chg_pct  = pd_data.get("change_pct", 0) or 0

            with st.expander(
                f"{short_tkr} — {g['company_name']} | "
                f"Price: {price:,.1f} CLP ({chg_pct:+.2f}%) | "
                f"P&L: {fmt_pct(gain_pct)}",
                expanded=False,
            ):
                b1, b2 = st.columns(2)

                with b1:
                    st.markdown(f"**Current Price:** {price:,.2f} CLP")
                    st.caption(f"Last verified: {ts_str(ts)}")
                    st.markdown(f"**Day Change:** {chg_pct:+.2f}%")
                    st.markdown(f"**Avg Cost:** {avg_cost:,.2f} CLP | **Qty:** {qty:,.0f}")
                    st.markdown(
                        f"**Holding P&L:** {fmt_clp(gain_clp)} ({fmt_pct(gain_pct)})"
                    )

                    # IPSA alpha since earliest buy
                    ipsa_ret = df_.get_ipsa_return_since(g["earliest_date"])
                    alpha    = calc_alpha(gain_pct, ipsa_ret)
                    if alpha is not None:
                        st.markdown(
                            f"**Alpha vs IPSA (since {g['earliest_date']}):** {fmt_pct(alpha)}"
                        )

                with b2:
                    if sma_data.get("signal"):
                        label = SIGNAL_LABELS.get(sma_data["signal"], sma_data["signal"])
                        st.markdown(f"**Trend Signal:** {label}")

                    if sr_data.get("support"):
                        st.markdown(f"**Support:** {sr_data['support']:,.1f} CLP")
                    if sr_data.get("resistance"):
                        st.markdown(f"**Resistance:** {sr_data['resistance']:,.1f} CLP")

                    cmf_url = (
                        f"https://www.cmfchile.cl/sitio/aplic/serdoc/ver_sgd.php"
                        f"?s=emiso&q={short_tkr}"
                    )
                    st.markdown(f"[CMF Filings]({cmf_url})")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — CHARTS
# ══════════════════════════════════════════════════════════════════════════════

with tab_charts:
    st.subheader("Price Charts")

    if not holdings:
        st.info("Add holdings to view charts.")
    else:
        # Ticker selector + period
        c_sel, c_period = st.columns([2, 1])

        with c_sel:
            chart_options = [
                f"{h['ticker'].replace('.SN','')} — {h['company_name']}"
                for h in holdings
            ]
            chart_choice = st.selectbox("Select holding", chart_options)

        with c_period:
            period_map = {
                "1 Month":  "1mo",
                "3 Months": "3mo",
                "6 Months": "6mo",
                "1 Year":   "1y",
                "2 Years":  "2y",
            }
            period_label  = st.selectbox("Period", list(period_map.keys()), index=3)
            period_code   = period_map[period_label]

        # Resolve selected holding
        chart_short   = chart_choice.split(" — ")[0]
        chart_ticker  = chart_short + ".SN"
        chart_holding = next((h for h in holdings if h["ticker"] == chart_ticker), None)

        hist = df_.get_historical_data(chart_ticker, period_code)

        if hist.empty:
            st.error(f"No chart data available for {chart_ticker}. Yahoo Finance may not have this ticker.")
        else:
            # ── Candlestick + SMA ─────────────────────────────────────────────
            fig = go.Figure()

            fig.add_trace(go.Candlestick(
                x=hist.index,
                open=hist["Open"],
                high=hist["High"],
                low=hist["Low"],
                close=hist["Close"],
                name=chart_short,
                increasing_line_color="#2ECC71",
                decreasing_line_color="#E74C3C",
                increasing_fillcolor="#2ECC71",
                decreasing_fillcolor="#E74C3C",
            ))

            if len(hist) >= 50:
                sma50 = hist["Close"].rolling(50).mean()
                fig.add_trace(go.Scatter(
                    x=hist.index, y=sma50,
                    mode="lines", name="50-SMA",
                    line=dict(color="#F39C12", width=1.8),
                ))

            if len(hist) >= 200:
                sma200 = hist["Close"].rolling(200).mean()
                fig.add_trace(go.Scatter(
                    x=hist.index, y=sma200,
                    mode="lines", name="200-SMA",
                    line=dict(color="#9B59B6", width=1.8),
                ))

            if chart_holding:
                fig.add_hline(
                    y=chart_holding["buy_price"],
                    line_dash="dash",
                    line_color="rgba(255,255,255,0.6)",
                    annotation_text=f"Buy: {chart_holding['buy_price']:,.1f}",
                    annotation_position="bottom right",
                    annotation_font_color="rgba(255,255,255,0.8)",
                )

            fig.update_layout(
                title=f"{chart_ticker} — Candlestick with 50/200-SMA",
                yaxis_title="Price (CLP)",
                template="plotly_dark",
                height=520,
                xaxis_rangeslider_visible=False,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(t=60, b=20, l=10, r=10),
            )

            st.plotly_chart(fig, use_container_width=True)

            # ── Volume bar chart ──────────────────────────────────────────────
            vol_colors = [
                "#2ECC71" if c >= o else "#E74C3C"
                for c, o in zip(hist["Close"], hist["Open"])
            ]
            fig_vol = go.Figure(go.Bar(
                x=hist.index,
                y=hist["Volume"],
                marker_color=vol_colors,
                name="Volume",
            ))
            fig_vol.update_layout(
                title="Volume",
                template="plotly_dark",
                height=180,
                margin=dict(t=30, b=20, l=10, r=10),
                xaxis_rangeslider_visible=False,
                showlegend=False,
            )
            st.plotly_chart(fig_vol, use_container_width=True)

            # ── SMA signal summary ────────────────────────────────────────────
            sma_data = calc_sma_signals(hist)
            sr_data  = calc_support_resistance(hist)

            c_sma, c_sr = st.columns(2)
            with c_sma:
                st.markdown("**SMA Summary**")
                if sma_data["sma50"]:
                    st.write(f"50-SMA: {sma_data['sma50']:,.2f} CLP")
                if sma_data["sma200"]:
                    st.write(f"200-SMA: {sma_data['sma200']:,.2f} CLP")
                if sma_data["stretch_pct"] is not None:
                    st.write(f"Stretch from 200-SMA: {fmt_pct(sma_data['stretch_pct'])}")
                if sma_data["signal"]:
                    st.write(f"Signal: **{SIGNAL_LABELS.get(sma_data['signal'], sma_data['signal'])}**")

            with c_sr:
                st.markdown("**Support / Resistance (60-day)**")
                if sr_data["support"]:
                    st.write(f"Support:    {sr_data['support']:,.2f} CLP")
                if sr_data["resistance"]:
                    st.write(f"Resistance: {sr_data['resistance']:,.2f} CLP")
                if sr_data["period_low"]:
                    st.write(
                        f"Period range: {sr_data['period_low']:,.1f} — "
                        f"{sr_data['period_high']:,.1f} CLP"
                    )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — PERFORMANCE (Equity Curve + TWR Alpha vs IPSA)
# ══════════════════════════════════════════════════════════════════════════════

with tab_performance:
    st.subheader("Performance Tracker — TWR & Alpha vs IPSA")
    st.caption(
        "Base 100 desde T=0 (primera compra). "
        "La Caja es invisible: el B100 solo se mueve cuando cambian los precios de las acciones. "
        "Precios ajustados por dividendos y splits."
    )

    # ── 1. Aportes de Capital ─────────────────────────────────────────────────
    with st.expander("Registrar Aportes de Capital", expanded=False):
        st.caption(
            "Ingresa aquí cada vez que depositaste dinero nuevo en tu portafolio. "
            "Esto permite al sistema calcular el rendimiento TWR correctamente, "
            "separando el crecimiento real de los depósitos."
        )

        # Formulario para agregar aporte
        fa, fb, fc, fd = st.columns([2, 2, 3, 1])
        with fa:
            cf_date = st.date_input(
                "Fecha del aporte",
                value=date.today(),
                key="cf_date",
            )
        with fb:
            cf_amount = st.number_input(
                "Monto (CLP)",
                min_value=1.0,
                step=100_000.0,
                format="%.0f",
                key="cf_amount",
                help="Positivo = aporte, negativo = retiro de capital.",
            )
        with fc:
            cf_notes = st.text_input("Nota (opcional)", key="cf_notes", placeholder="ej: Sueldo marzo")
        with fd:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Agregar", key="btn_add_cf", type="primary", use_container_width=True):
                db.add_capital_flow(str(cf_date), float(cf_amount), cf_notes)
                st.success(f"Aporte de {fmt_clp(cf_amount)} registrado el {cf_date}.")
                st.rerun()

        # Tabla de aportes existentes
        capital_flows = db.get_capital_flows()
        if capital_flows:
            st.markdown("**Aportes registrados:**")
            for flow in reversed(capital_flows):  # newest first
                col_d, col_a, col_n, col_del = st.columns([2, 2, 4, 1])
                col_d.write(flow["date"])
                col_a.write(fmt_clp(flow["amount"]))
                col_n.write(flow["notes"] or "—")
                if col_del.button("✕", key=f"del_cf_{flow['id']}", help="Eliminar este aporte"):
                    db.delete_capital_flow(flow["id"])
                    st.rerun()
        else:
            st.info("Sin aportes registrados. Agrega al menos uno para activar el TWR real.")

    st.divider()

    # ── 2. Controles de Backfill ──────────────────────────────────────────────
    col_b1, col_b2, col_status = st.columns([1, 1, 2])

    with col_b1:
        run_incr = st.button(
            "Actualizar Historial",
            type="primary",
            use_container_width=True,
            help="Agrega solo las fechas que faltan (incremental, rápido).",
        )
    with col_b2:
        run_full = st.button(
            "Recalcular Todo",
            use_container_width=True,
            help="Borra y reconstruye todo el historial desde la primera transacción. "
                 "Necesario después de agregar posiciones retroactivas o aportes de capital.",
        )

    # Mensajes persistidos con session_state (sobreviven al st.rerun)
    if "_perf_msg" in st.session_state:
        msg, level = st.session_state.pop("_perf_msg")
        getattr(col_status, level)(msg)

    if run_full:
        try:
            with col_status:
                with st.spinner("Borrando y recalculando todo el historial..."):
                    n_new = pe.clear_and_rebuild()
            st.session_state["_perf_msg"] = (
                f"Recálculo completo: {n_new} días calculados.", "success"
            )
        except Exception as exc:
            st.session_state["_perf_msg"] = (f"Error en recálculo: {exc}", "error")
        st.rerun()
    elif run_incr:
        try:
            with col_status:
                with st.spinner("Calculando fechas faltantes..."):
                    n_new = pe.run_backfill()
            msg = f"{n_new} día(s) nuevo(s) calculados." if n_new > 0 else "Historial al día."
            st.session_state["_perf_msg"] = (msg, "success" if n_new > 0 else "info")
        except Exception as exc:
            st.session_state["_perf_msg"] = (f"Error: {exc}", "error")
        st.rerun()
    else:
        # Auto-backfill silencioso: si falta hoy o ayer
        _existing = db.get_daily_performance_dates()
        _today_iso = date.today().isoformat()
        _yday_iso  = (date.today() - timedelta(days=1)).isoformat()
        if _today_iso not in _existing or _yday_iso not in _existing:
            with col_status:
                with st.spinner("Completando datos recientes..."):
                    _n = pe.run_backfill()
                if _n > 0:
                    st.caption(f"Auto-completado: {_n} día(s).")

    st.divider()

    # ── 3. Cargar datos y computar TWR ────────────────────────────────────────
    history       = db.get_daily_performance()
    capital_flows = db.get_capital_flows()

    # Aportes para marcadores del gráfico (solo visual, no afectan el TWR)
    flows_by_date: dict = {}
    for flow in capital_flows:
        flows_by_date[flow["date"]] = flows_by_date.get(flow["date"], 0.0) + flow["amount"]

    # ── Auto-detección de compras (BUY) desde el log de transacciones ─────────
    # El B100 usa equity_value; las compras se neutralizan automáticamente
    # con qty × buy_price como proxy del aporte de equity en cada fecha de compra.
    txns_raw = db.get_all_transactions_raw()
    buy_flows_by_date: dict = {}
    for t in txns_raw:
        if t["action"] == "BUY" and t["price"] is not None and t["quantity"] is not None:
            d = t["date"][:10]
            buy_flows_by_date[d] = buy_flows_by_date.get(d, 0.0) + float(t["quantity"]) * float(t["price"])

    if len(history) < 2:
        st.info(
            "Sin suficientes datos. "
            "Agrega posiciones en 'Manage Holdings' y pulsa 'Actualizar Historial'."
        )
    else:
        df_hist = pd.DataFrame(history)
        df_hist["date"] = pd.to_datetime(df_hist["date"])
        df_hist = df_hist.sort_values("date").reset_index(drop=True)
        # Filtrar solo días con equity > 0 (la Caja sola no genera rendimiento)
        df_hist = df_hist[df_hist["equity_value"] > 0].reset_index(drop=True)

        if df_hist.empty:
            st.info("Sin posiciones con equity > 0. Agrega holdings primero.")
        else:
            # ── B100 basado en equity_value + neutralización automática de BUYs ─
            df_hist["twr_b100"] = _compute_twr_b100(df_hist, buy_flows_by_date)

            # ── IPSA Base 100 desde T=0 ───────────────────────────────────────
            # Gracias al bfill en el motor, ipsa_close[0] debe ser no-None si hay datos IPSA
            t0_ipsa = df_hist["ipsa_close"].iloc[0]
            has_ipsa = pd.notna(t0_ipsa)

            if has_ipsa:
                # Ambas líneas arrancan en 100 en T=0 (primera compra)
                df_hist["ipsa_b100"] = (df_hist["ipsa_close"] / t0_ipsa) * 100
                # Alpha = diferencia directa (ambos en Base 100 desde el mismo T=0)
                df_hist["alpha"]     = df_hist["twr_b100"] - df_hist["ipsa_b100"]
            else:
                # Fallback: usar primera fecha disponible de IPSA
                first_ipsa_idx = df_hist["ipsa_close"].first_valid_index()
                if first_ipsa_idx is not None:
                    ipsa_base    = df_hist.loc[first_ipsa_idx, "ipsa_close"]
                    twr_at_align = df_hist.loc[first_ipsa_idx, "twr_b100"]
                    df_hist["ipsa_b100"] = np.where(
                        df_hist["ipsa_close"].notna(),
                        (df_hist["ipsa_close"] / ipsa_base) * twr_at_align,
                        np.nan,
                    )
                    df_hist["alpha"] = df_hist["twr_b100"] - df_hist["ipsa_b100"]
                    has_ipsa = True  # datos disponibles desde una fecha posterior a T=0
                    st.caption(
                        f"Nota: datos IPSA disponibles desde "
                        f"{df_hist.loc[first_ipsa_idx, 'date'].strftime('%Y-%m-%d')} "
                        f"(Yahoo Finance no tiene historial previo). "
                        f"Ambas curvas se alinean en esa fecha."
                    )

            # ── Gráfico dual-axis ─────────────────────────────────────────────
            fig_perf = go.Figure()

            # NAV absoluto (eje izquierdo)
            fig_perf.add_trace(go.Scatter(
                x=df_hist["date"],
                y=df_hist["nav"],
                mode="lines",
                name="NAV Total (CLP)",
                line=dict(color="#2ECC71", width=2.5),
                yaxis="y1",
                hovertemplate="%{x|%Y-%m-%d}<br>NAV: $%{y:,.0f}<extra></extra>",
            ))

            # TWR Portafolio Base 100 (eje derecho)
            fig_perf.add_trace(go.Scatter(
                x=df_hist["date"],
                y=df_hist["twr_b100"],
                mode="lines",
                name="Portafolio TWR (B100)",
                line=dict(color="#27AE60", width=2, dash="dot"),
                yaxis="y2",
                hovertemplate="%{x|%Y-%m-%d}<br>TWR: %{y:.2f}<extra></extra>",
            ))

            if has_ipsa:
                # IPSA Base 100
                fig_perf.add_trace(go.Scatter(
                    x=df_hist["date"],
                    y=df_hist["ipsa_b100"],
                    mode="lines",
                    name="IPSA (B100)",
                    line=dict(color="#F39C12", width=2, dash="dash"),
                    yaxis="y2",
                    hovertemplate="%{x|%Y-%m-%d}<br>IPSA: %{y:.2f}<extra></extra>",
                ))

                # Alpha (área violeta)
                fig_perf.add_trace(go.Scatter(
                    x=df_hist["date"],
                    y=df_hist["alpha"],
                    mode="lines",
                    name="Alpha (TWR − IPSA)",
                    line=dict(color="#9B59B6", width=1.2),
                    fill="tozeroy",
                    fillcolor="rgba(155,89,182,0.12)",
                    yaxis="y2",
                    hovertemplate="%{x|%Y-%m-%d}<br>Alpha: %{y:+.2f} pts<extra></extra>",
                ))

            # Línea de referencia Base 100
            fig_perf.add_hline(
                y=100,
                line_dash="dot",
                line_color="rgba(255,255,255,0.20)",
                yref="y2",
                annotation_text="Base 100",
                annotation_font_color="rgba(255,255,255,0.35)",
                annotation_position="left",
            )

            # Marcadores verticales de aportes
            # add_vline con annotation falla en figuras dual-axis (Plotly bug con strings x);
            # usamos add_shape + add_annotation por separado.
            for d_str, amt in flows_by_date.items():
                if amt > 0:
                    fig_perf.add_shape(
                        type="line",
                        x0=d_str, x1=d_str,
                        y0=0, y1=1,
                        xref="x", yref="paper",
                        line=dict(dash="dot", color="rgba(52,152,219,0.5)", width=1.5),
                    )
                    fig_perf.add_annotation(
                        x=d_str,
                        y=1,
                        xref="x", yref="paper",
                        text=f"Aporte {fmt_clp(amt)}",
                        showarrow=False,
                        font=dict(size=10, color="rgba(52,152,219,0.9)"),
                        xanchor="left",
                        yanchor="top",
                    )

            fig_perf.update_layout(
                title=f"Curva de Patrimonio TWR — Base 100 desde {df_hist['date'].iloc[0].strftime('%Y-%m-%d')}",
                template="plotly_dark",
                height=560,
                margin=dict(t=70, b=30, l=10, r=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                hovermode="x unified",
                yaxis=dict(
                    title="NAV (CLP)",
                    tickformat="$,.0f",
                    side="left",
                    showgrid=True,
                    gridcolor="rgba(255,255,255,0.06)",
                ),
                yaxis2=dict(
                    title="Base 100 (TWR)",
                    overlaying="y",
                    side="right",
                    showgrid=False,
                    zeroline=False,
                ),
            )

            st.plotly_chart(fig_perf, use_container_width=True)

            # ── KPIs ──────────────────────────────────────────────────────────
            st.markdown("### Resumen de Rendimiento")

            twr_return      = df_hist["twr_b100"].iloc[-1] - 100
            total_days      = (df_hist["date"].iloc[-1] - df_hist["date"].iloc[0]).days
            total_deposited = sum(v for v in flows_by_date.values() if v > 0)

            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric(
                "Retorno TWR",
                fmt_pct(twr_return),
                help="Time-Weighted Return ajustado por aportes. Estándar CFA/GIPS.",
            )
            k2.metric("Días en mercado", str(total_days))
            k3.metric(
                "Capital aportado",
                fmt_clp(total_deposited) if total_deposited else "No registrado",
                help="Suma de aportes registrados en el tab 'Registrar Aportes de Capital'.",
            )

            if has_ipsa and "ipsa_b100" in df_hist.columns:
                ipsa_last  = df_hist["ipsa_b100"].dropna()
                ipsa_ret   = ipsa_last.iloc[-1] - 100 if not ipsa_last.empty else None
                alpha_last = df_hist["alpha"].dropna()
                alpha_val  = alpha_last.iloc[-1] if not alpha_last.empty else None

                k4.metric("Retorno IPSA", fmt_pct(ipsa_ret) if ipsa_ret is not None else "N/A")
                if alpha_val is not None:
                    k5.metric(
                        "Alpha generado",
                        f"{alpha_val:+.2f} pts",
                        delta="sobre IPSA" if alpha_val >= 0 else "bajo IPSA",
                        delta_color="normal" if alpha_val >= 0 else "inverse",
                    )
                else:
                    k5.metric("Alpha generado", "N/A")
            else:
                k4.metric("Retorno IPSA", "Sin datos IPSA")
                k5.metric("Alpha generado", "Sin datos IPSA")

            st.divider()

            # ── Metodología ───────────────────────────────────────────────────
            with st.expander("Metodología — ¿Cómo funciona el B100?", expanded=False):
                st.markdown(f"""
**T=0:** `{df_hist['date'].iloc[0].strftime('%Y-%m-%d')}` (primera compra registrada)

**Portafolio B100 — Unit Pricing sobre equity_value:**
- La **Caja es invisible**: el B100 solo sube o baja cuando cambian los precios de las acciones.
- `valor_cuota[d] = equity_value[d] / cuotas_emitidas`
- Cuando se compran acciones nuevas (BUY), se emiten cuotas al precio del día anterior → el valor cuota no cambia ese día.
- Registrar caja, depositar o retirar efectivo **no afecta** el B100.

**IPSA Base 100:** `(Precio_IPSA[d] / Precio_IPSA[T=0]) × 100`
- Ambas líneas parten de 100 en T=0. **Alpha:** `B100_Portfolio − IPSA_B100`.

**Dividendos:** incluidos vía precios ajustados de Yahoo Finance (`auto_adjust=True`).

**Aportes registrados:** marcadores azules verticales (solo referencia visual, no afectan el cálculo).
                """)

            # ── Tabla histórica ───────────────────────────────────────────────
            with st.expander("Registro Histórico de Cierres Diarios", expanded=False):
                cols_base  = ["date", "nav", "equity_value", "cash", "twr_b100", "ipsa_close"]
                cols_extra = (["ipsa_b100", "alpha"] if has_ipsa and "ipsa_b100" in df_hist.columns else [])
                df_disp = df_hist[cols_base + cols_extra].copy()

                names_base  = ["Fecha", "NAV (CLP)", "Acciones", "Caja", "TWR B100", "IPSA Cierre"]
                names_extra = (["IPSA B100", "Alpha"] if cols_extra else [])
                df_disp.columns = names_base + names_extra

                df_disp["Fecha"] = df_disp["Fecha"].dt.strftime("%Y-%m-%d")
                df_disp = df_disp.iloc[::-1].reset_index(drop=True)

                fmt_map = {
                    "NAV (CLP)":   lambda x: fmt_clp(x) if pd.notna(x) else "—",
                    "Acciones":    lambda x: fmt_clp(x) if pd.notna(x) else "—",
                    "Caja":        lambda x: fmt_clp(x) if pd.notna(x) else "—",
                    "TWR B100":    lambda x: f"{x:.2f}" if pd.notna(x) else "—",
                    "IPSA Cierre": lambda x: f"{x:,.2f}" if pd.notna(x) else "—",
                }
                if cols_extra:
                    fmt_map["IPSA B100"] = lambda x: f"{x:.2f}" if pd.notna(x) else "—"
                    fmt_map["Alpha"]     = lambda x: f"{x:+.2f}" if pd.notna(x) else "—"

                st.dataframe(df_disp.style.format(fmt_map), use_container_width=True, height=320)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — NOTICIAS
# ══════════════════════════════════════════════════════════════════════════════

EMOL_RSS_URL = "https://www.emol.com/rss/economia.xml"
ALERT_KEYWORDS = ["dividendo", "hecho relevante", "cmf", "resultados"]
HIGHLIGHT_KEYWORDS = ["ipsa", "bolsa de santiago"]

_ALL_TICKERS_SHORT = [t.replace(".SN", "") for t in IPSA_CONSTITUENTS.keys()]


@st.cache_data(ttl=900)
def _fetch_emol_news() -> list[dict]:
    """Fetch and parse Emol Economía RSS feed. Returns list of article dicts."""
    try:
        feed = feedparser.parse(EMOL_RSS_URL)
        articles = []
        for entry in feed.entries:
            published_ts = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published_ts = datetime(*entry.published_parsed[:6], tzinfo=pytz.utc)
            articles.append({
                "title":     getattr(entry, "title", "Sin título"),
                "link":      getattr(entry, "link", "#"),
                "summary":   getattr(entry, "summary", ""),
                "published": published_ts,
                "source":    "Emol Economía",
            })
        return articles
    except Exception:
        return []


@st.cache_data(ttl=900)
def _fetch_yf_news(ticker: str) -> list[dict]:
    """Fetch Yahoo Finance news for a single .SN ticker."""
    import yfinance as yf
    try:
        news_raw = yf.Ticker(ticker).news or []
        articles = []
        for item in news_raw:
            content = item.get("content", {})
            pub_ts = None
            pub_raw = content.get("pubDate") or item.get("providerPublishTime")
            if isinstance(pub_raw, int):
                pub_ts = datetime.fromtimestamp(pub_raw, tz=pytz.utc)
            elif isinstance(pub_raw, str):
                try:
                    pub_ts = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                except Exception:
                    pass
            articles.append({
                "title":     content.get("title") or item.get("title", "Sin título"),
                "link":      content.get("canonicalUrl", {}).get("url") or item.get("link", "#"),
                "summary":   content.get("summary") or "",
                "published": pub_ts,
                "source":    f"Yahoo Finance ({ticker})",
            })
        return articles
    except Exception:
        return []


def _classify_news(article: dict, portfolio_tickers_short: list[str]) -> tuple[bool, bool, bool]:
    """
    Returns (is_alert, is_highlighted, mentions_portfolio).
    - is_alert: title/summary contains alert keywords
    - is_highlighted: mentions IPSA, Bolsa de Santiago, or a portfolio ticker
    - mentions_portfolio: mentions any portfolio ticker
    """
    text = (article["title"] + " " + article["summary"]).lower()
    is_alert = any(kw in text for kw in ALERT_KEYWORDS)
    is_highlighted = any(kw in text for kw in HIGHLIGHT_KEYWORDS)
    mentions_portfolio = any(t.lower() in text for t in portfolio_tickers_short)
    is_highlighted = is_highlighted or mentions_portfolio
    return is_alert, is_highlighted, mentions_portfolio


def _fmt_published(ts) -> str:
    if ts is None:
        return "Hora desconocida"
    chile_tz_local = pytz.timezone(CHILE_TZ)
    local_ts = ts.astimezone(chile_tz_local)
    return local_ts.strftime("%d/%m/%Y %H:%M CLT")


with tab_noticias:
    st.subheader("Noticias del Mercado")
    st.caption("Yahoo Finance (por acción) + Emol Economía (mercado local). Actualización automática cada 15 min.")

    # ── Controles superiores ──────────────────────────────────────────────────
    col_sel, col_btn, col_ts = st.columns([3, 1, 3])

    with col_sel:
        holdings_for_news = db.get_holdings()
        portfolio_tickers = [h["ticker"] for h in holdings_for_news] if holdings_for_news else []
        ticker_options    = ["Ver Todas (Mercado Local)"] + portfolio_tickers
        selected_ticker   = st.selectbox(
            "Filtrar por acción",
            options=ticker_options,
            key="news_ticker_select",
        )

    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Refrescar Noticias", key="btn_refresh_news", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # ── Carga de noticias ─────────────────────────────────────────────────────
    portfolio_tickers_short = [t.replace(".SN", "") for t in portfolio_tickers]

    if selected_ticker == "Ver Todas (Mercado Local)":
        # Emol feed + Yahoo para todos los holdings
        emol_articles = _fetch_emol_news()
        yf_articles: list[dict] = []
        for t in portfolio_tickers:
            yf_articles.extend(_fetch_yf_news(t))
        all_articles = yf_articles + emol_articles
        news_source_ok = True
        if not emol_articles and not yf_articles:
            news_source_ok = False
    else:
        emol_articles = _fetch_emol_news()
        yf_articles   = _fetch_yf_news(selected_ticker)
        all_articles  = yf_articles + emol_articles
        news_source_ok = True
        if not emol_articles and not yf_articles:
            news_source_ok = False

    with col_ts:
        st.markdown("<br>", unsafe_allow_html=True)
        if all_articles:
            st.caption(f"Última carga: {_fmt_published(datetime.now(pytz.timezone(CHILE_TZ)))}")

    st.divider()

    if not news_source_ok:
        st.warning("Servicio de noticias temporalmente fuera de línea. Intente de nuevo en unos minutos.")
    elif not all_articles:
        st.info("No se encontraron noticias para la selección actual.")
    else:
        # Ordenar por fecha descendente (None al final)
        all_articles.sort(
            key=lambda a: a["published"] or datetime.min.replace(tzinfo=pytz.utc),
            reverse=True,
        )

        st.markdown(f"**{len(all_articles)} noticias encontradas**")

        for article in all_articles:
            is_alert, is_highlighted, mentions_portfolio = _classify_news(article, portfolio_tickers_short)

            # Construir etiquetas
            badges = ""
            if is_alert:
                badges += " 🔴 **[ALERTA]**"
            if is_highlighted and not is_alert:
                badges += " 🟡 **[IMPORTANTE]**"
            if mentions_portfolio:
                matched = [t for t in portfolio_tickers_short if t.lower() in (article["title"] + article["summary"]).lower()]
                if matched:
                    badges += f" `{'  '.join(matched)}`"

            with st.container(border=True):
                # Título + badges
                title_safe = article["title"].replace("[", "\\[").replace("]", "\\]")
                link = article["link"] or "#"
                st.markdown(f"**[{title_safe}]({link})**{badges}", unsafe_allow_html=False)

                # Meta: fuente + hora
                meta_cols = st.columns([3, 2])
                meta_cols[0].caption(f"📰 {article['source']}")
                meta_cols[1].caption(f"🕐 {_fmt_published(article['published'])}")

                # Resumen (si existe y no está vacío)
                summary = article.get("summary", "").strip()
                if summary:
                    # Truncar a 300 caracteres para no saturar la pantalla
                    display_summary = summary[:300] + ("…" if len(summary) > 300 else "")
                    st.caption(display_summary)


# ── Footer ────────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    "Data source: Yahoo Finance. "
    "Prices for Chilean .SN tickers are denominated in CLP. "
    "If a price or index level is unavailable, the last verified timestamp is shown. "
    "This tool is for informational purposes only — not financial advice."
)
