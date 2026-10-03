-- RFM segmentation, identical to the PostgreSQL mart.
WITH per_customer AS (
    SELECT c.customer_id,
           c.name,
           c.country,
           MAX(d.full_date)           AS last_order_date,
           COUNT(DISTINCT f.order_id) AS orders,
           SUM(f.revenue)             AS revenue
      FROM `${warehouse}.fact_order_items` f
      JOIN `${warehouse}.dim_customer` c ON c.customer_key = f.customer_key
      JOIN `${warehouse}.dim_date` d ON d.date_key = f.date_key
     WHERE f.status NOT IN ('cancelled', 'returned')
     GROUP BY c.customer_id, c.name, c.country
),
scored AS (
    SELECT *,
           NTILE(4) OVER (ORDER BY last_order_date, customer_id) AS recency_score,
           NTILE(4) OVER (ORDER BY orders, customer_id) AS frequency_score,
           NTILE(4) OVER (ORDER BY revenue, customer_id) AS monetary_score
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
  FROM scored
