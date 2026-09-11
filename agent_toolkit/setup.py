"""One-command orchestrator: ingest -> profile -> dashboard -> summary ->
example questions, driven by a small JSON config instead of retyping CLI
flags for each script. This is the "swap the database" entry point.

Usage:
    python setup.py --config configs/candy_distributor.json
    python setup.py --config configs/candy_distributor.json --dry-run
    python setup.py --config configs/candy_distributor.json --skip dashboard summary

Config file shape:
    {
      "catalog": "workspace",
      "schema": "candy_distributor",
      "source": "C:\\...\\US+Candy+Distributor",
      "warehouse_id": "461364b1c78f2539"   // optional, defaults to db_sql.WAREHOUSE_ID
    }
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

STEPS = ["ingest", "profile", "dashboard", "summary", "questions"]
HERE = Path(__file__).resolve().parent


def run(script, *args):
    cmd = [sys.executable, str(HERE / script), *args]
    print(f"\n=== {' '.join(cmd)} ===")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise SystemExit(f"{script} failed (exit {result.returncode}); stopping.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--skip", nargs="*", default=[], choices=STEPS, help="Steps to skip")
    p.add_argument("--dry-run", action="store_true", help="Preview ingestion only, run nothing else")
    args = p.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    catalog = cfg.get("catalog", "workspace")
    schema = cfg["schema"]
    source = cfg["source"]
    warehouse_id = cfg.get("warehouse_id")

    if "ingest" not in args.skip:
        ingest_args = ["--catalog", catalog, "--schema", schema, "--source", source]
        if args.dry_run:
            ingest_args.append("--dry-run")
        run("ingest.py", *ingest_args)
        if args.dry_run:
            print("\nDry run only -- stopping before profile/dashboard/summary/questions.")
            return

    if "profile" not in args.skip:
        run("profiler.py", "--catalog", catalog, "--schema", schema)

    if "dashboard" not in args.skip:
        dash_args = ["--catalog", catalog, "--schema", schema]
        if warehouse_id:
            dash_args += ["--warehouse-id", warehouse_id]
        run("dashboard_generator.py", *dash_args)

    if "summary" not in args.skip:
        run("describe_dataset.py", "--catalog", catalog, "--schema", schema)

    if "questions" not in args.skip:
        run("generate_questions.py", "--schema", schema)

    print(f"\nDone. {catalog}.{schema} is ready. Point generic_agent_notebook.py's "
          f"catalog/schema widgets at it to start asking questions.")


if __name__ == "__main__":
    main()
