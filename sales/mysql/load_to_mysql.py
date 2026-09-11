"""Load the BAS sales sample CSVs into the local MySQL `bas_sales` database.

Usage (PowerShell):
    $env:MYSQL_PWD = "<your-mysql-password>"
    python load_to_mysql.py

Credentials are read from environment variables so the password never has to
be typed into a chat or committed to a file:
    MYSQL_HOST  (default: 127.0.0.1)
    MYSQL_PORT  (default: 3306)
    MYSQL_USER  (default: root)
    MYSQL_PWD   (required, no default)
    MYSQL_DB    (default: bas_sales)

Run schema.sql once first (e.g. via MySQL Workbench or:
    mysql -u root -p < schema.sql
) before running this script.
"""

import csv
import os
import sys
from pathlib import Path

import pymysql

DATA_DIR = Path(__file__).resolve().parent.parent

# (table, csv filename, columns in load order, set of columns that may be
# empty-string-in-CSV -> NULL, set of boolean columns)
TABLES = [
    ("categories", "categories.csv",
     ["category_id", "category_name"], set(), set()),
    ("suppliers", "suppliers.csv",
     ["supplier_id", "supplier_name", "contact_email", "state"],
     {"contact_email", "state"}, set()),
    ("customers", "customers.csv",
     ["customer_id", "first_name", "last_name", "email", "phone", "city",
      "state", "signup_date", "is_active"],
     {"phone"}, {"is_active"}),
    ("products", "products.csv",
     ["product_id", "product_name", "category_id", "supplier_id", "price",
      "cost", "quantity_on_hand", "reorder_level", "discontinued"],
     set(), {"discontinued"}),
    ("orders", "orders.csv",
     ["order_id", "customer_id", "order_date", "shipped_date", "status",
      "coupon_code"],
     {"shipped_date", "coupon_code"}, set()),
    ("order_items", "order_items.csv",
     ["order_item_id", "order_id", "product_id", "quantity", "unit_price"],
     set(), set()),
]


def coerce_row(row, nullable_cols, bool_cols):
    out = []
    for col, val in row.items():
        if val == "" and col in nullable_cols:
            out.append(None)
        elif col in bool_cols:
            out.append(1 if val.strip().lower() == "true" else 0)
        else:
            out.append(val)
    return out


def load_table(conn, table, csv_name, columns, nullable_cols, bool_cols):
    csv_path = DATA_DIR / csv_name
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [coerce_row(row, nullable_cols, bool_cols) for row in reader]

    if not rows:
        print(f"  {table}: no rows found in {csv_path}, skipping")
        return

    placeholders = ", ".join(["%s"] * len(columns))
    col_list = ", ".join(columns)
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    print(f"  {table}: loaded {len(rows)} rows")


def main():
    password = os.environ.get("MYSQL_PWD")
    if not password:
        sys.exit(
            "MYSQL_PWD is not set. Run:\n"
            '  $env:MYSQL_PWD = "<your-mysql-password>"\n'
            "then re-run this script."
        )

    conn = pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "root"),
        password=password,
        database=os.environ.get("MYSQL_DB", "bas_sales"),
    )

    print(f"Connected to {conn.host}:{conn.port}, database={conn.db.decode()}")

    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for table, *_ in TABLES:
                cur.execute(f"TRUNCATE TABLE {table}")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
        conn.commit()

        for table, csv_name, columns, nullable_cols, bool_cols in TABLES:
            load_table(conn, table, csv_name, columns, nullable_cols, bool_cols)
    finally:
        conn.close()

    print("Done.")


if __name__ == "__main__":
    main()
