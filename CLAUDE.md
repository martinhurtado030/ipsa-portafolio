# IPSA Portfolio Manager — Project Context

## What this project is
A local Streamlit web application for managing and analyzing a Chilean equity portfolio.
Strictly focused on the Santiago Stock Exchange (SSE). Data source: Yahoo Finance (`.SN` tickers, prices in CLP).

## How to run
```bash
# Local only
cd /Users/martinhurtado/Desktop/claudecode/ipsa_portfolio
python3 -m streamlit run app.py

# With public access (two Terminal tabs)
# Tab 1:
python3 -m streamlit run app.py --server.address 0.0.0.0
# Tab 2:
/opt/homebrew/bin/ngrok http 8501

# One-liner (app in background + ngrok in foreground)
cd /Users/martinhurtado/Desktop/claudecode/ipsa_portfolio && python3 -m streamlit run app.py --server.address 0.0.0.0 & sleep 3 && /opt/homebrew/bin/ngrok http 8501
```

## File structure
| File | Purpose |
|---|---|
| `app.py` | Main Streamlit UI — 6 tabs |
| `config.py` | IPSA tickers, sector map, macro symbols, constants |
| `database.py` | SQLite CRUD (holdings, cash, transactions) |
| `data_fetcher.py` | Yahoo Finance + FRED calls with `@st.cache_data` |
| `analysis.py` | Pure calculations: P&L, alpha, SMA, RSI, memory zones, volume confirmation, valuation |
| `performance_engine.py` | Backfill automático: reconstruye NAV diario desde transactions, guarda en daily_performance |
| `portfolio.db` | SQLite database (auto-created on first run, persists all data) |
| `requirements.txt` | Python dependencies |

## App tabs
1. **Overview** — NAV, two allocation donuts (por posición + por sector), holdings table with P&L
2. **Manage Holdings** — Add/remove positions from IPSA list, cash reserve, transaction log
3. **Universal Sieve** — Deep per-holding analysis: P&L, alpha vs IPSA, 50/200-SMA signals, RSI, Zonas de Memoria (S/R con ≥2 toques en 6M), Buy Zone alert (soporte + SMA200 + RSI<35), confirmación de volumen (>1.2x promedio 20s), P/E valuation, CMF links
4. **Morning Briefing** — Global Macro Signals (Copper/USD-CLP/S&P500/IPSA) + Snapshot Macroeconómico FRED (Fed Funds Rate, US Treasury 10Y, Tasa BCCh TPM, Bono Chile 10Y, Estatus de Riesgo) + análisis de bonos US10Y + comentario de estrategia + per-holding watch
5. **Charts** — Candlestick + SMA overlays + volume, buy price line
6. **Performance** — Equity curve (NAV diario en CLP, eje izq) + TWR Base 100 vs IPSA rebased (eje der). Método TWR neutraliza aportes de caja. Botón "Actualizar Historial" (incremental) y "Recalcular Todo" (borra y reconstruye). Precios ajustados por dividendos/splits. Alpha = TWR_portfolio − IPSA_rebased.

## Data & caching
- **Prices**: Yahoo Finance `.SN` tickers → CLP via `fast_info.last_price` / `fast_info.previous_close` (intraday vs prev close). Cache TTL: 5 min.
- **Macro (Yahoo)**: Copper (`HG=F`), USD/CLP (`CLP=X`), S&P 500 (`^GSPC`), IPSA (`^IPSA`) via `fast_info`. Cache TTL: 2 min.
- **Macro (FRED)**: DGS10, FEDFUNDS, IRSTCI01CLM156N (BCCh TPM), IRLTLT01CLM156N (Chile 10Y). Cache TTL: 1 hr.
- **Fundamentals** (P/E, P/B, dividends): Cache TTL: 1 hr.
- Manual refresh: "Refresh Market Data" button in sidebar clears all cache.
- **FRED API key**: `6cd1756ce64d643f595980392cf50bc1`

## Key design decisions
- **Price % change**: Always use `fast_info.last_price` vs `fast_info.previous_close` — never daily bar `iloc[-1]` vs `iloc[-2]` which returns 0% intraday.
- **Volume**: Not fetched from `fast_info` (removed). Volume confirmation uses historical OHLCV (`hist["Volume"]`).
- **Sector map**: `IPSA_SECTORS` in `config.py` maps short ticker → sector string (no `.SN` suffix).

## FRED series used
| Series ID | Description | Frequency |
|---|---|---|
| `DGS10` | US 10-Year Treasury yield | Daily |
| `FEDFUNDS` | US Federal Funds Rate (effective) | Monthly |
| `IRSTCI01CLM156N` | BCCh short-term policy rate (proxy TPM) | Monthly |
| `IRLTLT01CLM156N` | Chile 10Y government bond yield | Monthly |

## IPSA constituents (32 tickers)
AGUAS-A, BESALCO, BSANTANDER, BCI, CHILE, CAP, CCU, CENCOSUD, CMPC, COLBUN,
CONCHATORO, COPEC, ECL, ENELAM, ENTEL, FALABELLA, HABITAT, IAM, ILC, ITAUCL,
LTM, MALLPLAZA, PARAUCO, QUINENCO, RIPLEY, SALFACORP, SECURITY, SK, SMU, SONDA,
SQM-B, VAPORES

## Infrastructure
- **ngrok** installed at `/opt/homebrew/bin/ngrok`
- **ngrok authtoken** configured at `~/.config/ngrok/ngrok.yml`
- Free tier: public URL changes on every ngrok restart. Upgrade to paid for a fixed domain.
- The app and ngrok must both be running on this Mac for public access to work.

## Known limitations & decisions made
- **bolsadesantiago.com**: Blocked by Radware CAPTCHA — cannot be used for automated price fetching. Yahoo Finance kept as data source.
- **TradingView**: Returns 403 Forbidden on all endpoints — not usable.
- **Morning Briefing**: Only runs while the app is open. It is NOT a scheduled/background job. A separate email briefing script can be built if needed.
- **Data is not truly real-time**: Yahoo Finance free tier has ~15 min delay during market hours.
- **Public URL is not permanent**: ngrok free tier generates a new URL each session.
- **Performance tab — TWR + backfill automático**: rendimiento calculado con Time-Weighted Return (standard CFA/GIPS). Detecta aportes de caja como deltas del campo `cash` y los neutraliza. IPSA rebased al nivel del TWR en la primera fecha con datos. Auto-backfill silencioso al abrir; "Recalcular Todo" borra y reconstruye si se agregan posiciones retroactivas.
- **FRED series `INTDSRCLM193N`**: Does NOT exist — use `IRSTCI01CLM156N` for BCCh rate instead.

## SQLite tables
| Table | Purpose |
|---|---|
| `holdings` | Posiciones abiertas |
| `cash_reserve` | Caja disponible (fila única) |
| `transactions` | Log de BUY / REMOVE / CASH_UPDATE |
| `portfolio_history` | Snapshots manuales (legacy, reemplazado por daily_performance) |
| `daily_performance` | NAV diario reconstruido automáticamente — PRIMARY KEY: date TEXT |

## Pending / potential future features
- [ ] Automatic morning briefing via email (scheduled at 9:30 AM CLT)
- [ ] Fixed public URL (ngrok paid plan or alternative like Cloudflare Tunnel)
- [ ] Desktop launcher (`launch_portfolio.command` double-click shortcut)
- [ ] Auto-snapshot del NAV al cierre de mercado (scheduler o cron)
