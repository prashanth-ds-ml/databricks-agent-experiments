# Databricks notebook source
# MAGIC %md
# MAGIC # PDF -> Embeddings (zero billed services)
# MAGIC - Same first half as [`pdf_to_vector_index.py`](pdf_to_vector_index.py)
# MAGIC   -- extract text from an uploaded PDF, split it into chunks -- but
# MAGIC   stops at a plain **Delta table**, instead of syncing into a
# MAGIC   **Vector Search index**.
# MAGIC - **Why this version exists:** a Vector Search endpoint bills
# MAGIC   continuously for as long as it exists, and Databricks-hosted
# MAGIC   embedding models are pay-per-token. This notebook uses neither --
# MAGIC   embeddings are computed locally on your cluster with a small
# MAGIC   open-source Hugging Face model, and the result is just rows in a
# MAGIC   table. The only cost is the cluster time this notebook runs for,
# MAGIC   same as any other notebook -- nothing is left running/billing
# MAGIC   afterwards.
# MAGIC - Pairs with [`free_pdf_qa.py`](free_pdf_qa.py), which answers
# MAGIC   questions from the table this notebook produces, also with zero
# MAGIC   billed services (a local Hugging Face model instead of a
# MAGIC   Databricks-hosted one).
# MAGIC - Trade-off vs. `pdf_to_vector_index.py`: no governed, incrementally-
# MAGIC   syncing search index, no Agent Bricks Knowledge Assistant
# MAGIC   compatibility, and retrieval only scales to however many rows
# MAGIC   comfortably fit in memory on the driver (fine for a handful of
# MAGIC   PDFs, not for a large document library). See this folder's
# MAGIC   `README.md` for the full comparison.
# MAGIC
# MAGIC ## Before you run this
# MAGIC 1. **Cluster**: Databricks Runtime 14.3 LTS or newer, no ML runtime
# MAGIC    and no GPU required -- the default embedding model
# MAGIC    (`all-MiniLM-L6-v2`) is tiny and runs fine on CPU.
# MAGIC 2. **Run All, every time** -- every cell is safe to re-run (`IF NOT
# MAGIC    EXISTS` / `CREATE OR REPLACE` throughout).
# MAGIC 3. **One manual step:** uploading your PDF(s), same as the Vector
# MAGIC    Search version -- see the cell that creates the PDF volume below.

# COMMAND ----------

# MAGIC %md ## 1. Widgets

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "knowledge_assistant", "Schema")
dbutils.widgets.text("pdf_volume", "source_docs", "PDF volume name")
dbutils.widgets.text("chunks_table", "doc_chunks_free", "Chunks table name")
dbutils.widgets.text("chunk_size", "1000", "Chunk size (characters)")
dbutils.widgets.text("chunk_overlap", "150", "Chunk overlap (characters)")
dbutils.widgets.text("hf_embedding_model", "sentence-transformers/all-MiniLM-L6-v2", "Hugging Face embedding model")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Install dependencies (with retry)
# MAGIC Only `pypdf` (text extraction) and `sentence-transformers` (which
# MAGIC pulls in `torch`) -- no `databricks-vectorsearch` needed here.

# COMMAND ----------

import subprocess
import sys
import time

PIP_ARGS = ["-U", "pypdf", "sentence-transformers"]


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
PDF_VOLUME = dbutils.widgets.get("pdf_volume")
CHUNKS_TABLE = dbutils.widgets.get("chunks_table")
CHUNK_SIZE = int(dbutils.widgets.get("chunk_size"))
CHUNK_OVERLAP = int(dbutils.widgets.get("chunk_overlap"))
HF_EMBEDDING_MODEL = dbutils.widgets.get("hf_embedding_model")

PDF_VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{PDF_VOLUME}"
FULL_CHUNKS_TABLE = f"{CATALOG}.{SCHEMA}.{CHUNKS_TABLE}"

