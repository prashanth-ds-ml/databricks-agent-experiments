"""Generic CSV -> Unity Catalog Delta table ingestion. Point it at any
folder of CSVs and a catalog/schema, and it creates a managed volume,
uploads the CSVs, and creates one Delta table per file -- column names
are sanitized (lowercased, non-alphanumeric collapsed to underscores) so
messy source headers like "Country/Region" or "Order ID" become safe SQL
identifiers (`country_region`, `order_id`).

Non-data files are skipped automatically: filenames that look like a data
dictionary/readme, and anything over --max-mb (default 20MB, e.g. a huge
reference file bundled alongside the real tables) -- both are overridable
via --files (an explicit list is never filtered) or --include-large.

Usage:
    python ingest.py --catalog workspace --schema candy_distributor \
        --source "C:\\...\\US+Candy+Distributor"

    # Preview only -- no Databricks calls, just shows what would happen
    python ingest.py --schema candy_distributor --source "..." --dry-run
"""

import argparse
import csv
import os
import re

from db_sql import fs_cp, run_cli, run_sql

DEFAULT_MAX_MB = 20
SKIP_NAME_PATTERNS = ("dictionary", "readme", "background", "logo", "data_dict")


def create_if_not_exists(*args):
    try:
        run_cli(*args)
    except RuntimeError as e:
        if "already exists" in str(e).lower() or "RESOURCE_ALREADY_EXISTS" in str(e):
            print(f"  (already exists, skipping) databricks {' '.join(args)}")
        else:
            raise


def sanitize_identifier(name: str, seen: set) -> str:
    clean = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip()).strip("_").lower()
    if not clean:
        clean = "col"
    if clean[0].isdigit():
        clean = f"c_{clean}"
    base = clean
    i = 2
    while clean in seen:
        clean = f"{base}_{i}"
        i += 1
    seen.add(clean)
    return clean


def table_name_for(filename: str) -> str:
    stem = os.path.splitext(filename)[0]
    return sanitize_identifier(stem, set())


ENCODING_FALLBACKS = ("utf-8-sig", "cp1252", "latin-1")


def _open_csv(path: str):
    """Real-world CSVs aren't always UTF-8 -- try common fallbacks in order.
    latin-1 never raises (it maps every byte 0-255), so this always succeeds."""
    for enc in ENCODING_FALLBACKS:
        try:
            with open(path, newline="", encoding=enc) as f:
                f.read()  # force a full decode to validate the encoding
            return open(path, newline="", encoding=enc)
        except UnicodeDecodeError:
            continue
    return open(path, newline="", encoding="latin-1")


def count_csv_rows(path: str) -> int:
    with _open_csv(path) as f:
        return sum(1 for _ in csv.reader(f)) - 1  # minus header


def read_local_header(path: str):
    with _open_csv(path) as f:
        return next(csv.reader(f), [])


def discover_files(source_dir, explicit_files, max_mb, include_large):
    if explicit_files:
        return explicit_files, []

    skipped = []
    kept = []
    for f in sorted(os.listdir(source_dir)):
        if not f.lower().endswith(".csv"):
            continue
        lower = f.lower()
        size_mb = os.path.getsize(os.path.join(source_dir, f)) / (1024 * 1024)
        if any(pat in lower for pat in SKIP_NAME_PATTERNS):
            skipped.append((f, f"name looks non-tabular (matched '{[p for p in SKIP_NAME_PATTERNS if p in lower][0]}')"))
            continue
        if size_mb > max_mb and not include_large:
            skipped.append((f, f"{size_mb:.1f} MB > --max-mb {max_mb} (use --include-large or --files to force)"))
            continue
        kept.append(f)
    return kept, skipped


def preview(source_dir, files, skipped):
    print(f"Would ingest {len(files)} file(s) from {source_dir}:\n")
    for filename in files:
        path = os.path.join(source_dir, filename)
        table = table_name_for(filename)
        header = read_local_header(path)
        header = [h for h in header if h != "_rescued_data"]
        seen = set()
        col_map = [(h, sanitize_identifier(h, seen)) for h in header]
        rows = count_csv_rows(path)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"  {filename} ({size_mb:.1f} MB, {rows} rows) -> table `{table}`")
        for orig, clean in col_map:
            marker = "" if orig == clean else f"  <- '{orig}'"
            print(f"      {clean}{marker}")
        print()
    if skipped:
        print(f"Skipping {len(skipped)} file(s):")
        for filename, reason in skipped:
            print(f"  {filename}: {reason}")


def ingest_file(catalog, schema, volume, local_dir, filename):
    table = table_name_for(filename)
    local_path = os.path.join(local_dir, filename)
    volume_path = f"dbfs:/Volumes/{catalog}/{schema}/{volume}/{filename}"

    print(f"  Uploading {filename} -> {volume_path}")
    fs_cp(local_path, volume_path)

    remote_csv_path = f"/Volumes/{catalog}/{schema}/{volume}/{filename}"
    read_expr = f"read_files('{remote_csv_path}', format => 'csv', header => true, inferSchema => true)"

    probe_rows = run_sql(f"SELECT * FROM {read_expr} LIMIT 1")
    orig_columns = list(probe_rows[0].keys()) if probe_rows else []
    orig_columns = [c for c in orig_columns if c != "_rescued_data"]

    seen = set()
    select_list = ", ".join(f"`{c}` AS `{sanitize_identifier(c, seen)}`" for c in orig_columns)

    full_table = f"{catalog}.{schema}.{table}"
    run_sql(f"CREATE OR REPLACE TABLE {full_table} AS SELECT {select_list} FROM {read_expr}")

    loaded = run_sql(f"SELECT COUNT(*) AS n FROM {full_table}")[0]["n"]
    source_rows = count_csv_rows(local_path)
    status = "OK" if str(loaded) == str(source_rows) else "MISMATCH"
    print(f"  {full_table}: {loaded} rows (source csv: {source_rows}) [{status}]")
    return table


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", default="workspace")
    p.add_argument("--schema", required=True)
    p.add_argument("--source", required=True, help="Local folder containing CSVs")
    p.add_argument("--volume", default="raw_files")
    p.add_argument("--files", nargs="*", help="Specific filenames to ingest (skips auto-filtering)")
    p.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help=f"Skip files larger than this (default {DEFAULT_MAX_MB})")
    p.add_argument("--include-large", action="store_true", help="Don't skip oversized files")
    p.add_argument("--dry-run", action="store_true", help="Preview only, no Databricks calls")
    args = p.parse_args()

    files, skipped = discover_files(args.source, args.files, args.max_mb, args.include_large)
    if not files:
        raise SystemExit(f"No CSV files to ingest in {args.source} (see skipped list if any)")

    if args.dry_run:
        preview(args.source, files, skipped)
        return

    print(f"Schema {args.catalog}.{args.schema}")
    create_if_not_exists("schemas", "create", args.schema, args.catalog)
    print(f"Volume {args.catalog}.{args.schema}.{args.volume}")
    create_if_not_exists("volumes", "create", args.catalog, args.schema, args.volume, "MANAGED")

    if skipped:
        print(f"Skipping {len(skipped)} file(s): " + ", ".join(f for f, _ in skipped))

    tables = []
    for filename in files:
        print(f"Ingesting {filename}")
        tables.append(ingest_file(args.catalog, args.schema, args.volume, args.source, filename))

    print(f"\nDone. Tables: {', '.join(f'{args.catalog}.{args.schema}.{t}' for t in tables)}")


if __name__ == "__main__":
    main()
