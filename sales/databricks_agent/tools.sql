-- Unity Catalog functions used as tools by the sales agent notebook
-- (agent_notebook.py). Each function is a governed, reusable SQL
-- function that an LLM can call via tool-calling.

CREATE OR REPLACE FUNCTION workspace.bas_sales.top_selling_products(
  n INT COMMENT 'How many top products to return, ranked by total revenue descending. Defaults to 5.' DEFAULT 5
)
RETURNS TABLE(product_name STRING, category_name STRING, units_sold BIGINT, revenue DECIMAL(12,2))
COMMENT 'Returns the top N products by total revenue, joined with category names.'
RETURN
  SELECT product_name, category_name, units_sold, revenue FROM (
    SELECT p.product_name, c.category_name,
           SUM(oi.quantity) AS units_sold,
           SUM(oi.quantity * oi.unit_price) AS revenue,
           ROW_NUMBER() OVER (ORDER BY SUM(oi.quantity * oi.unit_price) DESC) AS rn
    FROM workspace.bas_sales.order_items oi
    JOIN workspace.bas_sales.products p ON oi.product_id = p.product_id
    JOIN workspace.bas_sales.categories c ON p.category_id = c.category_id
    GROUP BY p.product_name, c.category_name
  )
  WHERE rn <= n
  ORDER BY revenue DESC;

CREATE OR REPLACE FUNCTION workspace.bas_sales.customer_order_history(
  cust_id INT COMMENT 'The customer_id (from the customers table) to look up order history for.'
)
RETURNS TABLE(order_id INT, order_date DATE, status STRING, item_count BIGINT, order_total DECIMAL(12,2))
COMMENT 'Returns order history (id, date, status, item count, total) for a given customer_id.'
RETURN
  SELECT o.order_id, o.order_date, o.status,
         COUNT(oi.order_item_id) AS item_count,
         SUM(oi.quantity * oi.unit_price) AS order_total
  FROM workspace.bas_sales.orders o
  JOIN workspace.bas_sales.order_items oi ON o.order_id = oi.order_id
  WHERE o.customer_id = cust_id
  GROUP BY o.order_id, o.order_date, o.status
  ORDER BY o.order_date;

CREATE OR REPLACE FUNCTION workspace.bas_sales.low_stock_products()
RETURNS TABLE(product_name STRING, quantity_on_hand INT, reorder_level INT, supplier_name STRING)
COMMENT 'Returns non-discontinued products at or below their reorder level, with supplier name, for restocking decisions.'
RETURN
  SELECT p.product_name, p.quantity_on_hand, p.reorder_level, s.supplier_name
  FROM workspace.bas_sales.products p
  JOIN workspace.bas_sales.suppliers s ON p.supplier_id = s.supplier_id
  WHERE p.quantity_on_hand <= p.reorder_level AND p.discontinued = false;

CREATE OR REPLACE FUNCTION workspace.bas_sales.monthly_revenue()
RETURNS TABLE(month STRING, revenue DECIMAL(12,2), order_count BIGINT)
COMMENT 'Returns total revenue and distinct order count grouped by calendar month (yyyy-MM) of order_date.'
RETURN
  SELECT date_format(o.order_date, 'yyyy-MM') AS month,
         SUM(oi.quantity * oi.unit_price) AS revenue,
         COUNT(DISTINCT o.order_id) AS order_count
  FROM workspace.bas_sales.orders o
  JOIN workspace.bas_sales.order_items oi ON o.order_id = oi.order_id
  GROUP BY date_format(o.order_date, 'yyyy-MM')
  ORDER BY month;
