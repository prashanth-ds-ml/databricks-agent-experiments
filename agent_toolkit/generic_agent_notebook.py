# Databricks notebook source
# MAGIC %md
# MAGIC # Generic Sales-Style Agent
# MAGIC - Same tool-calling agent pattern as `sales_agent`, but the tools are
# MAGIC   **schema-agnostic** -- point it at any ingested dataset by changing
# MAGIC   the `catalog` / `schema` widgets below and rerunning
# MAGIC - Trade-off vs. the hand-written sales tools: these tools work on any
# MAGIC   dataset instantly, but answers are less curated -- the LLM has to
# MAGIC   figure out which table/column to use itself, guided by
# MAGIC   `list_tables` and `get_table_profile`
# MAGIC
# MAGIC **Prerequisites:**
# MAGIC 1. Run `ingest.py` to load a dataset's CSVs into `<catalog>.<schema>`.
# MAGIC 2. Run `profiler.py` against the same `<catalog>.<schema>` to populate
# MAGIC    the `profiles` volume these tools read from.
# MAGIC
# MAGIC ## Generic tool inventory
# MAGIC | Tool | Replaces (in the sales-specific version) |
# MAGIC |---|---|
# MAGIC | `list_tables()` | n/a -- new, needed since tables aren't known in advance |
# MAGIC | `get_table_profile(table_name)` | same tool, same idea |
# MAGIC | `run_readonly_sql(query)` | all 4 hand-written UC functions at once, at the cost of curation |
# MAGIC | `top_n_group_by(table, group_by, metric, n, agg)` | `top_selling_products` |
# MAGIC | `filter_by_threshold(table, column, operator, value, limit)` | `low_stock_products` |
# MAGIC
# MAGIC `monthly_revenue` and `customer_order_history` don't have direct generic
# MAGIC equivalents -- those need either a join (use `run_readonly_sql`) or a
# MAGIC specific "which column is the date/customer key" assumption the generic
# MAGIC tools deliberately don't make.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Install dependencies (with retry)
# MAGIC `databricks-langchain`/`mlflow` pull in fast-moving, heavy dependency
# MAGIC trees (openai, mcp), and every formula tried here that omitted
# MAGIC `unitycatalog-ai[databricks]` failed consistently -- 3 separate retries
# MAGIC in one run, each ~15-18 minutes, all hitting the identical
# MAGIC `ResolutionTooDeep: 200000`. This notebook doesn't use
# MAGIC `unitycatalog-ai` directly, but the sibling `sales_agent` notebook's
# MAGIC install line (which does include it) succeeded 3/3 times with an
# MAGIC otherwise-identical dependency set. Adding it back here purely as an
# MAGIC empirically-justified resolver stabilizer, not because the code needs
# MAGIC it. The retry loop stays regardless, in case this specific package set
# MAGIC is still occasionally flaky.

# COMMAND ----------

import subprocess
import sys
import time

PIP_ARGS = [
    "-U", "databricks-langchain", "unitycatalog-ai[databricks]", "mlflow",
    "langgraph==1.0.5", "langgraph-checkpoint==3.0.1",
    "langgraph-prebuilt==1.0.5", "langgraph-sdk==0.3.1",
]


def pip_install_with_retry(args, attempts=3, delay_seconds=20):
    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *args],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"pip install succeeded (attempt {attempt}/{attempts})")
            return
        print(f"pip install failed (attempt {attempt}/{attempts}, exit {result.returncode})")
        print(result.stderr[-1500:])
        if attempt < attempts:
            print(f"Retrying in {delay_seconds}s...")
            time.sleep(delay_seconds)
    raise RuntimeError(f"pip install failed after {attempts} attempts")


pip_install_with_retry(PIP_ARGS)
dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Swap the dataset here
# MAGIC Change these two widgets (top of the notebook in the Databricks UI) and
# MAGIC rerun -- nothing below this cell hardcodes a table or column name.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "bas_sales")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
PROFILES_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/profiles"
LLM_ENDPOINT = "databricks-meta-llama-3-3-70b-instruct"

