# Databricks components used here

A plain-language glossary of the actual Databricks (and adjacent) pieces
this project touches, why each one matters once you're putting an agent
in front of real data, and where to see it in this repo.

## Unity Catalog

- **What it is:** the governance layer over everything else — catalogs,
  schemas, tables, and volumes all live in it, with permissions attached
- **Why it matters for agents:** an agent's tools are only as safe as the
  data access underneath them
- A tool built on a Unity Catalog function inherits real access control —
  you can grant or revoke who (or what) can call it, same as any other
  governed object
- **Where:** every table, volume, and function in this repo lives under a
  catalog/schema, e.g. `workspace.bas_sales`

## Delta Lake tables

- **What it is:** the table format everything is stored in — reliable
  writes, a query-able transaction history, schema enforcement
- **Why it matters:** an agent reading bad or half-written data gives
  confidently wrong answers
- Delta's guarantees are part of why the answer underneath the agent can
  be trusted in the first place
- **Where:** `ingest.py` creates one Delta table per source CSV

## Volumes

- **What it is:** a managed folder inside Unity Catalog for files that
  aren't tables — raw CSVs on the way in, JSON/markdown output on the way
  out
- **Why it matters:** raw files need to land *somewhere* governed before
  they become a table
- Volumes are that landing zone, versus a random path with no access
  control
- **Where:** the `raw_files` volume holds uploaded CSVs; the `profiles`
  volume holds the data profiler's output

## SQL Warehouses + the Statement Execution API

- **What it is:** the compute that actually runs SQL, plus a REST API for
  running it programmatically instead of through a UI
- **Why it matters:** every script in `agent_toolkit/` — ingestion,
  profiling, dashboard generation — runs as scripted SQL against a
  warehouse, not manual clicks
- That's what makes "point this at a new dataset" a real, repeatable
  operation instead of a one-off
- **Where:** `db_sql.py`'s `run_sql()` in both `sales/databricks_agent/`
  and `agent_toolkit/`

## Unity Catalog Functions (as agent tools)

- **What it is:** a SQL (or Python) function registered in Unity Catalog,
  with a comment on the function and each parameter describing what it
  does
- **Why it matters:** those comments aren't just documentation — an LLM
  tool-calling framework reads them directly to decide when and how to
  call the function
- The function's own metadata *is* the tool definition — nothing to keep
  in sync by hand
- Because the function body is fixed SQL, it can't be talked into doing
  anything the SQL wasn't written to do
- **Where:** the 4 tools in `sales/databricks_agent/tools.sql`

## Lakeview (AI/BI) dashboards

- **What it is:** Databricks' native dashboarding tool — charts backed by
  SQL datasets, built here entirely through its REST API rather than the
  UI
- **Why it matters for agents:** a dashboard is the human-facing
  counterpart to an agent — same governed tables, but for someone who
  wants to look at trends themselves rather than ask a question
- Building one alongside the agent is a reminder that agents don't
  replace this, they sit next to it
- **Where:** `dashboard_generator.py` builds one automatically from
  whatever the data profiler found, for any dataset

## Foundation Model APIs

- **What it is:** hosted LLMs (Llama, GPT-OSS, Claude, and others)
  callable through a Databricks-managed endpoint
- **Why it matters:** no separate API key or provider account needed —
  the model call inherits the same governance and audit trail as
  everything else in the workspace
- **Where:** `ChatDatabricks(endpoint="databricks-meta-llama-3-3-70b-instruct")`
  in both agent notebooks; also used for embedding models
  (`databricks-gte-large-en`) in `knowledge_assistant/pdf_to_vector_index.py`

## Vector Search

- **What it is:** a governed, searchable index of embeddings (numeric
  representations of text), built and kept in sync with a source Delta
  table
- **Why it matters for agents:** it's what lets an agent (or a no-code
  Knowledge Assistant) answer questions from *unstructured* text --
  PDFs, docs -- the same way UC functions let it answer questions from
  structured tables
- A **Delta Sync Index** stays wired to its source table and refreshes
  with one `.sync()` call, instead of vectors being pushed/upserted by
  hand on every data change
- **Where:** `knowledge_assistant/pdf_to_vector_index.py` builds one from
  a table of PDF text chunks

## Agent Bricks / Knowledge Assistant

- **What it is:** a no-code Databricks feature that turns a Vector Search
  index into a question-answering chatbot -- point it at an index, no
  retrieval or prompting code to write
- **Why it matters:** the fastest path from "I have an index" to "I have
  something to ask questions to" -- worth knowing about specifically so
  you don't hand-build a RAG agent when this already does it
- **Where:** the intended next step after `knowledge_assistant/pdf_to_vector_index.py`
  builds the index -- see that notebook's "Next steps" cell

## AI Functions

- **What it is:** calling an LLM directly from a SQL query — `ai_query`,
  `ai_classify`, and similar — no agent, no Python, no tools
- **Why it matters:** not every "add some AI" problem needs an agent
- This is the lightest-weight option, worth knowing about specifically so
  you don't reach for a full agent when a single SQL function would do
- **Where:** the `ai_classify` restock-urgency demo in
  `sales/databricks_agent/agent_notebook.py`

## MLflow Tracing

- **What it is:** automatic step-by-step logging of an agent's LLM and
  tool calls — one line (`mlflow.langchain.autolog()`) turns it on
- **Why it matters:** "the agent gave a weird answer" is unanswerable
  without seeing which tool it called and what that tool actually
  returned
- Tracing is what makes an agent's behavior inspectable instead of a
  black box
- **Where:** turned on in both agent notebooks

## Databricks CLI + Jobs API

- **What it is:** everything above, done by running commands and scripts
  against Databricks' REST APIs, rather than clicking through the web UI
- **Why it matters:** it's what makes this whole repo re-runnable and
  reviewable
- Every table, function, and dashboard here was created by a command
  that's still sitting in this repo, not a UI action nobody can see after
  the fact
- **Where:** every script in `agent_toolkit/` and `sales/databricks_agent/`

## Not a Databricks component: LangGraph

- Worth being precise about, since it's easy to blur
- The agent's actual tool-calling loop (`create_react_agent`) comes from
  **LangGraph**, a separate open-source library, not something Databricks
  built
- Databricks provides the LLM endpoint and the governed tools; LangGraph
  decides, turn by turn, which tool to call and when to stop and answer

## How this helps data engineering

- **Governance travels with the tool.** An agent built on Unity Catalog
  functions and tables can't bypass the same permissions a human analyst
  is bound by — the access-control problem doesn't need a separate answer
  for "what if an LLM is asking"
- **Reliability underneath is what makes answers trustworthy.** Delta's
  transaction guarantees, schema enforcement, and the data profiler's
  null/type checks are all classic data engineering concerns — they're
  also exactly what stops an agent from confidently reporting garbage
- **Everything here is a script, not a click.** Ingestion, profiling,
  dashboards, and tool registration all run through CLI/API calls that
  live in this repo — the same reproducibility bar as any other data
  pipeline, instead of one-off setup nobody can rerun
- **Observability isn't optional once an LLM is in the loop.** MLflow
  Tracing gives the same kind of step-by-step visibility a data engineer
  already expects from a pipeline run — which tool ran, with what
  arguments, returning what
- **The generic toolkit is a data-onboarding problem, not an AI
  problem.** Making ingestion/profiling/dashboards work on *any* new
  dataset with zero new code is the same challenge as standing up a new
  data source in a normal pipeline — the agent tools were the easy part
  once that groundwork existed
