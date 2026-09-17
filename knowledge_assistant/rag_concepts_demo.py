# Databricks notebook source
# MAGIC %md
# MAGIC # RAG, Explained With Your Own PDF
# MAGIC A presentation notebook, not a pipeline -- run this live while
# MAGIC talking someone through how retrieval-augmented generation (RAG)
# MAGIC actually works, using the real chunks and embeddings from
# MAGIC [`free_pdf_to_embeddings.py`](free_pdf_to_embeddings.py) (run that
# MAGIC first). Five parts, in the order to present them:
# MAGIC
# MAGIC 1. **Chunking** -- what actually happens to the PDF's text
# MAGIC 2. **The embedding space** -- an interactive plot showing similar
# MAGIC    chunks cluster together, and where a real question lands in
# MAGIC    that space relative to the chunks it retrieves
# MAGIC 3. **Retrieval + metadata** -- what comes back for a question, and
# MAGIC    why each piece of metadata earns its place
# MAGIC 4. **Question -> Answer** -- the full chain, end to end
# MAGIC 5. **What would make this better** -- concrete next levers, not
# MAGIC    hand-waving
# MAGIC
# MAGIC Zero billed services, same as the pipeline it visualizes -- local
# MAGIC embeddings, local small chat model, a couple of open-source Python
# MAGIC libraries for the plots.
# MAGIC
# MAGIC **New to embeddings, or presenting to someone who is?** Run
# MAGIC [`embeddings_explained.py`](embeddings_explained.py) first -- a
# MAGIC 5-minute primer on the same techniques using simple example
# MAGIC sentences, so the plot below reads as a confirmation of something
# MAGIC already understood, not a new idea to take on faith.
# MAGIC
# MAGIC **Before you run this:** run `free_pdf_to_embeddings.py` **once**
# MAGIC so the chunks table exists -- not every time. Once that table's
# MAGIC there, re-running this notebook reuses it directly; you only need
# MAGIC to re-run `free_pdf_to_embeddings.py` again if you upload a new/
# MAGIC different PDF. Cluster: Databricks Runtime 14.3 LTS+, no GPU
# MAGIC required.

# COMMAND ----------

# MAGIC %md ## 1. Widgets

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "knowledge_assistant", "Schema")
dbutils.widgets.text("pdf_volume", "source_docs", "PDF volume name")
dbutils.widgets.text("chunks_table", "doc_chunks_free", "Chunks table name")
dbutils.widgets.text("hf_embedding_model", "sentence-transformers/all-MiniLM-L6-v2", "Hugging Face embedding model (must match free_pdf_to_embeddings.py)")
dbutils.widgets.dropdown("hf_llm_model", "Qwen/Qwen2.5-1.5B-Instruct", ["Qwen/Qwen2.5-0.5B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct", "Qwen/Qwen2.5-3B-Instruct"], "Hugging Face chat model")
dbutils.widgets.text("num_clusters", "5", "Clusters to color the embedding plot by")
dbutils.widgets.text("top_k", "4", "Chunks to retrieve per question")
dbutils.widgets.text("sample_question", "What is this document about?", "Sample question to demo with")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Install dependencies (with retry)
# MAGIC `scikit-learn` for PCA (compressing embeddings to 2D for plotting)
# MAGIC and KMeans (grouping similar chunks for coloring); `plotly` for an
# MAGIC interactive plot (hover a point to read the chunk).

# COMMAND ----------

import subprocess
import sys
import time

PIP_ARGS = ["-U", "pypdf", "sentence-transformers", "transformers", "accelerate", "scikit-learn", "plotly"]


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
PDF_VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{dbutils.widgets.get('pdf_volume')}"
FULL_CHUNKS_TABLE = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('chunks_table')}"
HF_EMBEDDING_MODEL = dbutils.widgets.get("hf_embedding_model")
HF_LLM_MODEL = dbutils.widgets.get("hf_llm_model")
NUM_CLUSTERS = int(dbutils.widgets.get("num_clusters"))
TOP_K = int(dbutils.widgets.get("top_k"))
SAMPLE_QUESTION = dbutils.widgets.get("sample_question")