# Set the session default so unqualified table names in LLM-written SQL
# (inside run_readonly_sql) resolve to this dataset without the model
# having to know or guess the catalog/schema.
spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"USE SCHEMA {SCHEMA}")

print(f"Dataset: {CATALOG}.{SCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Build the schema allow-list
# MAGIC Every generic tool below validates its `table_name`/`column` arguments
# MAGIC against this dict -- built fresh from `information_schema` on every run
# MAGIC -- before touching SQL. An LLM-supplied identifier that isn't in here
# MAGIC is rejected with a plain-text message instead of being interpolated
# MAGIC into a query, which is what makes string-building the SQL below safe.

# COMMAND ----------

import re

import mlflow

mlflow.langchain.autolog()


def refresh_schema_catalog():
    rows = spark.sql(f"""
        SELECT table_name, column_name
        FROM {CATALOG}.information_schema.columns
        WHERE table_schema = '{SCHEMA}' AND column_name != '_rescued_data'
        ORDER BY table_name, ordinal_position
    """).collect()
    catalog = {}
    for r in rows:
        catalog.setdefault(r["table_name"], []).append(r["column_name"])
    return catalog


TABLE_COLUMNS = refresh_schema_catalog()
print(f"{len(TABLE_COLUMNS)} tables: {', '.join(TABLE_COLUMNS)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The generic tools
# MAGIC - `run_readonly_sql` blocks DDL/DML by keyword and only allows
# MAGIC   `SELECT`/`WITH`, auto-caps at 100 rows -- a regex/keyword guard is a
# MAGIC   reasonable teaching-grade defense, not a production one; a real
# MAGIC   deployment would run this under a genuinely read-only Unity Catalog
# MAGIC   credential too, not rely on app-level checks alone
# MAGIC - `top_n_group_by` and `filter_by_threshold` validate every identifier
# MAGIC   against `TABLE_COLUMNS` before building SQL, so an unknown table or
# MAGIC   column is rejected rather than interpolated

# COMMAND ----------

from langchain_core.tools import tool

FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|GRANT|REVOKE|COPY|USE|SET)\b",
    re.IGNORECASE,
)
AGGS = {"sum": "SUM", "avg": "AVG", "count": "COUNT", "min": "MIN", "max": "MAX"}
OPERATORS = {"=", "!=", "<", "<=", ">", ">="}


@tool
def list_tables() -> str:
    """List every table available in this dataset, with column counts.
    Call this first if you don't already know what tables exist."""
    return "\n".join(f"{t} ({len(cols)} columns)" for t, cols in sorted(TABLE_COLUMNS.items()))


