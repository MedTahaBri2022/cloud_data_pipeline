-- staging -> star schema. Every statement is an upsert: running the transform
-- twice on the same staging data leaves the warehouse unchanged.

-- Calendar covering every order date (generated, never typed by hand).
INSERT INTO warehouse.dim_date
    (date_key, full_date, year, quarter, month, month_name, day_of_week, is_weekend)
SELECT to_char(d, 'YYYYMMDD')::int,
       d::date,
       extract(year FROM d)::int,
       extract(quarter FROM d)::int,
       extract(month FROM d)::int,
       trim(to_char(d, 'Month')),
       extract(isodow FROM d)::int,
       extract(isodow FROM d) IN (6, 7)
  FROM (SELECT min(ordered_at)::date AS first_day, max(ordered_at)::date AS last_day
          FROM staging.orders) AS bounds,
       generate_series(bounds.first_day, bounds.last_day, interval '1 day') AS d
ON CONFLICT (date_key) DO NOTHING;

-- Dimensions: type 1 (latest attributes overwrite the previous ones).
INSERT INTO warehouse.dim_customer (customer_id, name, email, country, signup_date)
SELECT customer_id, name, email, country, signup_date
  FROM staging.customers
ON CONFLICT (customer_id) DO UPDATE
   SET name = EXCLUDED.name,
       email = EXCLUDED.email,
       country = EXCLUDED.country,
       signup_date = EXCLUDED.signup_date;

INSERT INTO warehouse.dim_product (product_id, name, category, unit_cost, list_price)
SELECT product_id, name, category, unit_cost, list_price
  FROM staging.products
ON CONFLICT (product_id) DO UPDATE
   SET name = EXCLUDED.name,
       category = EXCLUDED.category,
       unit_cost = EXCLUDED.unit_cost,
       list_price = EXCLUDED.list_price;

-- Facts: business keys are swapped for surrogate keys, measures are computed
-- once here so every report agrees on what "revenue" and "margin" mean.
INSERT INTO warehouse.fact_order_items
    (order_id, product_key, customer_key, date_key, status, channel,
     quantity, unit_price, revenue, cost, margin)
SELECT o.order_id,
       p.product_key,
       c.customer_key,
       to_char(o.ordered_at, 'YYYYMMDD')::int,
       o.status,
       o.channel,
       i.quantity,
       i.unit_price,
       i.quantity * i.unit_price,
       i.quantity * p.unit_cost,
       i.quantity * (i.unit_price - p.unit_cost)
  FROM staging.order_items i
  JOIN staging.orders o ON o.order_id = i.order_id
  JOIN warehouse.dim_product p ON p.product_id = i.product_id
  JOIN warehouse.dim_customer c ON c.customer_id = o.customer_id
ON CONFLICT (order_id, product_key) DO UPDATE
   SET customer_key = EXCLUDED.customer_key,
       date_key = EXCLUDED.date_key,
       status = EXCLUDED.status,
       channel = EXCLUDED.channel,
       quantity = EXCLUDED.quantity,
       unit_price = EXCLUDED.unit_price,
       revenue = EXCLUDED.revenue,
       cost = EXCLUDED.cost,
       margin = EXCLUDED.margin;

-- A corrected order may have fewer lines than before: drop facts whose line no
-- longer exists in staging, so the two layers always reconcile.
DELETE FROM warehouse.fact_order_items f
 USING warehouse.dim_product p
 WHERE p.product_key = f.product_key
   AND NOT EXISTS (
       SELECT 1
         FROM staging.order_items i
        WHERE i.order_id = f.order_id
          AND i.product_id = p.product_id
   );
