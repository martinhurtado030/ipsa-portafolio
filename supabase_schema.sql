-- ============================================================
-- IPSA Portfolio Manager — Supabase Schema
-- Run ONCE in: Supabase Dashboard → SQL Editor → New Query
-- ============================================================

-- Holdings (posiciones abiertas)
CREATE TABLE IF NOT EXISTS holdings (
    id           BIGSERIAL    PRIMARY KEY,
    user_id      UUID         NOT NULL,
    ticker       TEXT         NOT NULL,
    company_name TEXT         NOT NULL,
    quantity     FLOAT        NOT NULL,
    buy_price    FLOAT        NOT NULL,
    buy_date     TEXT         NOT NULL,
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);

-- Cash reserve (una fila por usuario)
CREATE TABLE IF NOT EXISTS cash_reserve (
    user_id  UUID  PRIMARY KEY,
    amount   FLOAT NOT NULL DEFAULT 0
);

-- Transaction log (BUY / REMOVE / CASH_UPDATE)
CREATE TABLE IF NOT EXISTS transactions (
    id           BIGSERIAL    PRIMARY KEY,
    user_id      UUID         NOT NULL,
    action       TEXT         NOT NULL,
    ticker       TEXT,
    quantity     FLOAT,
    price        FLOAT,
    date         TEXT         NOT NULL,
    notes        TEXT,
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);

-- Capital flows (aportes y retiros de capital)
CREATE TABLE IF NOT EXISTS capital_flows (
    id         BIGSERIAL    PRIMARY KEY,
    user_id    UUID         NOT NULL,
    date       TEXT         NOT NULL,
    amount     FLOAT        NOT NULL,
    notes      TEXT,
    created_at TIMESTAMPTZ  DEFAULT NOW()
);

-- Daily performance history (NAV diario por usuario)
CREATE TABLE IF NOT EXISTS daily_performance (
    id           BIGSERIAL    PRIMARY KEY,
    user_id      UUID         NOT NULL,
    date         TEXT         NOT NULL,
    nav          FLOAT,
    equity_value FLOAT,
    cash         FLOAT,
    ipsa_close   FLOAT,
    UNIQUE (user_id, date)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_holdings_user        ON holdings(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user    ON transactions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_capital_flows_user   ON capital_flows(user_id, date);
CREATE INDEX IF NOT EXISTS idx_daily_perf_user_date ON daily_performance(user_id, date);
