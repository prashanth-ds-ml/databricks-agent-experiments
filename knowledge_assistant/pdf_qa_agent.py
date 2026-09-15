# Databricks notebook source
# MAGIC %md
# MAGIC # PDF Q&A Agent (Vector Search tool, no Agent Bricks)
# MAGIC - A tool-calling agent that answers questions from the PDF(s) indexed
# MAGIC   by `pdf_to_vector_index.py` -- same LangGraph ReAct pattern as
# MAGIC   [`sales/databricks_agent/agent_notebook.py`](../sales/databricks_agent/agent_notebook.py),
# MAGIC   but with one tool: a Vector Search lookup instead of UC SQL
# MAGIC   functions.
# MAGIC - **Why this exists instead of Agent Bricks' Knowledge Assistant:**
# MAGIC   this workspace's Knowledge Assistant deployment hit a platform-side
# MAGIC   404 on an internal Databricks model (`instructed-retriever-1`) that
# MAGIC   isn't fixable from a notebook or the CLI. The Vector Search index
# MAGIC   itself was confirmed healthy (queried directly, got real relevant
# MAGIC   results) -- this notebook is a from-scratch alternative that only
# MAGIC   depends on things this workspace already has working: the index,
# MAGIC   and a pay-per-token chat model.
# MAGIC - Every model in `LLM_ENDPOINT`'s dropdown (widget below) is already
# MAGIC   deployed in this workspace -- no separate account, API key, or
# MAGIC   billing setup beyond what your Databricks workspace already has.
# MAGIC
# MAGIC **Prerequisites:** run `pdf_to_vector_index.py` first so the index
# MAGIC named below actually exists.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Widgets

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "knowledge_assistant", "Schema")
dbutils.widgets.text("vs_endpoint", "knowledge_assistant_vs", "Vector Search endpoint name")
dbutils.widgets.text("vs_index_name", "doc_chunks_index", "Vector Search index name")
dbutils.widgets.dropdown(
    "llm_endpoint", "databricks-meta-llama-3-3-70b-instruct",
    [
        "databricks-meta-llama-3-3-70b-instruct",
        "databricks-meta-llama-3-1-8b-instruct",
        "databricks-gpt-oss-120b",
        "databricks-gpt-oss-20b",
        "databricks-llama-4-maverick",
        "databricks-qwen35-122b-a10b",
    ],
    "LLM endpoint",
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Install dependencies (with retry)
# MAGIC Same pinned `langgraph`/`langgraph-prebuilt` versions and the same
# MAGIC `unitycatalog-ai[databricks]`-in-the-install-line fix as the sales
# MAGIC agent notebook -- see that notebook's install cell for the full
# MAGIC explanation of why. `databricks-vectorsearch` is the only addition,
# MAGIC needed to query the index this agent's tool wraps.

# COMMAND ----------

import subprocess
import sys
import time

PIP_ARGS = [
    "-U", "databricks-langchain", "databricks-vectorsearch", "unitycatalog-ai[databricks]", "mlflow",
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
# MAGIC ## 3. Configuration

# COMMAND ----------

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
VS_ENDPOINT = dbutils.widgets.get("vs_endpoint")
FULL_INDEX_NAME = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('vs_index_name')}"
LLM_ENDPOINT = dbutils.widgets.get("llm_endpoint")

print(f"Index:  {FULL_INDEX_NAME}")
print(f"LLM:    {LLM_ENDPOINT}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. The retriever tool
# MAGIC Wraps `index.similarity_search` as a plain LangChain `@tool` -- the
# MAGIC LLM decides when to call it, same as any UC-function tool in the
# MAGIC other notebooks. Formats each hit with its source file/page so the
# MAGIC model can cite where an answer came from.
# MAGIC
# MAGIC Assumes the index was built with **Databricks-computed embeddings**
# MAGIC (the `embedding_source = databricks` default in `pdf_to_vector_index.py`),
# MAGIC so a plain text query works directly. If you built the index with
# MAGIC the `huggingface` option instead, this tool needs the same HF model
# MAGIC to embed the query first and pass `query_vector=` -- see that
# MAGIC notebook's `search()` helper for the pattern.

# COMMAND ----------

from databricks.vector_search.client import VectorSearchClient
from langchain_core.tools import tool

vsc = VectorSearchClient()
index = vsc.get_index(endpoint_name=VS_ENDPOINT, index_name=FULL_INDEX_NAME)


@tool
def search_documents(query: str) -> str:
    """Search the indexed PDF(s) for text relevant to the query. Returns
    the top matching chunks with their source file and page number. Call
    this for any question about the document's content -- don't guess."""
    results = index.similarity_search(
        query_text=query,
        columns=["source_file", "page_number", "content"],
        num_results=4,
    )
    rows = results.get("result", {}).get("data_array", [])
    if not rows:
        return "No relevant passages found in the indexed document(s)."
    return "\n\n".join(
        f"[{source_file}, page {int(page_number)}, relevance {score:.2f}]\n{content}"
        for source_file, page_number, content, score in rows
    )


# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Wire up the agent
# MAGIC Same LangGraph ReAct pattern as the other two agents in this repo --
# MAGIC one tool this time, but the loop (call tool, read result, decide
# MAGIC whether to call again or answer) is identical.

# COMMAND ----------

import mlflow
from databricks_langchain import ChatDatabricks
from langgraph.prebuilt import create_react_agent

mlflow.langchain.autolog()

llm = ChatDatabricks(endpoint=LLM_ENDPOINT)

SYSTEM_PROMPT = (
    "You answer questions using only the search_documents tool -- never "
    "guess or use outside knowledge. Always call the tool at least once "
    "before answering. Cite the source file and page number for any fact "
    "you state. If the tool returns nothing relevant, say so plainly "
    "instead of making something up."
)

agent = create_react_agent(llm, [search_documents], prompt=SYSTEM_PROMPT)


def ask(question: str) -> str:
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return result["messages"][-1].content


# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Try it out
# MAGIC Replace the question below with one about your own uploaded PDF.

# COMMAND ----------

print(ask("What is this document about? Summarize it in a few sentences."))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next steps
# MAGIC - Ask follow-up questions in a new cell: `print(ask("..."))`.
# MAGIC - Combine this tool with the sales agent's UC-function tools in one
# MAGIC   agent, so it can answer both structured (SQL) and unstructured
# MAGIC   (PDF) questions -- just merge the tool lists and system prompts.
# MAGIC - If Databricks resolves the `instructed-retriever-1` issue on
# MAGIC   their side later, the Agent Bricks Knowledge Assistant becomes a
# MAGIC   no-code alternative to this notebook, pointed at the same index --
# MAGIC   neither approach requires rebuilding the index itself.
