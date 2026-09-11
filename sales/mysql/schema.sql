-- Schema for the BAS sales sample dataset (categories, suppliers, products,
-- customers, orders, order_items). Run this once before load_to_mysql.py.

CREATE DATABASE IF NOT EXISTS bas_sales
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE bas_sales;

CREATE TABLE IF NOT EXISTS categories (
  category_id   INT PRIMARY KEY,
  category_name VARCHAR(100) NOT NULL
);

CREATE TABLE IF NOT EXISTS suppliers (
  supplier_id   INT PRIMARY KEY,
  supplier_name VARCHAR(150) NOT NULL,
  contact_email VARCHAR(150),
  state         VARCHAR(2)
);

CREATE TABLE IF NOT EXISTS products (
  product_id       INT PRIMARY KEY,
  product_name     VARCHAR(150) NOT NULL,
  category_id      INT,
  supplier_id      INT,
  price            DECIMAL(10,2),
  cost             DECIMAL(10,2),
  quantity_on_hand INT,
  reorder_level    INT,
  discontinued     BOOLEAN,
  FOREIGN KEY (category_id) REFERENCES categories(category_id),
  FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id)
);

CREATE TABLE IF NOT EXISTS customers (
  customer_id  INT PRIMARY KEY,
  first_name   VARCHAR(100),
  last_name    VARCHAR(100),
  email        VARCHAR(150),
  phone        VARCHAR(20),
  city         VARCHAR(100),
  state        VARCHAR(2),
  signup_date  DATE,
  is_active    BOOLEAN
);

CREATE TABLE IF NOT EXISTS orders (
  order_id     INT PRIMARY KEY,
  customer_id  INT,
  order_date   DATE,
  shipped_date DATE NULL,
  status       VARCHAR(30),
  coupon_code  VARCHAR(30),
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE IF NOT EXISTS order_items (
  order_item_id INT PRIMARY KEY,
  order_id      INT,
  product_id    INT,
  quantity      INT,
  unit_price    DECIMAL(10,2),
  FOREIGN KEY (order_id) REFERENCES orders(order_id),
  FOREIGN KEY (product_id) REFERENCES products(product_id)
);
