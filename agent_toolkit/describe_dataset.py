"""Generates a one-paragraph plain-English summary of a dataset from its
profile -- "this looks like a sales/transactions dataset with 4 tables,
~10K orders, spanning 2021-2026...". One LLM call (via the ai_query SQL
function) over a compact schema description; everything else in this
toolkit is either deterministic or, for the agent, per-question -- this is
the one place a single summarizing LLM call earns its keep.

Usage:
    python describe_dataset.py --catalog workspace --schema candy_distributor
"""

import argparse
import glob
import json
import os

from db_sql import fs_cp, run_sql

LLM_ENDPOINT = "databricks-meta-llama-3-3-70b-instruct"


def load_profiles(schema):
    out_dir = os.path.join(os.path.dirname(__file__), "_profiles_out", schema)
    profiles = {}
    for path in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            profiles[os.path.splitext(os.path.basename(path))[0]] = json.load(f)
    return profiles


def build_schema_text(schema, profiles):
    lines = [f"Schema: {schema}"]
    for table, p in profiles.items():
        lines.append(f"\nTable `{table}` ({p['row_count']} rows):")
        for col, e in p["columns"].items():
            if e["kind"] == "categorical" and not e.get("high_cardinality"):
                sample = ", ".join(list(e.get("value_counts", {}))[:4])
                lines.append(f"  - {col} ({e['kind']}): e.g. {sample}")
            elif e["kind"] == "temporal":
                lines.append(f"  - {col} ({e['kind']}): {e['min']} to {e['max']}")
            else:
                lines.append(f"  - {col} ({e['kind']})")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", default="workspace")
    p.add_argument("--schema", required=True)
    p.add_argument("--volume", default="profiles")
    args = p.parse_args()

    profiles = load_profiles(args.schema)
    if not profiles:
        raise SystemExit(f"No profile JSON found for '{args.schema}'. Run profiler.py first.")

    schema_text = build_schema_text(args.schema, profiles)
    prompt = (
        "You are a data analyst. Below is a schema (tables, columns, sample "
        "categorical values, date ranges) for a dataset. In 3-5 sentences, "
        "plain English, describe what kind of business/domain this data "
        "likely represents, what the main tables probably mean, and one or "
        "two things worth investigating. Do not invent numbers not shown "
        "below.\n\n" + schema_text
    )
    escaped_prompt = prompt.replace("'", "''")

    rows = run_sql(f"SELECT ai_query('{LLM_ENDPOINT}', '{escaped_prompt}') AS summary")
    summary = rows[0]["summary"]

    out_dir = os.path.join(os.path.dirname(__file__), "_profiles_out", args.schema)
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "dataset_summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {args.schema}: what is this data?\n\n{summary}\n")

    fs_cp(md_path, f"dbfs:/Volumes/{args.catalog}/{args.schema}/{args.volume}/dataset_summary.md")

    print(summary)
    print(f"\nWrote {md_path} and uploaded to {args.catalog}.{args.schema}.{args.volume}")


if __name__ == "__main__":
    main()
