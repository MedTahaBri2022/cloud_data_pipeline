-- Warehouse schema. Idempotent: safe to run at the start of every pipeline run.

CREATE SCHEMA IF NOT EXISTS etl;        -- pipeline bookkeeping
CREATE SCHEMA IF NOT EXISTS staging;    -- validated copies of the sources
CREATE SCHEMA IF NOT EXISTS warehouse;  -- star schema
CREATE SCHEMA IF NOT EXISTS marts;      -- reporting layer read by BI tools

-- ---------------------------------------------------------------- bookkeeping

CREATE TABLE IF NOT EXISTS etl.watermarks (
    source     text PRIMARY KEY,
    position   timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS etl.rejected_records (
    id          bigserial PRIMARY KEY,
    source      text NOT NULL,
    record_id   text NOT NULL,
    reasons     text[] NOT NULL,
    payload     jsonb NOT NULL,
    rejected_at timestamptz NOT NULL DEFAULT now(),
    -- A record that is still invalid on the next run is updated, not duplicated.
    CONSTRAINT uq_rejected_source_record UNIQUE (source, record_id)
);

CREATE TABLE IF NOT EXISTS etl.quality_results (
    id         bigserial PRIMARY KEY,
    check_name text NOT NULL,
    passed     boolean NOT NULL,
    observed   text NOT NULL,
    checked_at timestamptz NOT NULL DEFAULT now()
);

-- -------------------------------------------------------------------- staging

CREATE TABLE IF NOT EXISTS staging.customers (
    customer_id text PRIMARY KEY,
    name        text NOT NULL,
    email       text NOT NULL,
    country     text NOT NULL,
    signup_date date NOT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staging.products (
    product_id text PRIMARY KEY,
    name       text NOT NULL,
    category   text NOT NULL,
    unit_cost  numeric(10, 2) NOT NULL,
    list_price numeric(10, 2) NOT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staging.orders (
    order_id    text PRIMARY KEY,
    customer_id text NOT NULL,
    status      text NOT NULL,
    channel     text NOT NULL,
    ordered_at  timestamptz NOT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staging.order_items (
    order_id   text NOT NULL REFERENCES staging.orders (order_id) ON DELETE CASCADE,
    product_id text NOT NULL,
    quantity   int NOT NULL,
    unit_price numeric(10, 2) NOT NULL,
    PRIMARY KEY (order_id, product_id)
);

-- ------------------------------------------------------------------ warehouse

CREATE TABLE IF NOT EXISTS warehouse.dim_date (
    date_key    int PRIMARY KEY,           -- 20260131
    full_date   date NOT NULL UNIQUE,
    year        int NOT NULL,
    quarter     int NOT NULL,
    month       int NOT NULL,
    month_name  text NOT NULL,
    day_of_week int NOT NULL,              -- 1 = Monday
    is_weekend  boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS warehouse.dim_customer (
    customer_key bigserial PRIMARY KEY,    -- surrogate key
    customer_id  text NOT NULL UNIQUE,     -- business key
    name         text NOT NULL,
    email        text NOT NULL,
    country      text NOT NULL,
    signup_date  date NOT NULL
);

CREATE TABLE IF NOT EXISTS warehouse.dim_product (
    product_key bigserial PRIMARY KEY,
    product_id  text NOT NULL UNIQUE,
    name        text NOT NULL,
    category    text NOT NULL,
    unit_cost   numeric(10, 2) NOT NULL,
    list_price  numeric(10, 2) NOT NULL
);

-- Grain: one row per product per order.
CREATE TABLE IF NOT EXISTS warehouse.fact_order_items (
    order_id     text NOT NULL,
    product_key  bigint NOT NULL REFERENCES warehouse.dim_product (product_key),
    customer_key bigint NOT NULL REFERENCES warehouse.dim_customer (customer_key),
    date_key     int NOT NULL REFERENCES warehouse.dim_date (date_key),
    status       text NOT NULL,
    channel      text NOT NULL,
    quantity     int NOT NULL,
    unit_price   numeric(10, 2) NOT NULL,
    revenue      numeric(12, 2) NOT NULL,
    cost         numeric(12, 2) NOT NULL,
    margin       numeric(12, 2) NOT NULL,
    PRIMARY KEY (order_id, product_key)
);

CREATE INDEX IF NOT EXISTS idx_fact_date ON warehouse.fact_order_items (date_key);
CREATE INDEX IF NOT EXISTS idx_fact_customer ON warehouse.fact_order_items (customer_key);
