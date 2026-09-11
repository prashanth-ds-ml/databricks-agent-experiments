"""Profiles all workspace.bas_sales tables (shape, per-column null/distinct
counts, numerical min/max/mean/median/stddev, categorical top-N value
counts, PK/FK flags) and publishes the results as one JSON file per table
plus a combined data_dictionary.md, uploaded to the
workspace.bas_sales.profiles Unity Catalog volume.

Rerun this whenever the underlying tables change, so the cached profiles
(and the agent's get_table_profile tool, see agent_notebook.py) stay
current. Usage:
    python build_profiles.py
"""

import json
import os

from db_sql import fs_cp, run_sql

CATALOG = "workspace"
SCHEMA = "bas_sales"
TABLES = ["categories", "suppliers", "customers", "products", "orders", "order_items"]
CATEGORICAL_CARDINALITY_LIMIT = 50
TOP_N = 10

NUMERIC_TYPES = {"INT", "BIGINT", "SMALLINT", "TINYINT", "DOUBLE", "FLOAT", "DECIMAL"}
TEMPORAL_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMP_NTZ"}

PRIMARY_KEYS = {
    "categories": ["category_id"],
    "suppliers": ["supplier_id"],
    "customers": ["customer_id"],
    "products": ["product_id"],
    "orders": ["order_id"],
    "order_items": ["order_item_id"],
}
FOREIGN_KEYS = {
    "products": {"category_id": "categories.category_id", "supplier_id": "suppliers.supplier_id"},
    "orders": {"customer_id": "customers.customer_id"},
    "order_items": {"order_id": "orders.order_id", "product_id": "products.product_id"},
}

VOLUME_PATH = f"dbfs:/Volumes/{CATALOG}/{SCHEMA}/profiles"


def base_type(data_type):
    return data_type.split("(")[0].upper()


def get_columns(table):
    rows = run_sql(f"""
        SELECT column_name, data_type
        FROM {CATALOG}.information_schema.columns
        WHERE table_catalog = '{CATALOG}' AND table_schema = '{SCHEMA}' AND table_name = '{table}'
          AND column_name != '_rescued_data'
        ORDER BY ordinal_position
    """)
    return [(r["column_name"], base_type(r["data_type"])) for r in rows]


def build_summary_sql(table, columns):
    full = f"{CATALOG}.{SCHEMA}.{table}"
    exprs = ["COUNT(*) AS row_count"]
    for col, dtype in columns:
        exprs.append(f"COUNT(*) - COUNT(`{col}`) AS `{col}__nulls`")
        exprs.append(f"COUNT(DISTINCT `{col}`) AS `{col}__distinct`")
        if dtype in NUMERIC_TYPES:
            exprs.append(f"MIN(`{col}`) AS `{col}__min`")
            exprs.append(f"MAX(`{col}`) AS `{col}__max`")
            exprs.append(f"AVG(`{col}`) AS `{col}__mean`")
            exprs.append(f"PERCENTILE_APPROX(`{col}`, 0.5) AS `{col}__median`")
            exprs.append(f"STDDEV(`{col}`) AS `{col}__stddev`")
        elif dtype in TEMPORAL_TYPES:
            exprs.append(f"MIN(`{col}`) AS `{col}__min`")
            exprs.append(f"MAX(`{col}`) AS `{col}__max`")
    return f"SELECT {', '.join(exprs)} FROM {full}"


def num(v):
    return float(v) if v is not None else None