print(f"Chunks table: {FULL_CHUNKS_TABLE}")
print(f"Sample question for this walkthrough: {SAMPLE_QUESTION!r}")

# COMMAND ----------

# MAGIC %md
# MAGIC # Part 1: Chunking
# MAGIC A PDF's text can't be handed to an embedding model or an LLM all at
# MAGIC once -- both have a limited amount of text they can look at per
# MAGIC call. So the first step is always: cut the document into smaller,
# MAGIC overlapping pieces ("chunks") small enough to embed and retrieve
# MAGIC individually.
# MAGIC
# MAGIC Below: one real page from your PDF, before and after chunking.

# COMMAND ----------

from pypdf import PdfReader

pdf_files = sorted(f.name for f in dbutils.fs.ls(PDF_VOLUME_PATH) if f.name.lower().endswith(".pdf"))
if not pdf_files:
    raise RuntimeError(f"No PDFs found in {PDF_VOLUME_PATH} -- run free_pdf_to_embeddings.py first.")

reader = PdfReader(f"{PDF_VOLUME_PATH}/{pdf_files[0]}")
demo_page_number = min(2, len(reader.pages))
demo_page_text = reader.pages[demo_page_number - 1].extract_text() or ""

print(f"--- Raw text, page {demo_page_number} of {pdf_files[0]} ({len(demo_page_text)} characters) ---\n")
print(demo_page_text[:600] + ("..." if len(demo_page_text) > 600 else ""))

# COMMAND ----------

import re


def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            boundary = text.rfind(". ", start, end)
            if boundary <= start:
                boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end >= n:
            break
        start = max(end - chunk_overlap, start + 1)
    return [c for c in chunks if c]


demo_chunks = chunk_text(demo_page_text, chunk_size=1000, chunk_overlap=150)
print(f"That one page became {len(demo_chunks)} chunk(s):\n")
for i, c in enumerate(demo_chunks):
    print(f"[chunk {i}, {len(c)} chars] {c[:150]}...\n")

# COMMAND ----------

# MAGIC %md
# MAGIC **Why overlap matters:** without it, a sentence that happens to sit
# MAGIC right on a chunk boundary gets split in half, and neither half alone
# MAGIC makes sense. A ~150-character overlap means the end of one chunk
# MAGIC re-appears at the start of the next, so context survives the cut.
# MAGIC
# MAGIC Now zoom out from one page to every chunk in the whole document --
# MAGIC this is what actually got embedded and stored.

# COMMAND ----------

import numpy as np

chunks_pdf = spark.table(FULL_CHUNKS_TABLE).toPandas()
if chunks_pdf.empty:
    raise RuntimeError(f"{FULL_CHUNKS_TABLE} is empty -- run free_pdf_to_embeddings.py first.")

embedding_matrix = np.array(chunks_pdf["embedding"].tolist())
chunk_lengths = chunks_pdf["content"].str.len()

import plotly.express as px

fig = px.histogram(
    chunk_lengths, nbins=20,
    labels={"value": "Characters per chunk"},
    title=f"Chunk size distribution -- {len(chunks_pdf)} chunks across {chunks_pdf['source_file'].nunique()} PDF(s)",
)
fig.update_layout(showlegend=False, height=350)
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC # Part 2: The Embedding Space
# MAGIC Every chunk gets converted into a list of numbers (an embedding) --
# MAGIC a point in a high-dimensional space (384 dimensions for the default
# MAGIC model) chosen so that **chunks with similar meaning end up as
# MAGIC nearby points**, regardless of the exact words used.
# MAGIC
# MAGIC 384 dimensions can't be plotted directly, so PCA compresses it down
# MAGIC to 2, keeping as much of the original structure as a flat plot can
# MAGIC show. Color groups are found automatically (KMeans) -- points of the
# MAGIC same color are chunks the model considers similar in meaning.
# MAGIC **Hover any point** to read the actual chunk behind it.

# COMMAND ----------

from sklearn.cluster import KMeans
from sklearn.decomposition import PCA

