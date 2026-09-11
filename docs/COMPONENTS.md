# Databricks components used here

A plain-language glossary of the actual Databricks (and adjacent)
pieces this project touches, why each one matters once you're putting an
agent in front of real data, and where to see it in this repo.

## Unity Catalog

**What it is:** the governance layer over everything else -- catalogs,
schemas, tables, and volumes all live in it, with permissions attached.

**Why it matters for agents:** an agent's tools are only as safe as the
data access underneath them. A tool built on a Unity Catalog function
inherits real access control -- you can grant or revoke who (or what) can
call it, same as any other governed object.

**Where:** every table, volume, and function in this repo lives under a
catalog/schema, e.g. `workspace.bas_sales`.

## Delta Lake tables

**What it is:** the table format everything is stored in -- reliable
writes, a query-able transaction history, schema enforcement.

**Why it matters:** an agent reading bad or half-written data gives
confidently wrong answers. Delta's guarantees are part of why the answer
underneath the agent can be trusted in the first place.

**Where:** `ingest.py` creates one Delta table per source CSV.

## Volumes

**What it is:** a managed folder inside Unity Catalog for files that
aren't tables -- raw CSVs on the way in, JSON/markdown output on the way
out.

**Why it matters:** raw files need to land *somewhere* governed before
they become a table; volumes are that landing zone, versus a random path
with no access control.

**Where:** the `raw_files` volume holds uploaded CSVs; the `profiles`
volume holds the data profiler's output.

## SQL Warehouses + the Statement Execution API

**What it is:** the compute that actually runs SQL, and a REST API for
running it programmatically instead of through a UI.

**Why it matters:** every script in `agent_toolkit/` -- ingestion,
profiling, dashboard generation -- runs as scripted SQL against a
warehouse, not manual clicks. That's what makes "point this at a new
dataset" a real, repeatable operation instead of a one-off.

**Where:** `db_sql.py`'s `run_sql()` in both `sales/databricks_agent/`
and `agent_toolkit/`.

## Unity Catalog Functions (as agent tools)

**What it is:** a SQL (or Python) function registered in Unity Catalog,
with a comment on the function and each parameter describing what it
does.

**Why it matters:** those comments aren't just documentation -- an LLM
tool-calling framework reads them directly to decide when and how to call
the function. The function's own metadata *is* the tool definition, nothing
to keep in sync by hand. And because the function body is fixed SQL, it
can't be talked into doing anything the SQL wasn't written to do.

**Where:** the 4 tools in `sales/databricks_agent/tools.sql`.

## Lakeview (AI/BI) dashboards

**What it is:** Databricks' native dashboarding tool -- charts backed by
SQL datasets, built here entirely through its REST API rather than the UI.

**Why it matters for agents:** a dashboard is the human-facing counterpart
to an agent -- same governed tables, but for someone who wants to look at
trends themselves rather than ask a question. Building one alongside the
agent is a reminder that agents don't replace this, they sit next to it.

**Where:** `dashboard_generator.py` builds one automatically from whatever
the data profiler found, for any dataset.

## Foundation Model APIs

**What it is:** hosted LLMs (Llama, GPT-OSS, Claude, and others) callable
through a Databricks-managed endpoint -- no separate API key or provider
account needed.

**Where:** `ChatDatabricks(endpoint="databricks-meta-llama-3-3-70b-instruct")`
in both agent notebooks.

## AI Functions

**What it is:** calling an LLM directly from a SQL query --
`ai_query`, `ai_classify`, and similar -- no agent, no Python, no tools.

**Why it matters:** not every "add some AI" problem needs an agent. This
is the lightest-weight option, worth knowing about specifically so you
don't reach for a full agent when a single SQL function would do.

**Where:** the `ai_classify` restock-urgency demo in
`sales/databricks_agent/agent_notebook.py`.

## MLflow Tracing

**What it is:** automatic step-by-step logging of an agent's LLM and tool
calls -- one line (`mlflow.langchain.autolog()`) turns it on.

**Why it matters:** "the agent gave a weird answer" is unanswerable
without seeing which tool it called and what that tool actually returned.
Tracing is what makes an agent's behavior inspectable instead of a black
box.

**Where:** turned on in both agent notebooks.

## Databricks CLI + Jobs API

**What it is:** everything above, done by running commands and scripts
against Databricks' REST APIs, rather than clicking through the web UI.

**Why it matters:** it's what makes this whole repo re-runnable and
reviewable -- every table, function, and dashboard here was created by a
command that's still sitting in this repo, not a UI action nobody can see
after the fact.

## Not a Databricks component: LangGraph

Worth being precise about, since it's easy to blur: the agent's actual
tool-calling loop (`create_react_agent`) comes from **LangGraph**, a
separate open-source library, not something Databricks built. Databricks
provides the LLM endpoint and the governed tools; LangGraph is what
decides, turn by turn, which tool to call and when to stop and answer.
