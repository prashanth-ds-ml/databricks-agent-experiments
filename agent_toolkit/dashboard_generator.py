"""Generic Lakeview dashboard generator: reads whatever profiler.py already
computed for a schema and builds a dashboard automatically -- a row-count
counter, up to 2 categorical bar charts, and a monthly trend line (if a
date column exists) per table. No hand-written SQL, no dataset-specific
knowledge; charts are generic ("top values"), not curated business
insights like a hand-built dashboard would have.

Usage:
    python profiler.py --catalog workspace --schema candy_distributor   # first
    python dashboard_generator.py --catalog workspace --schema candy_distributor
"""

import argparse
import glob
import json
import os

from db_sql import WAREHOUSE_ID, run_cli

MAX_TABLES = 6
MAX_CATEGORICAL_PER_TABLE = 2


def load_profiles(schema):
    out_dir = os.path.join(os.path.dirname(__file__), "_profiles_out", schema)
    profiles = {}
    for path in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            p = json.load(f)
        profiles[os.path.splitext(os.path.basename(path))[0]] = p
    return profiles


def pick_categorical_columns(profile, limit):
    picks = []
    for col, e in profile["columns"].items():
        if e["kind"] != "categorical" or e.get("high_cardinality") or e.get("is_inferred_primary_key"):
            continue
        if e["distinct_count"] < 2:
            continue
        picks.append(col)
        if len(picks) >= limit:
            break
    return picks


def pick_temporal_column(profile):
    for col, e in profile["columns"].items():
        if e["kind"] == "temporal":
            return col
    return None


def counter_widget(name, dataset, field, title, x, y, w, h):
    return {
        "widget": {
            "name": name,
            "queries": [{"name": "main_query", "query": {
                "datasetName": dataset,
                "fields": [{"name": field, "expression": f"`{field}`"}],
                "disaggregated": True,
            }}],
            "spec": {
                "version": 2, "widgetType": "counter",
                "encodings": {"value": {"fieldName": field, "displayName": title}},
                "frame": {"showTitle": True, "title": title},
            },
        },
        "position": {"x": x, "y": y, "width": w, "height": h},
    }


def bar_widget(name, dataset, x_field, y_field, title, x, y, w, h):
    return {
        "widget": {
            "name": name,
            "queries": [{"name": "main_query", "query": {
                "datasetName": dataset,
                "fields": [
                    {"name": x_field, "expression": f"`{x_field}`"},
                    {"name": y_field, "expression": f"`{y_field}`"},
                ],
                "disaggregated": True,
            }}],
            "spec": {
                "version": 3, "widgetType": "bar",
                "encodings": {
                    "x": {"fieldName": x_field, "scale": {"type": "quantitative"}, "displayName": x_field},
                    "y": {"fieldName": y_field, "scale": {"type": "categorical", "sort": {"by": "x-reversed"}}, "displayName": y_field},
                },
                "frame": {"showTitle": True, "title": title},
            },
        },
        "position": {"x": x, "y": y, "width": w, "height": h},
    }


def line_widget(name, dataset, x_field, y_field, title, x, y, w, h):
    return {
        "widget": {
            "name": name,
            "queries": [{"name": "main_query", "query": {
                "datasetName": dataset,
                "fields": [
                    {"name": x_field, "expression": f"`{x_field}`"},
                    {"name": y_field, "expression": f"`{y_field}`"},
                ],
                "disaggregated": True,
            }}],
            "spec": {
                "version": 3, "widgetType": "line",
                "encodings": {
                    "x": {"fieldName": x_field, "scale": {"type": "categorical"}, "displayName": x_field},
                    "y": {"fieldName": y_field, "scale": {"type": "quantitative"}, "displayName": y_field},
                },
                "frame": {"showTitle": True, "title": title},
            },
        },
        "position": {"x": x, "y": y, "width": w, "height": h},
    }