pca = PCA(n_components=2, random_state=0)
coords_2d = pca.fit_transform(embedding_matrix)

n_clusters = max(2, min(NUM_CLUSTERS, len(chunks_pdf)))
cluster_labels = KMeans(n_clusters=n_clusters, random_state=0, n_init=10).fit_predict(embedding_matrix)

hover_text = [
    f"{row.source_file}, page {int(row.page_number)}<br>{row.content[:160]}..."
    for row in chunks_pdf.itertuples()
]

# COMMAND ----------

from sentence_transformers import SentenceTransformer

embed_model = SentenceTransformer(HF_EMBEDDING_MODEL)


def retrieve(query: str, k: int = TOP_K):
    query_vector = np.array(embed_model.encode([query])[0])
    query_norm = query_vector / (np.linalg.norm(query_vector) + 1e-10)
    matrix_norm = embedding_matrix / (np.linalg.norm(embedding_matrix, axis=1, keepdims=True) + 1e-10)
    scores = matrix_norm @ query_norm
    top_idx = np.argsort(-scores)[:k]
    return top_idx, scores[top_idx], query_vector


top_idx, top_scores, sample_query_vector = retrieve(SAMPLE_QUESTION, TOP_K)
sample_query_2d = pca.transform(sample_query_vector.reshape(1, -1))[0]

# COMMAND ----------

import plotly.graph_objects as go

fig = go.Figure()

fig.add_trace(go.Scatter(
    x=coords_2d[:, 0], y=coords_2d[:, 1],
    mode="markers",
    marker=dict(size=9, color=cluster_labels, colorscale="Viridis", line=dict(width=0.5, color="white")),
    text=hover_text,
    hoverinfo="text",
    name="Chunks",
))

for idx in top_idx:
    fig.add_trace(go.Scatter(
        x=[sample_query_2d[0], coords_2d[idx, 0]],
        y=[sample_query_2d[1], coords_2d[idx, 1]],
        mode="lines",
        line=dict(color="crimson", width=1, dash="dot"),
        hoverinfo="skip",
        showlegend=False,
    ))

fig.add_trace(go.Scatter(
    x=[sample_query_2d[0]], y=[sample_query_2d[1]],
    mode="markers+text",
    marker=dict(size=20, color="crimson", symbol="star", line=dict(width=1, color="white")),
    text=["Your question"], textposition="top center",
    hoverinfo="text",
    hovertext=[SAMPLE_QUESTION],
    name="Query",
))

fig.update_layout(
    title=f"Chunk embeddings (colored by {n_clusters} auto-discovered groups) -- red lines connect your\n"
          f"question to the {TOP_K} chunks retrieval actually picked",
    showlegend=False, height=650,
    xaxis_title="PCA dimension 1", yaxis_title="PCA dimension 2",
)
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC **What to point out live:** the red star (your question) lands
# MAGIC closest to a specific cluster of chunks, not scattered randomly --
# MAGIC that's the model recognizing which part of the document your
# MAGIC question is actually about, before any LLM is even involved. The
# MAGIC dotted lines are the chunks retrieval picked as most relevant --
# MAGIC they should visibly be among the nearest points to the star.

# COMMAND ----------

# MAGIC %md
# MAGIC # Part 3: Retrieval + Metadata
# MAGIC Finding the nearest chunks is only half of retrieval -- what you
# MAGIC attach to each chunk (its metadata) is what makes the result
# MAGIC *usable* rather than just a floating piece of text.

# COMMAND ----------

hits = chunks_pdf.iloc[top_idx].copy()
hits["score"] = top_scores
hits["preview"] = hits["content"].str.slice(0, 160) + "..."
display(hits[["score", "source_file", "page_number", "chunk_index", "preview"]])

# COMMAND ----------

