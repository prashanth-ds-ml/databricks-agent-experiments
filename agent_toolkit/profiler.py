"""Generic data profiler: point it at any catalog.schema and it profiles
every table found there (shape, null %, distinct count, numerical
min/max/mean/median/stddev, categorical top-10 value counts, temporal
range, heuristically inferred primary keys), then uploads one JSON per
table plus a combined data_dictionary.md to a `profiles` volume in that
same schema.

Usage:
    python profiler.py --catalog workspace --schema candy_distributor
"""

import argparse
import json
import os

from db_sql import fs_cp, run_cli, run_sql

CATEGORICAL_CARDINALITY_LIMIT = 50
TOP_N = 10

NUMERIC_TYPES = {"INT", "BIGINT", "SMALLINT", "TINYINT", "DOUBLE", "FLOAT", "DECIMAL"}
TEMPORAL_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMP_NTZ"}


def base_type(data_type):
    return data_type.split("(")[0].upper()


def list_tables(catalog, schema):
    rows = run_sql(f"""
        SELECT table_name
        FROM {catalog}.information_schema.tables
        WHERE table_catalog = '{catalog}' AND table_schema = '{schema}'
          AND table_type = 'MANAGED' AND table_name != 'profiles'
        ORDER BY table_name
    """)
    return [r["table_name"] for r in rows]


def get_columns(catalog, schema, table):
    rows = run_sql(f"""
        SELECT column_name, data_type
        FROM {catalog}.information_schema.columns
        WHERE table_catalog = '{catalog}' AND table_schema = '{schema}' AND table_name = '{table}'
          AND column_name != '_rescued_data'
        ORDER BY ordinal_position
    """)
    return [(r["column_name"], base_type(r["data_type"])) for r in rows]


def build_summary_sql(catalog, schema, table, columns):
    full = f"{catalog}.{schema}.{table}"
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


def profile_table(catalog, schema, table):
    columns = get_columns(catalog, schema, table)
    summary = run_sql(build_summary_sql(catalog, schema, table, columns))[0]
    row_count = int(summary["row_count"])

    columns_profile = {}
    pk_candidates = []
    for col, dtype in columns:
        null_count = int(summary[f"{col}__nulls"])
        distinct_count = int(summary[f"{col}__distinct"])
        entry = {
            "data_type": dtype,
            "null_count": null_count,
            "null_pct": round(null_count / row_count * 100, 2) if row_count else 0.0,
            "distinct_count": distinct_count,
        }

        if row_count and null_count == 0 and distinct_count == row_count:
            pk_candidates.append(col)

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
                    FROM {catalog}.{schema}.{table}
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

    # Heuristic PK: a fully-unique, non-null column, preferring one whose
    # name looks like an id. Inferred, not a declared constraint.
    inferred_pk = None
    id_like = [c for c in pk_candidates if c.lower() == "id" or c.lower().endswith("_id") or c.lower().endswith("id")]
    if id_like:
        inferred_pk = id_like[0]
    elif pk_candidates:
        inferred_pk = pk_candidates[0]
    if inferred_pk:
        columns_profile[inferred_pk]["is_inferred_primary_key"] = True

    return {
        "table": f"{catalog}.{schema}.{table}",
        "row_count": row_count,
        "column_count": len(columns),
        "inferred_primary_key": inferred_pk,
        "columns": columns_profile,
    }


def render_markdown(schema, profiles):
    lines = [f"# {schema} Data Dictionary", "", f"Auto-generated table/column profile for `{schema}`.", ""]
    for table, p in profiles.items():
        lines.append(f"## {table}")
        lines.append(f"- Rows: {p['row_count']}  |  Columns: {p['column_count']}")
        if p["inferred_primary_key"]:
            lines.append(f"- Inferred primary key: `{p['inferred_primary_key']}` (heuristic, not a declared constraint)")
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
            pk = " (PK, inferred)" if e.get("is_inferred_primary_key") else ""
            lines.append(f"| {col}{pk} | {e['kind']} | {e['null_pct']}% | {e['distinct_count']} | {summary} |")
        lines.append("")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", default="workspace")
    p.add_argument("--schema", required=True)
    p.add_argument("--volume", default="profiles")
    args = p.parse_args()

    try:
        run_cli("volumes", "create", args.catalog, args.schema, args.volume, "MANAGED")
    except RuntimeError as e:
        if "already exists" not in str(e).lower():
            raise

    tables = list_tables(args.catalog, args.schema)
    if not tables:
        raise SystemExit(f"No tables found in {args.catalog}.{args.schema}")

    out_dir = os.path.join(os.path.dirname(__file__), "_profiles_out", args.schema)
    os.makedirs(out_dir, exist_ok=True)

    profiles = {}
    for t in tables:
        print(f"Profiling {t} ...")
        profiles[t] = profile_table(args.catalog, args.schema, t)
        path = os.path.join(out_dir, f"{t}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profiles[t], f, indent=2)
        fs_cp(path, f"dbfs:/Volumes/{args.catalog}/{args.schema}/{args.volume}/{t}.json")

    md_path = os.path.join(out_dir, "data_dictionary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(args.schema, profiles))
    fs_cp(md_path, f"dbfs:/Volumes/{args.catalog}/{args.schema}/{args.volume}/data_dictionary.md")

    print(f"\nDone. {len(tables)} tables profiled: {', '.join(tables)}")
    print(f"Local output: {out_dir}")
    print(f"Volume: {args.catalog}.{args.schema}.{args.volume}")


if __name__ == "__main__":
    main()
