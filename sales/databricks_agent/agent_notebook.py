# Databricks notebook source
# MAGIC %md
# MAGIC # Sales Agent
# MAGIC - Tool-calling agent over the `workspace.bas_sales` tables
# MAGIC - Built with the Mosaic AI Agent Framework: a Databricks-hosted
# MAGIC   foundation model (`ChatDatabricks`) bound to Unity Catalog SQL
# MAGIC   functions as tools (see `tools.sql` in this folder)
# MAGIC - Orchestrated with LangGraph's prebuilt ReAct agent
# MAGIC
# MAGIC **Prerequisites** (both one-time, rerun after data changes):
# MAGIC 1. Run `tools.sql` against a SQL warehouse to register the four UC
# MAGIC    functions this notebook uses as tools.
# MAGIC 2. Run `python build_profiles.py` locally to populate the
# MAGIC    `workspace.bas_sales.profiles` volume that the `get_table_profile`
# MAGIC    tool reads from.
# MAGIC
# MAGIC - Full pipeline history (MySQL, Databricks ingestion, dashboard,
# MAGIC   profiler): see `README.md` in this folder
# MAGIC - This notebook picks up from "tables + tools already exist"
# MAGIC
# MAGIC ## How the whole pipeline fits together
# MAGIC - Diagram rendered a few cells below (right after dependencies are
# MAGIC   installed)
# MAGIC - Covers everything in this folder end to end: the one-time/on-change
# MAGIC   data pipeline on one side, the agent runtime on the other
# MAGIC - Databricks `%md` cells don't render Mermaid natively, so it's drawn
# MAGIC   via `displayHTML` + Mermaid.js instead -- see the `show_mermaid`
# MAGIC   helper below
# MAGIC
# MAGIC ## Tool inventory
# MAGIC | Tool | Type | Reads | Use for |
# MAGIC |---|---|---|---|
# MAGIC | `top_selling_products(n)` | UC SQL function | order_items, products, categories | "top N products by revenue" |
# MAGIC | `customer_order_history(cust_id)` | UC SQL function | orders, order_items | "this customer's orders / spend" |
# MAGIC | `low_stock_products()` | UC SQL function | products, suppliers | "what needs restocking" |
# MAGIC | `monthly_revenue()` | UC SQL function | orders, order_items | "revenue trend over time" |
# MAGIC | `get_table_profile(table_name)` | Python tool, cached file read | profiles volume (JSON) | "shape, null %, min/max/mean, unique counts" |
# MAGIC
# MAGIC - The four UC functions run live SQL against the Delta tables every call
# MAGIC - `get_table_profile` is different on purpose: reads a precomputed
# MAGIC   snapshot instead of recomputing stats per question, so profiling
# MAGIC   questions are instant and cheap -- see `build_profiles.py`

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Install dependencies (with retry)
# MAGIC - `langgraph` and `langgraph-prebuilt` are pinned to the **same** version
# MAGIC   on purpose
# MAGIC - Newer `langgraph-prebuilt` releases import `ExecutionInfo` /
# MAGIC   `ServerInfo` from `langgraph.runtime`, which only exists in matching
# MAGIC   newer `langgraph` core releases
# MAGIC - Mixed versions (e.g. a bare `-U langgraph`) raise:
# MAGIC   `ImportError: cannot import name 'ExecutionInfo' from 'langgraph.runtime'`
# MAGIC - `databricks-langchain`/`mlflow`/`unitycatalog-ai` are left on `-U`
# MAGIC   deliberately -- pinning them tighter was tried (in the sibling
# MAGIC   `generic_agent_notebook.py`) and made an intermittent
# MAGIC   `ResolutionTooDeep: 200000` pip error *more* frequent, not less.
# MAGIC   Across repeated test runs, exact-pinning, splitting into two
# MAGIC   installs, and this exact `-U` formula each failed at least once --
# MAGIC   including one run where the identical command had already succeeded
# MAGIC   minutes earlier. That's a transient PyPI-index/resolver hiccup, not a
# MAGIC   property of the command, so the fix is retrying the install a few
# MAGIC   times rather than chasing another formula.
# MAGIC - `dbutils.library.restartPython()` reloads the kernel so the freshly
# MAGIC   installed versions actually take effect

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
# MAGIC ## Diagram helper
# MAGIC - Defined **after** the `restartPython()` above on purpose
# MAGIC - `dbutils.library.restartPython()` wipes the entire Python namespace to
# MAGIC   load the freshly installed packages -- anything defined before it
# MAGIC   (including a helper function) stops existing afterwards
# MAGIC - Renders the pipeline diagram referenced in the intro cell

# COMMAND ----------


def show_mermaid(diagram: str, height: int = 460) -> None:
    """Render a Mermaid diagram in a notebook cell's output. Databricks `%md`
    cells don't execute the Mermaid.js that GitHub/other renderers use, so a
    ```mermaid fence in markdown just shows as a plain code block -- this
    instead uses `displayHTML` (a real Databricks builtin, sandboxed iframe
    with JS allowed) to load Mermaid.js from a CDN and render the diagram
    directly in the cell's output area.
    """
    displayHTML(f"""
    <div class="mermaid" style="background:white;padding:16px;border-radius:8px;">
{diagram}
    </div>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
    <script>mermaid.initialize({{ startOnLoad: true, theme: "default" }});</script>
    """)


