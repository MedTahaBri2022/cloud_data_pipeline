-- Same rule as sql/003_marts.sql: cancelled and returned orders are not sales.
SELECT d.year,
       d.month,
       FORMAT_DATE('%Y-%m', d.full_date) AS year_month,
       COUNT(DISTINCT f.order_id)        AS orders,
       SUM(f.quantity)                   AS units,
       SUM(f.revenue)                    AS revenue,
       SUM(f.margin)                     AS margin,
       ROUND(SAFE_DIVIDE(SUM(f.margin), SUM(f.revenue)) * 100, 2) AS margin_pct
  FROM `${warehouse}.fact_order_items` f
  JOIN `${warehouse}.dim_date` d ON d.date_key = f.date_key
 WHERE f.status NOT IN ('cancelled', 'returned')
 GROUP BY d.year, d.month, year_month