# MAGIC %md
# MAGIC **Why each column earns its place:**
# MAGIC - `score` -- how confident the match is; lets you drop weak matches
# MAGIC   instead of always forcing in a fixed number of results
# MAGIC - `source_file` + `page_number` -- exactly what makes an answer
# MAGIC   **citable**: "page 4 says..." instead of an unverifiable claim
# MAGIC - `chunk_index` -- lets you pull in the chunk right before/after a
# MAGIC   match, if a question needs more surrounding context than one chunk
# MAGIC   holds
# MAGIC
# MAGIC A real PDF also carries its own document-level metadata (separate
# MAGIC from anything this pipeline computed) -- worth attaching to every
# MAGIC chunk from that file, e.g. for filtering a multi-document library
# MAGIC down to "just this author" or "just this report":

# COMMAND ----------

pdf_title = getattr(reader.metadata, "title", None) or "(not set in this PDF)"
pdf_author = getattr(reader.metadata, "author", None) or "(not set in this PDF)"
print(f"Document title:  {pdf_title}")
print(f"Document author: {pdf_author}")

# COMMAND ----------

# MAGIC %md
# MAGIC # Part 4: Question -> Answer
# MAGIC The full chain: retrieve the chunks above, hand them to a small
# MAGIC local model as context, and ask it to answer *only* from that
# MAGIC context -- not from whatever it happened to memorize during
# MAGIC training.

# COMMAND ----------

from transformers import pipeline

generator = pipeline("text-generation", model=HF_LLM_MODEL, torch_dtype="auto", device_map="auto")

SYSTEM_PROMPT = (
    "Answer the user's question using only the provided context. If the "
    "context doesn't contain the answer, say you don't know rather than "
    "guessing. Cite the source file and page number for facts you use."
)

context = "\n\n".join(
    f"[{row.source_file}, page {int(row.page_number)}]\n{row.content}"
    for row in hits.itertuples()
)
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {SAMPLE_QUESTION}"},
]
answer = generator(messages, max_new_tokens=300, do_sample=False)[0]["generated_text"][-1]["content"]

print(f"Q: {SAMPLE_QUESTION}\n")
print(f"A: {answer}")

# COMMAND ----------

# MAGIC %md
# MAGIC # Part 5: What Would Make This Better
# MAGIC Every lever below is a real, separately-tunable piece of this
# MAGIC pipeline -- none of it requires starting over.
# MAGIC
# MAGIC | Lever | What it does | Effort |
# MAGIC |---|---|---|
# MAGIC | **Smarter chunking** | Split on headings/paragraphs instead of a fixed character count, so a chunk is always one coherent idea, not an arbitrary slice | Low |
# MAGIC | **Bigger/better embedding model** | Swap `all-MiniLM-L6-v2` (384 dims, fast) for a larger open model or a Databricks-hosted one (`databricks-gte-large-en`, 1024 dims) -- better at telling similar-but-distinct chunks apart | Low (one widget) |
# MAGIC | **Hybrid search** | Combine this vector search with old-fashioned keyword search (e.g. BM25) -- catches exact terms (names, codes, numbers) that meaning-based search alone can miss | Medium |
# MAGIC | **Reranking** | After the fast top-K search, run a second, slower but more accurate model over just those K to re-sort them -- higher precision at the top | Medium |
# MAGIC | **A real vector index at scale** | Once this is more than a few PDFs, driver-side numpy stops being enough -- that's exactly what `pdf_to_vector_index.py`'s Vector Search index (or a local library like FAISS) is for | Medium |
# MAGIC | **Bigger/better chat model** | Swap the local `Qwen2.5-1.5B` for a larger model, or a Databricks-hosted one (`pdf_qa_agent.py`) -- noticeably better reasoning and writing | Low (one widget or notebook) |
# MAGIC | **Query rewriting** | Expand a short/ambiguous question into a fuller one (or generate a few phrasings) before embedding it, to retrieve better when the user's wording doesn't match the document's | Medium |
# MAGIC | **An evaluation set** | A fixed list of real questions with known-correct answers, scored automatically (retrieval recall@k, answer faithfulness) -- turns "feels better" into a number you can track across changes | Medium-High |
# MAGIC | **A feedback loop** | Let users mark an answer as wrong/unhelpful, and use that signal over time to spot systematic weak spots (a chunking issue, a missing document, a bad retrieval) | Higher |