def profile_table(table):
    columns = get_columns(table)
    summary = run_sql(build_summary_sql(table, columns))[0]
    row_count = int(summary["row_count"])

    columns_profile = {}
    for col, dtype in columns:
        null_count = int(summary[f"{col}__nulls"])
        distinct_count = int(summary[f"{col}__distinct"])
        entry = {
            "data_type": dtype,
            "null_count": null_count,
            "null_pct": round(null_count / row_count * 100, 2) if row_count else 0.0,
            "distinct_count": distinct_count,
            "is_primary_key": col in PRIMARY_KEYS.get(table, []),
            "is_foreign_key": col in FOREIGN_KEYS.get(table, {}),
        }
        if entry["is_foreign_key"]:
            entry["references"] = FOREIGN_KEYS[table][col]

        if dtype in NUMERIC_TYPES:
            entry.update({
                "kind": "numerical",
                "min": num(summary[f"{col}__min"]),
                "max": num(summary[f"{col}__max"]),
                "mean": round(num(summary[f"{col}__mean"]), 4) if summary[f"{col}__mean"] is not None else None,
                "median": num(summary[f"{col}__median"]),
                "stddev": round(num(summary[f"{col}__stddev"]), 4) if summary[f"{col}__stddev"] is not None else None,
            })
        elif dtype in TEMPORAL_TYPES:
            entry.update({
                "kind": "temporal",
                "min": summary[f"{col}__min"],
                "max": summary[f"{col}__max"],
            })
        else:
            entry["kind"] = "categorical"
            if distinct_count <= CATEGORICAL_CARDINALITY_LIMIT:
                vc_rows = run_sql(f"""
                    SELECT `{col}` AS value, COUNT(*) AS n
                    FROM {CATALOG}.{SCHEMA}.{table}
                    WHERE `{col}` IS NOT NULL
                    GROUP BY `{col}`
                    ORDER BY n DESC
                    LIMIT {TOP_N}
                """)
                entry["value_counts"] = {r["value"]: int(r["n"]) for r in vc_rows}
                entry["high_cardinality"] = False
            else:
                entry["high_cardinality"] = True
                entry["note"] = (
                    f"{distinct_count} unique values across {row_count} rows; "
                    "too high-cardinality to list (likely an identifier/free-text column)"
                )

        columns_profile[col] = entry

    return {
        "table": f"{CATALOG}.{SCHEMA}.{table}",
        "row_count": row_count,
        "column_count": len(columns),
        "primary_key": PRIMARY_KEYS.get(table, []),
        "foreign_keys": FOREIGN_KEYS.get(table, {}),
        "columns": columns_profile,
    }


def render_markdown(profiles):
    lines = ["# Sales Data Dictionary", "", "Auto-generated table/column profile for `workspace.bas_sales`.", ""]
    for table, p in profiles.items():
        lines.append(f"## {table}")
        lines.append(f"- Rows: {p['row_count']}  |  Columns: {p['column_count']}")
        if p["primary_key"]:
            lines.append(f"- Primary key: `{', '.join(p['primary_key'])}`")
        if p["foreign_keys"]:
            fk_str = ", ".join(f"`{c}` -> `{ref}`" for c, ref in p["foreign_keys"].items())
            lines.append(f"- Foreign keys: {fk_str}")
        lines.append("")
        lines.append("| Column | Kind | Null % | Distinct | Summary |")
        lines.append("|---|---|---|---|---|")
        for col, e in p["columns"].items():
            if e["kind"] == "numerical":
                summary = f"min={e['min']}, max={e['max']}, mean={e['mean']}, median={e['median']}, stddev={e['stddev']}"
            elif e["kind"] == "temporal":
                summary = f"range: {e['min']} to {e['max']}"
            elif e.get("high_cardinality"):
                summary = e["note"]
            else:
                top = ", ".join(f"{k} ({v})" for k, v in list(e.get("value_counts", {}).items())[:5])
                summary = top
            pk = " (PK)" if e["is_primary_key"] else (" (FK)" if e["is_foreign_key"] else "")
            lines.append(f"| {col}{pk} | {e['kind']} | {e['null_pct']}% | {e['distinct_count']} | {summary} |")
        lines.append("")
    return "\n".join(lines)


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "profiles")
    os.makedirs(out_dir, exist_ok=True)

    profiles = {}
    for t in TABLES:
        print(f"Profiling {t} ...")
        profiles[t] = profile_table(t)
        path = os.path.join(out_dir, f"{t}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profiles[t], f, indent=2)
        fs_cp(path, f"{VOLUME_PATH}/{t}.json")
        print(f"  uploaded to {VOLUME_PATH}/{t}.json")

    md_path = os.path.join(out_dir, "data_dictionary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(profiles))
    fs_cp(md_path, f"{VOLUME_PATH}/data_dictionary.md")

    print(f"Done. {len(TABLES)} table profiles + data_dictionary.md in {out_dir} and {VOLUME_PATH}")


if __name__ == "__main__":
    main()