def build_dashboard_json(catalog, schema, profiles):
    datasets = []
    layout = []
    y = 0
    widget_seq = 0

    tables = list(profiles.items())[:MAX_TABLES]
    for table, profile in tables:
        full = f"{catalog}.{schema}.{table}"
        row_ds = f"ds_{table}_rows"
        datasets.append({"name": row_ds, "displayName": f"{table}-rows",
                          "query": f"SELECT COUNT(*) AS row_count FROM {full}"})
        widget_seq += 1
        layout.append(counter_widget(f"w{widget_seq}", row_ds, "row_count", f"{table}: rows", 0, y, 2, 3))

        cat_cols = pick_categorical_columns(profile, MAX_CATEGORICAL_PER_TABLE)
        x_pos = 2
        for col in cat_cols:
            ds_name = f"ds_{table}_{col}_top"
            datasets.append({"name": ds_name, "displayName": f"{table}-{col}-top",
                              "query": f"SELECT `{col}` AS value, COUNT(*) AS n FROM {full} "
                                        f"WHERE `{col}` IS NOT NULL GROUP BY `{col}` ORDER BY n DESC LIMIT 10"})
            widget_seq += 1
            layout.append(bar_widget(f"w{widget_seq}", ds_name, "n", "value", f"{table}: top {col}", x_pos, y, 2, 3))
            x_pos += 2

        y += 3

        temporal_col = pick_temporal_column(profile)
        if temporal_col:
            ds_name = f"ds_{table}_{temporal_col}_trend"
            datasets.append({"name": ds_name, "displayName": f"{table}-{temporal_col}-trend",
                              "query": f"SELECT date_format(`{temporal_col}`, 'yyyy-MM') AS period, COUNT(*) AS n "
                                        f"FROM {full} GROUP BY 1 ORDER BY 1"})
            widget_seq += 1
            layout.append(line_widget(f"w{widget_seq}", ds_name, "period", "n", f"{table}: rows by {temporal_col}", 0, y, 6, 4))
            y += 4

    return {
        "datasets": datasets,
        "pages": [{"name": "main", "displayName": f"{schema} Overview", "layout": layout}],
    }


def find_existing_dashboard(display_name):
    """List dashboards and return the id of one matching display_name, if
    any -- so rerunning this script updates the same dashboard in place
    instead of failing on a name collision or piling up duplicates."""
    resp = run_cli_json("get", "/api/2.0/lakeview/dashboards")
    for d in resp.get("dashboards", []):
        if d.get("display_name") == display_name and d.get("lifecycle_state") != "TRASHED":
            return d["dashboard_id"]
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", default="workspace")
    p.add_argument("--schema", required=True)
    p.add_argument("--warehouse-id", default=WAREHOUSE_ID)
    p.add_argument("--display-name", default=None)
    args = p.parse_args()

    profiles = load_profiles(args.schema)
    if not profiles:
        raise SystemExit(f"No profile JSON found for '{args.schema}'. Run profiler.py first.")

    display_name = args.display_name or f"{args.schema} Overview"
    content = build_dashboard_json(args.catalog, args.schema, profiles)

    req_path = os.path.join(os.path.dirname(__file__), "_dashboard_request.json")
    with open(req_path, "w", encoding="utf-8") as f:
        json.dump({
            "display_name": display_name,
            "warehouse_id": args.warehouse_id,
            "serialized_dashboard": json.dumps(content),
        }, f)

    existing_id = find_existing_dashboard(display_name)
    if existing_id:
        run_cli_json("patch", f"/api/2.0/lakeview/dashboards/{existing_id}", req_path)
        dashboard_id = existing_id
        print(f"Updated existing dashboard '{display_name}' ({dashboard_id})")
    else:
        resp = run_cli_json("post", "/api/2.0/lakeview/dashboards", req_path)
        dashboard_id = resp["dashboard_id"]
        print(f"Created dashboard '{display_name}' ({dashboard_id})")

    run_cli("lakeview", "publish", dashboard_id, "--warehouse-id", args.warehouse_id)
    print(f"Published. Open: #dashboardsv3/{dashboard_id}/published (in your workspace URL)")


def run_cli_json(method, path, body_file=None):
    import subprocess
    from db_sql import DB_EXE, PROFILE
    args = [DB_EXE, "api", method, path, "--profile", PROFILE, "-o", "json"]
    if body_file:
        args += ["--json", f"@{body_file}"]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"api {method} {path} failed: {result.stdout}\n{result.stderr}")
    return json.loads(result.stdout)


if __name__ == "__main__":
    main()
