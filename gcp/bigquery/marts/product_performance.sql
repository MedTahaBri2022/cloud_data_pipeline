SELECT p.product_id,
       p.name,
       p.category,
       COUNT(DISTINCT f.order_id) AS orders,
       SUM(f.quantity)            AS units,
       SUM(f.revenue)             AS revenue,
       SUM(f.margin)              AS margin,
       RANK() OVER (ORDER BY SUM(f.revenue) DESC) AS revenue_rank
  FROM `${warehouse}.fact_order_items` f
  JOIN `${warehouse}.dim_product` p ON p.product_key = f.product_key
 WHERE f.status NOT IN ('cancelled', 'returned')
 GROUP BY p.product_id, p.name, p.category