print(f"PDF volume:    {PDF_VOLUME_PATH}")
print(f"Chunks table:  {FULL_CHUNKS_TABLE}")
print(f"Embedding via: {HF_EMBEDDING_MODEL} (local, free)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Create the schema + PDF volume, then upload your PDF(s)
# MAGIC Same manual step as the Vector Search version: after this cell runs,
# MAGIC upload your PDF(s) via Catalog Explorer (`<catalog> > <schema> >
# MAGIC Volumes > <pdf_volume> > Upload`), then continue.

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{PDF_VOLUME}")
print(f"Upload PDF(s) to: {PDF_VOLUME_PATH}")

# COMMAND ----------

# MAGIC %md ## 5. List the PDFs found

# COMMAND ----------

pdf_files = sorted(f.name for f in dbutils.fs.ls(PDF_VOLUME_PATH) if f.name.lower().endswith(".pdf"))
if not pdf_files:
    raise RuntimeError(f"No PDFs found in {PDF_VOLUME_PATH}. Upload at least one, then re-run from here.")
print(f"Found {len(pdf_files)} PDF(s): {', '.join(pdf_files)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Extract text and split into chunks
# MAGIC Identical logic to `pdf_to_vector_index.py` -- see that notebook's
# MAGIC step 6 for the full explanation of `chunk_text`/`chunk_id_for`.

# COMMAND ----------

import hashlib
import re

from pypdf import PdfReader


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


def chunk_id_for(filename: str, page_number: int, chunk_index: int) -> str:
    raw = f"{filename}:{page_number}:{chunk_index}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


rows = []
for filename in pdf_files:
    reader = PdfReader(f"{PDF_VOLUME_PATH}/{filename}")
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        for chunk_index, chunk in enumerate(chunk_text(page_text, CHUNK_SIZE, CHUNK_OVERLAP)):
            rows.append({
                "chunk_id": chunk_id_for(filename, page_number, chunk_index),
                "source_file": filename,
                "page_number": page_number,
                "chunk_index": chunk_index,
                "content": chunk,
            })

if not rows:
    raise RuntimeError("Extracted 0 text chunks -- if the PDF is scanned images, this needs OCR first.")
print(f"{len(rows)} chunks from {len(pdf_files)} PDF(s)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Embed every chunk locally
# MAGIC Always runs (unlike the Vector Search notebook, where this was
# MAGIC conditional) -- there's no Databricks-managed embedding option here
# MAGIC by design, only the free local one.

# COMMAND ----------

from sentence_transformers import SentenceTransformer

hf_model = SentenceTransformer(HF_EMBEDDING_MODEL)
vectors = hf_model.encode([r["content"] for r in rows], show_progress_bar=True).tolist()
for r, vector in zip(rows, vectors):
    r["embedding"] = vector
EMBEDDING_DIM = len(vectors[0])
print(f"Embedded {len(rows)} chunks with {HF_EMBEDDING_MODEL} ({EMBEDDING_DIM} dims)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Write the chunks + embeddings to a Delta table
# MAGIC `CREATE OR REPLACE` -- safe to re-run. No `enableChangeDataFeed`
# MAGIC needed here (that was only required for Vector Search's Delta Sync
# MAGIC Index) -- this table is read fresh by `free_pdf_qa.py` every time,
# MAGIC not incrementally synced anywhere.

# COMMAND ----------

from pyspark.sql.types import ArrayType, FloatType, IntegerType, StringType, StructField, StructType

schema = StructType([
    StructField("chunk_id", StringType(), False),
    StructField("source_file", StringType(), False),
    StructField("page_number", IntegerType(), False),
    StructField("chunk_index", IntegerType(), False),
    StructField("content", StringType(), False),
    StructField("embedding", ArrayType(FloatType()), False),
])

chunks_df = spark.createDataFrame(rows, schema=schema)
chunks_df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(FULL_CHUNKS_TABLE)

print(f"Wrote {chunks_df.count()} rows to {FULL_CHUNKS_TABLE}")
display(chunks_df.drop("embedding").limit(5))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done -- nothing left running
# MAGIC Unlike the Vector Search version, this notebook created **no
# MAGIC endpoint and no index** -- just a Delta table (storage cost only,
# MAGIC not compute). There is nothing to remember to delete afterward.
# MAGIC
# MAGIC ## Next steps
# MAGIC - Open [`free_pdf_qa.py`](free_pdf_qa.py) and point its `chunks_table`
# MAGIC   widget at `doc_chunks_free` (the default) to ask questions --
# MAGIC   make sure its `hf_embedding_model` widget matches
# MAGIC   `HF_EMBEDDING_MODEL` above, since queries and stored chunks must
# MAGIC   be embedded with the same model to be comparable.
# MAGIC - Uploaded more PDFs later? Just re-run this notebook top to bottom --
# MAGIC   it picks up every PDF currently in the volume.
