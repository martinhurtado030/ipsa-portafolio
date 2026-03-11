# IPSA Portfolio Manager — Project Context

## What this project is
A Streamlit web application for managing and analyzing a Chilean equity portfolio.
Strictly focused on the Santiago Stock Exchange (SSE). Data source: Yahoo Finance (`.SN` tickers, prices in CLP).
Multi-user: each user has an isolated portfolio. Auth + database via Supabase.

## Live URL
**https://ipsa-portafolio.streamlit.app**

## GitHub repo
**https://github.com/martinhurtado030/ipsa-portafolio**

## How to deploy updates
```bash
cd /Users/martinhurtado/Desktop/claudecode/ipsa_portfolio
git add <files>
git commit -m "descripcion"
git push
# Streamlit Cloud redespliega automáticamente en ~1 minuto
```

## How to run locally
```bash
cd /Users/martinhurtado/Desktop/claudecode/ipsa_portfolio
python3 -m streamlit run app.py

# Background (survives terminal close)
nohup python3 -m streamlit run app.py > streamlit.log 2>&1 &
pkill -f "streamlit run app.py"  # para detenerlo
```

## File structure
| File | Purpose |
|---|---|
| `app.py` | Main Streamlit UI — 7 tabs + auth gate |
| `config.py` | IPSA tickers, sector map, macro symbols, constants |
| `database.py` | Supabase CRUD — todas las funciones aceptan `user_id` kwarg |
| `supabase_client.py` | Clientes Supabase: `get_anon_client()` (auth) + `get_admin_client()` (datos) |
| `data_fetcher.py` | Yahoo Finance + FRED calls con `@st.cache_data` |
| `analysis.py` | Cálculos puros: P&L, alpha, SMA, RSI, zonas de memoria, volumen, valuación |
| `performance_engine.py` | Backfill automático NAV diario desde transactions |
| `supabase_schema.sql` | Schema SQL — ya ejecutado en Supabase (no re-ejecutar) |
| `.streamlit/secrets.toml` | Credenciales Supabase — NO commitear, en .gitignore |
| `requirements.txt` | Dependencias Python |

## App tabs
1. **Overview** — NAV, dos donuts de asignación (posición + sector), tabla holdings con P&L
2. **Manage Holdings** — Agregar/eliminar posiciones del IPSA, caja, log de transacciones
3. **Universal Sieve** — Análisis profundo por holding: P&L, alpha vs IPSA, señales SMA 50/200, RSI, Zonas de Memoria, Buy Zone alert, confirmación de volumen, P/E, links CMF
4. **Morning Briefing** — Macro global (Copper/USD-CLP/S&P500/IPSA) + FRED snapshot + análisis bonos US10Y + holdings watch agrupado por ticker
5. **Charts** — Candlestick + SMAs + volumen + línea de precio de compra
6. **Performance** — Curva NAV (CLP) + TWR Base 100 vs IPSA rebased. Backfill incremental automático.
7. **Noticias** — Yahoo Finance news por holding + RSS Emol Economía. Filtro por ticker. Badges de alerta e importancia.

## Supabase
- **URL**: `https://swisiqrwsagusofozxed.supabase.co`
- **Anon key**: `[ver .streamlit/secrets.toml]`
- **Service key**: `[ver .streamlit/secrets.toml]`
- Secrets configurados en: Streamlit Cloud → App settings → Secrets
- Email confirmation: **desactivado** (Sign In/Providers → Email → Confirm email OFF)

## Supabase tables
| Table | Purpose |
|---|---|
| `holdings` | Posiciones abiertas (user_id scoped) |
| `cash_reserve` | Caja disponible — una fila por usuario |
| `transactions` | Log BUY / REMOVE / CASH_UPDATE |
| `capital_flows` | Aportes y retiros de capital |
| `daily_performance` | NAV diario — UNIQUE(user_id, date) |

## Auth architecture
- `get_anon_client()` → sign_in / sign_up (usa anon key)
- `get_admin_client()` → todas las operaciones de datos (usa service key, bypasses RLS)
- `st.session_state["user_id"]` y `["user_email"]` se setean al login
- Auth gate en app.py: si no hay `user_id` en session_state → muestra pantalla de login → `st.stop()`
- `database.py` resuelve user_id en orden: kwarg explícito → session_state → RuntimeError
- `performance_engine.py` pasa `user_id=user_id` (None por defecto, database.py lee session_state)

