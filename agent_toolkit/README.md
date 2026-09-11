# Agent Toolkit

- A reusable, dataset-agnostic version of the `sales_agent` build
- Point it at any folder of CSVs and a catalog/schema name, and you get:
  Delta tables, a data profile, a generic tool-calling agent, and an
  auto-generated dashboard -- all without writing new SQL
- Trade-off vs. the hand-built `databricks_agent` folder: everything here
  works instantly on a new dataset, but the tools and dashboard are
  generic (the LLM has to figure out which table/column fits a question),
  not curated business logic like `top_selling_products` was

## Proven on two different datasets

- `workspace.bas_sales` -- the original sales schema (6 tables, hand-built
  tools existed already)
- `workspace.candy_distributor` -- ingested fresh from
  `databricks_agents/US+Candy+Distributor/` as the generalization test: 5
  tables (including a 33,787-row zip-code reference table it picked up
  automatically), a 10,194-row sales table, messy source headers
  (`Order ID`, `Country/Region`) correctly sanitized to `order_id`,
  `country_region`

## Usage: swap the dataset in 1 command

```powershell
cd databricks_agents\agent_toolkit

# Write a small config once (see configs/candy_distributor.json for an example)
python setup.py --config configs/<your_dataset>.json

# Preview first if you're unsure what will get picked up (no Databricks calls)
python setup.py --config configs/<your_dataset>.json --dry-run

# Rerun any subset (all steps are idempotent -- update in place, no duplicates)
python setup.py --config configs/<your_dataset>.json --skip ingest profile
```

`setup.py` chains, in order: ingest -> profile -> dashboard -> plain-English
summary -> example questions. Each step is also a standalone script if you
want to run just one (`python ingest.py --help`, etc.).

Then open `generic_agent_notebook.py` in Databricks (or upload it via
`databricks workspace import`), change the `catalog`/`schema` widgets at
the top to `<your_schema>`, and run it -- no code edits needed.

## What a config file looks like

```json
{
  "catalog": "workspace",
  "schema": "candy_distributor",
  "source": "C:\\...\\US+Candy+Distributor",
  "warehouse_id": "<your-sql-warehouse-id>"
}
```

## Small things that make "just connect any data" actually work

- **`ingest.py --dry-run`** -- shows every file it would ingest, the
  sanitized table/column names, and row counts, entirely locally (zero
  Databricks calls) -- catch a wrong folder or a typo before touching
  anything.
- **Auto-skips non-data files** -- filenames that look like a data
  dictionary/readme, and anything over `--max-mb` (default 20; e.g. a
  30MB reference file bundled next to the real tables) are skipped by
  default, with the reason printed. Override with `--files` (explicit
  list is never filtered) or `--include-large`.
- **Encoding fallback** -- real-world CSVs aren't always UTF-8; local
  preview/row-counting tries `utf-8-sig` -> `cp1252` -> `latin-1` instead
  of crashing on the first non-UTF-8 byte.
- **`describe_dataset.py`** -- one LLM call (via `ai_query`) that reads
  the profile and writes a 3-5 sentence plain-English summary of what the
  dataset probably is, uploaded alongside the profiles.
- **`generate_questions.py`** -- deterministic, no LLM call: generates a
  handful of plausible example questions purely from column
  kinds/cardinality (excludes id-like/PK columns from being used as a
  "total X" metric -- that bug showed up immediately on `candy_sales`,
  where `row_id` was getting summed before the fix).
- **`setup.py`** itself -- one config file instead of five sets of CLI
  flags, and every step is idempotent (dashboard generation looks up an
  existing dashboard by name and updates it instead of failing on a name
  collision).

## What's generic vs. what needs a human

| Piece | How generic |
|---|---|
| `ingest.py` | Fully generic -- any CSV folder, sanitizes messy column names automatically |
| `profiler.py` | Fully generic -- discovers tables/columns via `information_schema`, infers a likely primary key heuristically (not a declared constraint) |
| `dashboard_generator.py` | Fully generic -- builds a row-count counter, up to 2 categorical bar charts, and a date trend line per table, purely from the profile |
| `generic_agent_notebook.py` tools | Fully generic -- `list_tables`, `get_table_profile`, `run_readonly_sql`, `top_n_group_by`, `filter_by_threshold` all validate identifiers against a live schema allow-list, no hardcoded table/column names |
| The *questions* the agent answers well | Not fully generic -- generic tools mean generic (less curated) answers; a domain-specific tool like `low_stock_products` will always out-perform a generic `filter_by_threshold` call for that exact question |

## Generic tool inventory

| Tool | What it does | Safety |
|---|---|---|
| `list_tables()` | Lists every table in the dataset | n/a |
| `get_table_profile(table_name)` | Returns the cached profile JSON for a table | Validates `table_name` against known tables |
| `run_readonly_sql(query)` | Runs a `SELECT`/`WITH` query, up to 100 rows | Blocks DDL/DML by keyword, `SELECT`/`WITH`-only prefix check -- a teaching-grade guard, not a production one; a real deployment should also run this under a genuinely read-only Unity Catalog credential |
| `top_n_group_by(table, group_by, metric, n, agg)` | Top N groups by an aggregate | `table`/`group_by`/`metric` validated against a live `information_schema` allow-list before touching SQL; `agg` validated against a fixed set |
| `filter_by_threshold(table, column, operator, value, limit)` | Rows matching a comparison | Same allow-list validation; `operator` validated against a fixed set; `value` is float-cast or quote-escaped |

## A debugging lesson worth keeping

`generic_agent_notebook.py`'s `%pip install` cell intermittently failed
with `pip._vendor.resolvelib.resolvers.ResolutionTooDeep: 200000` -- pip's
resolver thrashing through the fast-moving `databricks-langchain`/`mlflow`
dependency tree (openai, mcp). Two instinctive fixes were tried and made
it *worse*, not better, across repeated test runs: pinning every package
to an exact version, and splitting the install into two separate `pip`
calls. Neither was the real variable.

What actually mattered, found by testing every combination rather than
reasoning about it in the abstract: every failing run had dropped
`unitycatalog-ai[databricks]` from the install line (this notebook
doesn't use it directly, so it seemed safe to omit) -- and every
successful run had kept it. Adding it back, purely as an empirically
justified resolver stabilizer, fixed it. Both notebooks' install cells
also keep a retry loop as a safety net and carry a comment explaining
this so a future "obvious" fix doesn't get re-tried blind.

## Relationship to `databricks_agent/`

- `databricks_agent/` (the original `sales_agent` build) stays as-is --
  curated, named tools and a hand-designed dashboard, specific to the
  sales schema
- This folder is the general-purpose version, proven by actually running
  it against a second, unrelated dataset rather than just designed to
  look portable
