SELECT c.country,
       f.channel,
       COUNT(DISTINCT f.order_id) AS orders,
       SUM(f.revenue)             AS revenue,
       ROUND(SUM(f.revenue) / COUNT(DISTINCT f.order_id), 2) AS average_order_value
  FROM `${warehouse}.fact_order_items` f
  JOIN `${warehouse}.dim_customer` c ON c.customer_key = f.customer_key
 WHERE f.status NOT IN ('cancelled', 'returned')
 GROUP BY c.country, f.channel