## Data & caching
- **Prices**: Yahoo Finance `.SN` → CLP via `fast_info.last_price` / `fast_info.previous_close`. Cache TTL: 5 min.
- **Macro (Yahoo)**: Copper (`HG=F`), USD/CLP (`CLP=X`), S&P 500 (`^GSPC`), IPSA (`^IPSA`). Cache TTL: 2 min.
- **Macro (FRED)**: DGS10, FEDFUNDS, IRSTCI01CLM156N (BCCh TPM), IRLTLT01CLM156N (Chile 10Y). Cache TTL: 1 hr.
- **Fundamentals** (P/E, P/B, dividends): Cache TTL: 1 hr.
- Manual refresh: botón "Refresh Market Data" en sidebar borra todo el cache.
- **FRED API key**: `6cd1756ce64d643f595980392cf50bc1`
- **Timestamps**: siempre convertidos a hora Chile (America/Santiago) antes de mostrar — el servidor Streamlit Cloud corre en UTC.

## Key design decisions
- **Price % change**: usar `fast_info.last_price` vs `fast_info.previous_close` — nunca `iloc[-1]` vs `iloc[-2]` (da 0% intraday).
- **Volume**: no se usa `fast_info`. Confirmación de volumen usa OHLCV histórico (`hist["Volume"]`).
- **Sector map**: `IPSA_SECTORS` en `config.py` mapea ticker corto → sector (sin sufijo `.SN`).
- **Dividend yield**: Yahoo Finance devuelve `dividendYield` para tickers `.SN` ya como porcentaje (0.56 = 0.56%) — NO multiplicar por 100.
- **Fair Value (P/E Relative)**: eliminado de Universal Sieve — no se usa en ningún ticker.
- **Morning Briefing holdings watch**: agrupa múltiples compras del mismo ticker en una sola fila (cantidad total, costo promedio ponderado, P&L consolidado, alpha desde la compra más antigua).

## IPSA constituents (34 tickers)
AGUAS-A, ANDINA-B, BESALCO, BSANTANDER, BCI, CHILE, CAP, CCU, CENCOSUD, CENCOMALLS,
CMPC, COLBUN, CONCHATORO, COPEC, ECL, ENELAM, ENTEL, FALABELLA, HABITAT, IAM, ILC,
ITAUCL, LTM, MALLPLAZA, PARAUCO, QUINENCO, RIPLEY, SALFACORP, SECURITY, SK, SMU,
SONDA, SQM-B, VAPORES

## FRED series used
| Series ID | Description | Frequency |
|---|---|---|
| `DGS10` | US 10-Year Treasury yield | Daily |
| `FEDFUNDS` | US Federal Funds Rate (effective) | Monthly |
| `IRSTCI01CLM156N` | BCCh short-term policy rate (proxy TPM) | Monthly |
| `IRLTLT01CLM156N` | Chile 10Y government bond yield | Monthly |

## Infrastructure
- **Streamlit Cloud**: app desplegada permanentemente en `ipsa-portafolio.streamlit.app`
- **GitHub**: repo `martinhurtado030/ipsa-portafolio` (público) — push = redeploy automático
- **ngrok** instalado en `/opt/homebrew/bin/ngrok` (alternativa local con URL pública temporal)

## Known limitations & decisions made
- **bolsadesantiago.com**: bloqueado por CAPTCHA Radware — no usar para precios.
- **TradingView**: devuelve 403 en todos los endpoints — no usable.
- **Data delay**: Yahoo Finance free tier tiene ~15 min de delay en horario de mercado.
- **FRED series `INTDSRCLM193N`**: NO existe — usar `IRSTCI01CLM156N` para tasa BCCh.
- **Supabase free tier**: límite de emails de confirmación por hora → email confirmation desactivado.

## Pending / potential future features
- [ ] Morning briefing automático por email (9:30 AM CLT)
- [ ] Auto-snapshot NAV al cierre de mercado