show_mermaid("""
flowchart LR
    subgraph Ingest["Data pipeline (one-time / on data change)"]
        CSV[/"Local CSVs\\ndatabricks_agents/sales"/] -->|"databricks fs cp"| RawVol[("raw_files\\nvolume")]
        RawVol -->|"read_files + CTAS"| Tables[("6 Delta tables\\nworkspace.bas_sales")]
        Tables -->|"build_profiles.py"| ProfVol[("profiles volume\\nJSON + data_dictionary.md")]
        Tables -->|"tools.sql"| UCFuncs["4 UC SQL functions"]
    end

    subgraph Runtime["Agent runtime (this notebook)"]
        Question(["User question"]) --> Agent["LangGraph\\nReAct agent"]
        Agent <--> LLM["ChatDatabricks\\nLlama 3.3 70B"]
        Agent --> UCFuncs
        Agent --> ProfileTool["get_table_profile tool"]
        ProfileTool --> ProfVol
        UCFuncs --> Tables
        Agent --> Answer(["Answer"])
    end
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Configuration
# MAGIC - Names the catalog/schema, which foundation model to call, where the
# MAGIC   profiler output lives, and the list of UC functions to bind as tools
# MAGIC - Nothing here talks to Databricks yet -- just constants used by the
# MAGIC   cells below

# COMMAND ----------

import json

from databricks_langchain import ChatDatabricks, UCFunctionToolkit
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

CATALOG = "workspace"
SCHEMA = "bas_sales"
LLM_ENDPOINT = "databricks-meta-llama-3-3-70b-instruct"
PROFILES_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/profiles"
PROFILED_TABLES = ["categories", "suppliers", "customers", "products", "orders", "order_items"]

TOOL_FUNCTIONS = [
    f"{CATALOG}.{SCHEMA}.top_selling_products",
    f"{CATALOG}.{SCHEMA}.customer_order_history",
    f"{CATALOG}.{SCHEMA}.low_stock_products",
    f"{CATALOG}.{SCHEMA}.monthly_revenue",
]

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. The `get_table_profile` tool
# MAGIC - A plain Python/LangChain tool (`@tool`), not a UC function -- needs a
# MAGIC   file read + lookup, which SQL functions can't express
# MAGIC - Reads the JSON that `build_profiles.py` already computed and uploaded
# MAGIC   to the `profiles` volume, returns it verbatim for the LLM to read
# MAGIC   numbers out of
# MAGIC - Unknown table, or cache not built yet -> a plain-text error back to
# MAGIC   the model instead of a stack trace, since that's what a tool result
# MAGIC   becomes either way

# COMMAND ----------


@tool
def get_table_profile(table_name: str) -> str:
    """Return a precomputed data profile for one of the bas_sales tables:
    row/column count, per-column null %, distinct count, and for numerical
    columns min/max/mean/median/stddev, or for categorical columns the top
    value counts. Use this for questions about data shape, distributions,
    ranges, or "how many unique X" -- it's exact and instant, so prefer it
    over estimating from other tool results. Valid table_name values:
    categories, suppliers, customers, products, orders, order_items.
    """
    if table_name not in PROFILED_TABLES:
        return f"Unknown table '{table_name}'. Valid tables: {', '.join(PROFILED_TABLES)}"
    try:
        with open(f"{PROFILES_DIR}/{table_name}.json", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return (
            f"No cached profile found for '{table_name}'. Run build_profiles.py "
            "(see this folder) to generate it."
        )


# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Wire up the LLM, tools, and agent
# MAGIC - `UCFunctionToolkit` turns the four UC SQL functions into LangChain
# MAGIC   tools automatically -- name, args, and description all come from the
# MAGIC   function's own SQL metadata (the comments in `tools.sql`)
# MAGIC - `get_table_profile` is added to that same tool list
# MAGIC - LangGraph's prebuilt ReAct agent implements the tool-calling loop
# MAGIC - That loop, for one question, is rendered in the next cell
# MAGIC - ReAct means the LLM can repeat "call a tool, read the result" more
# MAGIC   than once before answering (e.g. `get_table_profile` on two different
# MAGIC   tables for a comparison question) -- the diagram shows one
# MAGIC   round-trip, but nothing here caps it beyond the model's own judgment

# COMMAND ----------

show_mermaid("""
sequenceDiagram
    autonumber
    participant U as User
    participant Ag as Agent (LangGraph)
    participant L as LLM (ChatDatabricks)
    participant To as Tool
    participant Src as SQL Warehouse / Volume

    U->>Ag: ask("...")
    Ag->>L: messages + tool schemas
    L-->>Ag: tool_call(name, args)
    Ag->>To: invoke(args)
    To->>Src: query / file read
    Src-->>To: rows / JSON
    To-->>Ag: tool result
    Ag->>L: messages + tool result
    L-->>Ag: final answer
    Ag-->>U: answer text
""", height=380)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Tracing
# MAGIC - `mlflow.langchain.autolog()` turns on MLflow Tracing for every LangChain
# MAGIC   / LangGraph call made after this point -- no code changes to the agent
# MAGIC   itself
# MAGIC - Captures each LLM call and each tool call (name, arguments, result,
# MAGIC   latency) as a trace, viewable in this notebook's MLflow Experiment
# MAGIC - One line, part of the Mosaic AI Agent Framework's observability layer

# COMMAND ----------

import mlflow

mlflow.langchain.autolog()

# COMMAND ----------

llm = ChatDatabricks(endpoint=LLM_ENDPOINT)

toolkit = UCFunctionToolkit(function_names=TOOL_FUNCTIONS)
tools = toolkit.tools + [get_table_profile]

SYSTEM_PROMPT = (
    "You are a sales analyst for a small plant/garden retailer. Answer "
    "questions about products, customers, orders, and inventory using the "
    "tools provided. For questions about data shape, ranges, distributions, "
    "or unique-value counts, use get_table_profile rather than guessing or "
    "computing manually. Always call a tool rather than guessing numbers. "
    "Cite the specific figures the tool returned in your answer."
)

agent = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Try it out
# MAGIC - `ask()` is a thin wrapper: send one user message in, read the agent's
# MAGIC   final text back out
# MAGIC - Each cell below is a separate question chosen to exercise a
# MAGIC   different tool (noted per cell) so you can see which tool fired for
# MAGIC   which kind of question

# COMMAND ----------


def ask(question: str) -> str:
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return result["messages"][-1].content


# COMMAND ----------

# MAGIC %md Exercises `top_selling_products`.

# COMMAND ----------

print(ask("What are our top 3 products by revenue?"))

# COMMAND ----------

# MAGIC %md Exercises `low_stock_products`.

# COMMAND ----------

print(ask("Which products need restocking right now, and who supplies them?"))

# COMMAND ----------

# MAGIC %md Exercises `monthly_revenue`.

# COMMAND ----------

print(ask("How did monthly revenue trend over the data we have, and which month was best?"))

# COMMAND ----------

# MAGIC %md Exercises `customer_order_history`.

# COMMAND ----------

print(ask("Show me the order history for customer 1 and summarize their spending."))

# COMMAND ----------

# MAGIC %md Exercises `get_table_profile` on `products` (numerical stats + distinct count).

# COMMAND ----------

print(ask("What's the price range and average price of our products, and how many product categories are there?"))

# COMMAND ----------

# MAGIC %md Exercises `get_table_profile` on `customers` (null % + categorical value counts).

# COMMAND ----------

print(ask("How much of the customers table has missing data, and what fraction of customers are active?"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Bonus: AI Functions (SQL-only, no agent)
# MAGIC - `ai_classify` is a Databricks SQL builtin: calls an LLM directly from a
# MAGIC   query, no Python, no LangGraph, no tools
# MAGIC - Same restocking question `low_stock_products` answers, but classified
# MAGIC   into an urgency label straight in SQL -- useful comparison point:
# MAGIC   this is the lightest-weight way to put AI in front of the data,
# MAGIC   trading control/traceability for simplicity
# MAGIC - Part of the Mosaic AI Agent Framework's tool surface, used with zero
# MAGIC   agent-side setup

# COMMAND ----------

display(spark.sql("""
    SELECT
      p.product_name,
      p.quantity_on_hand,
      p.reorder_level,
      ai_classify(
        'Product: ' || p.product_name ||
        ', quantity on hand: ' || p.quantity_on_hand ||
        ', reorder level: ' || p.reorder_level,
        ARRAY('urgent', 'monitor', 'ok')
      ) AS restock_urgency
    FROM workspace.bas_sales.products p
    WHERE p.quantity_on_hand <= p.reorder_level AND p.discontinued = false
    ORDER BY p.quantity_on_hand ASC
"""))

# COMMAND ----------

# MAGIC %md ## Next steps
# MAGIC - Add more UC functions to `tools.sql` for new questions (e.g. churn
# MAGIC   risk, category mix) and append them to `TOOL_FUNCTIONS`.
# MAGIC - Re-run `build_profiles.py` after any data change so `get_table_profile`
# MAGIC   stays current -- it reads a cached snapshot, not live data.
# MAGIC - Wrap `agent` in an MLflow `ChatAgent`/`ChatModel` interface -- the
# MAGIC   step that unlocks AI Playground, Agent Evaluation, and deployment;
# MAGIC   nothing below this line works until it's done.
# MAGIC - Build an Agent Evaluation golden set from the six questions above
# MAGIC   (expected facts, not just "looks reasonable") and run
# MAGIC   `mlflow.evaluate` for quality/cost/latency scoring.
# MAGIC - Deploy via `agents.deploy` (from the `databricks-agents` package) or
# MAGIC   a Databricks App, once wrapped, for a callable endpoint instead of
# MAGIC   "run the notebook."
# MAGIC - Swap `LLM_ENDPOINT` to compare models, e.g. `databricks-gpt-oss-120b`
# MAGIC   for stronger reasoning or `databricks-llama-4-maverick`.
