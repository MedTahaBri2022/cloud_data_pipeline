-- Reporting layer: what Power BI (or the reporting API) reads. Materialized so
-- dashboards never run heavy aggregations themselves; refreshed at the end of
-- each pipeline run. Cancelled and returned orders are excluded from sales.

CREATE MATERIALIZED VIEW IF NOT EXISTS marts.monthly_revenue AS
SELECT d.year,
       d.month,
       to_char(d.full_date, 'YYYY-MM')   AS year_month,
       count(DISTINCT f.order_id)        AS orders,
       sum(f.quantity)                   AS units,
       sum(f.revenue)                    AS revenue,
       sum(f.margin)                     AS margin,
       round(sum(f.margin) / nullif(sum(f.revenue), 0) * 100, 2) AS margin_pct
  FROM warehouse.fact_order_items f
  JOIN warehouse.dim_date d ON d.date_key = f.date_key
 WHERE f.status NOT IN ('cancelled', 'returned')
 GROUP BY d.year, d.month, to_char(d.full_date, 'YYYY-MM');

CREATE UNIQUE INDEX IF NOT EXISTS uq_monthly_revenue ON marts.monthly_revenue (year_month);

CREATE MATERIALIZED VIEW IF NOT EXISTS marts.product_performance AS
SELECT p.product_id,
       p.name,
       p.category,
       count(DISTINCT f.order_id)  AS orders,
       sum(f.quantity)             AS units,
       sum(f.revenue)              AS revenue,
       sum(f.margin)               AS margin,
       rank() OVER (ORDER BY sum(f.revenue) DESC) AS revenue_rank
  FROM warehouse.fact_order_items f
  JOIN warehouse.dim_product p ON p.product_key = f.product_key
 WHERE f.status NOT IN ('cancelled', 'returned')
 GROUP BY p.product_id, p.name, p.category;

CREATE UNIQUE INDEX IF NOT EXISTS uq_product_performance ON marts.product_performance (product_id);

-- RFM segmentation: recency, frequency and monetary value, each scored 1-4 by
-- quartile, then mapped to a segment a marketer can act on.
CREATE MATERIALIZED VIEW IF NOT EXISTS marts.customer_segments AS
WITH per_customer AS (
    SELECT c.customer_id,
           c.name,
           c.country,
           max(d.full_date)           AS last_order_date,
           count(DISTINCT f.order_id) AS orders,
           sum(f.revenue)             AS revenue
      FROM warehouse.fact_order_items f
      JOIN warehouse.dim_customer c ON c.customer_key = f.customer_key
      JOIN warehouse.dim_date d ON d.date_key = f.date_key
     WHERE f.status NOT IN ('cancelled', 'returned')
     GROUP BY c.customer_id, c.name, c.country
),
scored AS (
    SELECT *,
           ntile(4) OVER (ORDER BY last_order_date) AS recency_score,
           ntile(4) OVER (ORDER BY orders)          AS frequency_score,
           ntile(4) OVER (ORDER BY revenue)         AS monetary_score
      FROM per_customer
)
SELECT customer_id,
       name,
       country,
       last_order_date,
       orders,
       revenue,
       recency_score,
       frequency_score,
       monetary_score,
       CASE
           WHEN recency_score = 4 AND frequency_score >= 3 AND monetary_score >= 3 THEN 'champion'
           WHEN recency_score >= 3 AND frequency_score >= 2 THEN 'loyal'
           WHEN recency_score = 4 THEN 'new'
           WHEN recency_score <= 2 AND monetary_score >= 3 THEN 'at risk'
           ELSE 'occasional'
       END AS segment
  FROM scored;

CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_segments ON marts.customer_segments (customer_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS marts.sales_by_country_channel AS
SELECT c.country,
       f.channel,
       count(DISTINCT f.order_id) AS orders,
       sum(f.revenue)             AS revenue,
       round(sum(f.revenue) / count(DISTINCT f.order_id), 2) AS average_order_value
  FROM warehouse.fact_order_items f
  JOIN warehouse.dim_customer c ON c.customer_key = f.customer_key
 WHERE f.status NOT IN ('cancelled', 'returned')
 GROUP BY c.country, f.channel;

CREATE UNIQUE INDEX IF NOT EXISTS uq_sales_by_country_channel
    ON marts.sales_by_country_channel (country, channel);
