# Databricks notebook source
# MAGIC %md
# MAGIC # PDF Q&A (zero billed services)
# MAGIC - Answers questions from the table built by
# MAGIC   [`free_pdf_to_embeddings.py`](free_pdf_to_embeddings.py), using
# MAGIC   only things that don't bill beyond the cluster you're already
# MAGIC   running: cosine similarity computed directly in Python (no Vector
# MAGIC   Search endpoint), and a small open-source Hugging Face model
# MAGIC   loaded onto this cluster for generation (no Databricks-hosted,
# MAGIC   pay-per-token chat model).
# MAGIC - **Not a LangGraph tool-calling agent** like `pdf_qa_agent.py` --
# MAGIC   small local models are unreliable at the structured tool-calling
# MAGIC   format that pattern depends on. Instead this is a plain,
# MAGIC   fixed retrieve-then-generate pipeline: always search the table,
# MAGIC   always hand the results to the model as context. Simpler, and a
# MAGIC   better fit for a small local model than an agentic loop.
# MAGIC - Retrieval loads the whole chunks table onto the driver and scores
# MAGIC   it with plain numpy -- fine for one or a few PDFs, not built to
# MAGIC   scale to a large document library (that's what `pdf_qa_agent.py` +
# MAGIC   Vector Search is for, at the cost of a billed endpoint).
# MAGIC
# MAGIC ## Before you run this
# MAGIC 1. Run `free_pdf_to_embeddings.py` first.
# MAGIC 2. **Cluster**: Databricks Runtime 14.3 LTS+, no GPU required for
# MAGIC    the default small models below, but give it enough memory to
# MAGIC    hold a ~1-2B parameter model (a single-node cluster with 16GB+
# MAGIC    RAM, e.g. `r5.large` or bigger, is plenty).
# MAGIC 3. Run All -- the first run downloads both Hugging Face models to
# MAGIC    the cluster, which takes a minute or two; re-runs are faster.

# COMMAND ----------

# MAGIC %md ## 1. Widgets

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "knowledge_assistant", "Schema")
dbutils.widgets.text("chunks_table", "doc_chunks_free", "Chunks table name")
dbutils.widgets.text("hf_embedding_model", "sentence-transformers/all-MiniLM-L6-v2", "Hugging Face embedding model (must match the indexing notebook)")
dbutils.widgets.dropdown(
    "hf_llm_model", "Qwen/Qwen2.5-1.5B-Instruct",
    ["Qwen/Qwen2.5-0.5B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct", "Qwen/Qwen2.5-3B-Instruct"],
    "Hugging Face chat model (bigger = better answers, slower on CPU)",
)
dbutils.widgets.text("top_k", "4", "Chunks to retrieve per question")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Install dependencies (with retry)

# COMMAND ----------

import subprocess
import sys
import time

PIP_ARGS = ["-U", "sentence-transformers", "transformers", "accelerate"]


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

# MAGIC %md ## 3. Configuration

# COMMAND ----------

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
FULL_CHUNKS_TABLE = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('chunks_table')}"
HF_EMBEDDING_MODEL = dbutils.widgets.get("hf_embedding_model")
HF_LLM_MODEL = dbutils.widgets.get("hf_llm_model")
TOP_K = int(dbutils.widgets.get("top_k"))

print(f"Chunks table:    {FULL_CHUNKS_TABLE}")
print(f"Embedding model: {HF_EMBEDDING_MODEL}")
print(f"Chat model:      {HF_LLM_MODEL}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Load the chunks table onto the driver
# MAGIC `toPandas()` pulls every row into driver memory -- the scale limit
# MAGIC mentioned in the intro. Fine for one PDF's worth of chunks (dozens
# MAGIC to low thousands of rows); a large document library would need a
# MAGIC real vector index (Vector Search, or a library like FAISS) instead.

# COMMAND ----------

import numpy as np

chunks_pdf = spark.table(FULL_CHUNKS_TABLE).toPandas()
if chunks_pdf.empty:
    raise RuntimeError(f"{FULL_CHUNKS_TABLE} has 0 rows -- run free_pdf_to_embeddings.py first.")

embedding_matrix = np.array(chunks_pdf["embedding"].tolist())
print(f"Loaded {len(chunks_pdf)} chunks, embedding dim {embedding_matrix.shape[1]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Load the models
# MAGIC Both cached in memory for the rest of this notebook session --
# MAGIC loaded once here, reused by every question below.

# COMMAND ----------

from sentence_transformers import SentenceTransformer
from transformers import pipeline

embed_model = SentenceTransformer(HF_EMBEDDING_MODEL)
generator = pipeline("text-generation", model=HF_LLM_MODEL, torch_dtype="auto", device_map="auto")
print("Models loaded")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Retrieval: cosine similarity in plain numpy
# MAGIC No index, no server -- just normalize every embedding (including the
# MAGIC query's) to unit length and take a dot product, which is exactly
# MAGIC what cosine similarity is. Sort, take the top K.

# COMMAND ----------


def retrieve(query: str, k: int = TOP_K):
    query_vector = np.array(embed_model.encode([query])[0])
    query_norm = query_vector / (np.linalg.norm(query_vector) + 1e-10)
    matrix_norm = embedding_matrix / (np.linalg.norm(embedding_matrix, axis=1, keepdims=True) + 1e-10)
    scores = matrix_norm @ query_norm
    top_idx = np.argsort(-scores)[:k]
    return chunks_pdf.iloc[top_idx].assign(score=scores[top_idx])


# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Generation: stuff the retrieved chunks into the local model's prompt
# MAGIC A fixed pipeline, not an agent loop: always retrieve, always pass the
# MAGIC results as context, ask the model to answer only from that context.

# COMMAND ----------

SYSTEM_PROMPT = (
    "Answer the user's question using only the provided context. If the "
    "context doesn't contain the answer, say you don't know rather than "
    "guessing. Cite the source file and page number for facts you use."
)


def ask(question: str, k: int = TOP_K) -> str:
    hits = retrieve(question, k)
    context = "\n\n".join(
        f"[{row.source_file}, page {int(row.page_number)}]\n{row.content}"
        for row in hits.itertuples()
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]
    output = generator(messages, max_new_tokens=300, do_sample=False)
    return output[0]["generated_text"][-1]["content"]


# COMMAND ----------

# MAGIC %md ## 8. Try it out

# COMMAND ----------

print(ask("What is this document about? Summarize it in a few sentences."))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next steps
# MAGIC - Ask follow-ups: `print(ask("..."))` in a new cell.
# MAGIC - If answer quality isn't good enough, try a bigger `hf_llm_model`
# MAGIC   widget value (slower, better) -- or, if you decide a little
# MAGIC   billed usage is worth it later, `pdf_qa_agent.py` gets noticeably
# MAGIC   better answers from a much larger Databricks-hosted model, reading
# MAGIC   from the same PDF via a Vector Search index built from this same
# MAGIC   source data.