@tool
def get_table_profile(table_name: str) -> str:
    """Return a precomputed data profile for a table: row/column count,
    per-column null %, distinct count, numerical min/max/mean/median/stddev,
    or categorical top-10 value counts. Use this to find valid column names
    and understand a table's shape before calling other tools."""
    if table_name not in TABLE_COLUMNS:
        return f"Unknown table '{table_name}'. Valid tables: {', '.join(TABLE_COLUMNS)}"
    try:
        with open(f"{PROFILES_DIR}/{table_name}.json", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"No cached profile for '{table_name}'. Run profiler.py against {CATALOG}.{SCHEMA} first."


@tool
def run_readonly_sql(query: str) -> str:
    """Run a read-only SQL query (SELECT/WITH only) against this dataset's
    tables (unqualified table names resolve within it) and return up to 100
    rows as text. DDL/DML is blocked. Prefer the other tools when they fit;
    use this for joins or questions the other tools can't express."""
    q = query.strip().rstrip(";")
    if not re.match(r"^(SELECT|WITH)\b", q, re.IGNORECASE):
        return "Rejected: only SELECT/WITH queries are allowed."
    if FORBIDDEN_SQL.search(q):
        return "Rejected: query contains a disallowed keyword."
    try:
        df = spark.sql(q)
        rows = df.limit(100).collect()
        return "\n".join(str(r.asDict()) for r in rows) or "(no rows)"
    except Exception as e:
        return f"Query failed: {e}"


@tool
def top_n_group_by(table_name: str, group_by_column: str, metric_column: str, n: int = 5, agg: str = "sum") -> str:
    """Group a table by one column and aggregate another, returning the top
    N groups (e.g. top 5 product_name by sum of sales). agg must be one of:
    sum, avg, count, min, max. Call get_table_profile first to find valid
    table_name/group_by_column/metric_column values."""
    if table_name not in TABLE_COLUMNS:
        return f"Unknown table '{table_name}'. Valid tables: {', '.join(TABLE_COLUMNS)}"
    cols = TABLE_COLUMNS[table_name]
    if group_by_column not in cols or metric_column not in cols:
        return f"Unknown column(s). Valid columns for {table_name}: {', '.join(cols)}"
    if agg not in AGGS:
        return f"Unknown agg '{agg}'. Valid: {', '.join(AGGS)}"
    n = max(1, min(int(n), 50))
    df = spark.sql(f"""
        SELECT `{group_by_column}` AS group_value, {AGGS[agg]}(`{metric_column}`) AS metric_value
        FROM {CATALOG}.{SCHEMA}.{table_name}
        GROUP BY `{group_by_column}`
        ORDER BY metric_value DESC
        LIMIT {n}
    """)
    rows = df.collect()
    return "\n".join(f"{r['group_value']}: {r['metric_value']}" for r in rows) or "(no rows)"


@tool
def filter_by_threshold(table_name: str, column: str, operator: str, value: str, limit: int = 20) -> str:
    """Return rows from a table where a column meets a threshold, e.g.
    quantity_on_hand <= 10. operator must be one of: =, !=, <, <=, >, >=.
    Call get_table_profile first to find valid table_name/column values."""
    if table_name not in TABLE_COLUMNS:
        return f"Unknown table '{table_name}'. Valid tables: {', '.join(TABLE_COLUMNS)}"
    if column not in TABLE_COLUMNS[table_name]:
        return f"Unknown column '{column}'. Valid columns: {', '.join(TABLE_COLUMNS[table_name])}"
    if operator not in OPERATORS:
        return f"Unknown operator '{operator}'. Valid: {', '.join(OPERATORS)}"
    limit = max(1, min(int(limit), 100))
    try:
        value_sql = str(float(value))
    except ValueError:
        value_sql = "'" + str(value).replace("'", "''") + "'"
    df = spark.sql(
        f"SELECT * FROM {CATALOG}.{SCHEMA}.{table_name} WHERE `{column}` {operator} {value_sql} LIMIT {limit}"
    )
    rows = df.collect()
    return "\n".join(str(r.asDict()) for r in rows) or "(no rows matched)"


TOOLS = [list_tables, get_table_profile, run_readonly_sql, top_n_group_by, filter_by_threshold]

# COMMAND ----------

# MAGIC %md
# MAGIC ## Wire up the agent
# MAGIC Same LangGraph ReAct pattern as the sales-specific notebook -- only the
# MAGIC tool list changed.

# COMMAND ----------

from databricks_langchain import ChatDatabricks
from langgraph.prebuilt import create_react_agent

llm = ChatDatabricks(endpoint=LLM_ENDPOINT)

SYSTEM_PROMPT = (
    "You are a data analyst assistant. You don't know this dataset's schema "
    "in advance -- start with list_tables and get_table_profile to learn "
    "what's available, then use the other tools to answer. Always call a "
    "tool rather than guessing numbers. Cite the specific figures returned."
)

agent = create_react_agent(llm, TOOLS, prompt=SYSTEM_PROMPT)


def ask(question: str) -> str:
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return result["messages"][-1].content


# COMMAND ----------

# MAGIC %md ## Try it out
# MAGIC These questions are dataset-agnostic on purpose -- they should work
# MAGIC whatever `catalog`/`schema` you pointed the widgets at above.

# COMMAND ----------

print(ask("What tables are available, and roughly how big is each one?"))

# COMMAND ----------

print(ask("Pick the table that looks most like a transactions/sales table, and tell me its top 5 categories by total value, using whatever numeric and categorical columns make sense."))

# COMMAND ----------

print(ask("Find a table with a quantity-like numeric column and show me rows where that quantity is unusually low."))
