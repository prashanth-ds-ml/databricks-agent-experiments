# Databricks notebook source
# MAGIC %md
# MAGIC # PDF -> Vector Search Index
# MAGIC - Takes PDF(s) sitting in a Unity Catalog volume, splits them into
# MAGIC   overlapping text chunks, embeds those chunks, and syncs the result
# MAGIC   into a **Databricks Vector Search** index -- a governed, queryable
# MAGIC   UC object.
# MAGIC - That index is the piece a Databricks **Knowledge Assistant** (Agent
# MAGIC   Bricks) or a custom RAG tool points at -- this notebook builds the
# MAGIC   index, it doesn't build the assistant itself (see "Next steps" at
# MAGIC   the bottom).
# MAGIC - Companion to [`sales/databricks_agent/`](../sales/databricks_agent/)
# MAGIC   and [`agent_toolkit/`](../agent_toolkit/): those put an agent in
# MAGIC   front of *structured* tables; this puts one in front of
# MAGIC   *unstructured* documents.
# MAGIC
# MAGIC ## Before you run this (smooth-start checklist)
# MAGIC 1. **Cluster**: attach this notebook to an all-purpose or job cluster
# MAGIC    on **Databricks Runtime 14.3 LTS or newer** (the plain "Runtime",
# MAGIC    not the ML runtime -- nothing here needs it). Single-node/small is
# MAGIC    plenty for a handful of PDFs. A GPU isn't required for the default
# MAGIC    Hugging Face model (`all-MiniLM-L6-v2`, tiny), only useful if you
# MAGIC    swap in a bigger one.
# MAGIC 2. **Vector Search must be enabled** for this workspace (Unity
# MAGIC    Catalog, Standard tier or above) and your user needs permission to
# MAGIC    create a Vector Search endpoint and Unity Catalog objects
# MAGIC    (schemas/volumes/tables) in the target catalog. If the endpoint
# MAGIC    cell below fails with a permissions error, that's the first thing
# MAGIC    to check.
# MAGIC 3. **Run All, every time.** Every cell below is written to be
# MAGIC    re-run safely: schema/volume/table creation use `IF NOT EXISTS`
# MAGIC    or `CREATE OR REPLACE`, and the index cell detects an existing
# MAGIC    index and syncs it instead of failing. The one thing that can't be
# MAGIC    scripted is step 4 below -- uploading your own PDF(s) -- do that
# MAGIC    once, then Run All picks them up.
# MAGIC 4. **One manual step:** after the volume is created (a couple of
# MAGIC    cells in), pause and upload your PDF(s) into it via Catalog
# MAGIC    Explorer before continuing -- exact instructions are in that cell.
# MAGIC
# MAGIC ## Two embedding paths, pick with the `embedding_source` widget
# MAGIC | | `databricks` (default) | `huggingface` |
# MAGIC |---|---|---|
# MAGIC | What computes the embeddings | A Databricks-hosted Foundation Model endpoint (`databricks-gte-large-en`) | A local `sentence-transformers` model, run on this cluster |
# MAGIC | Setup cost | None -- it's already deployed in the workspace | Downloads a model to the cluster the first time |
# MAGIC | Index type built | Delta Sync Index, **Databricks-computed embeddings** | Delta Sync Index, **self-managed embeddings** |
# MAGIC | Querying with plain text later | Yes, directly | No -- queries must be embedded with the same HF model first (handled by the `search()` helper below) |
# MAGIC | Good for | The smooth-start default; no extra dependency, no model download | Comparing against an open-weight embedding model, or working offline from any Databricks-hosted endpoint |

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Widgets
# MAGIC Declared **before** the install cell on purpose -- the install cell
# MAGIC needs to know `embedding_source` to decide whether to pull in
# MAGIC `sentence-transformers`, which the sibling agent notebooks' install
# MAGIC cells don't need to do (their config always comes after install).

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "knowledge_assistant", "Schema")
dbutils.widgets.text("pdf_volume", "source_docs", "PDF volume name")
dbutils.widgets.text("chunks_table", "doc_chunks", "Chunks table name")
dbutils.widgets.text("chunk_size", "1000", "Chunk size (characters)")
dbutils.widgets.text("chunk_overlap", "150", "Chunk overlap (characters)")
dbutils.widgets.dropdown("embedding_source", "databricks", ["databricks", "huggingface"], "Embedding source")
dbutils.widgets.text("databricks_embedding_endpoint", "databricks-gte-large-en", "Databricks embedding endpoint")
dbutils.widgets.text("hf_embedding_model", "sentence-transformers/all-MiniLM-L6-v2", "Hugging Face embedding model")
dbutils.widgets.text("vs_endpoint", "knowledge_assistant_vs", "Vector Search endpoint name")
dbutils.widgets.text("vs_index_name", "doc_chunks_index", "Vector Search index name")

print(f"embedding_source = {dbutils.widgets.get('embedding_source')}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Install dependencies (with retry)
# MAGIC - `databricks-vectorsearch` is the SDK for creating/querying the
# MAGIC   index; `pypdf` is a pure-Python PDF text extractor -- both light,
# MAGIC   no known resolver issues like the LangGraph agent notebooks have.
# MAGIC - `sentence-transformers` (which pulls in `torch`) is only installed
# MAGIC   when the `embedding_source` widget is set to `huggingface`, so the
# MAGIC   default `databricks` path stays fast to install.
# MAGIC - Same retry-loop pattern as the other notebooks in this repo, kept
# MAGIC   here as a safety net even though these packages haven't shown the
# MAGIC   `ResolutionTooDeep` issue that motivated it originally.

# COMMAND ----------

import subprocess
import sys
import time

PIP_ARGS = ["-U", "databricks-vectorsearch", "pypdf"]
if dbutils.widgets.get("embedding_source") == "huggingface":
    PIP_ARGS.append("sentence-transformers")


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
# MAGIC Re-reads every widget into a plain constant -- needed because
# MAGIC `restartPython()` above wiped the Python namespace (widget *values*
# MAGIC survive a restart, Python variables don't).

# COMMAND ----------

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
PDF_VOLUME = dbutils.widgets.get("pdf_volume")
CHUNKS_TABLE = dbutils.widgets.get("chunks_table")
CHUNK_SIZE = int(dbutils.widgets.get("chunk_size"))
CHUNK_OVERLAP = int(dbutils.widgets.get("chunk_overlap"))
EMBEDDING_SOURCE = dbutils.widgets.get("embedding_source")
DATABRICKS_EMBEDDING_ENDPOINT = dbutils.widgets.get("databricks_embedding_endpoint")
HF_EMBEDDING_MODEL = dbutils.widgets.get("hf_embedding_model")
VS_ENDPOINT = dbutils.widgets.get("vs_endpoint")

PDF_VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{PDF_VOLUME}"
FULL_CHUNKS_TABLE = f"{CATALOG}.{SCHEMA}.{CHUNKS_TABLE}"
FULL_INDEX_NAME = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('vs_index_name')}"

print(f"Dataset:        {CATALOG}.{SCHEMA}")
print(f"PDF volume:     {PDF_VOLUME_PATH}")
print(f"Chunks table:   {FULL_CHUNKS_TABLE}")
print(f"Embedding via:  {EMBEDDING_SOURCE}")
print(f"VS endpoint:    {VS_ENDPOINT}")
print(f"VS index:       {FULL_INDEX_NAME}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Create the schema + PDF volume, then upload your PDF(s)
# MAGIC `IF NOT EXISTS` everywhere -- safe to re-run. After this cell runs
# MAGIC once, **pause here**: open **Catalog Explorer** in the Databricks UI,
# MAGIC navigate to `<catalog> > <schema> > Volumes > <pdf_volume>`, and use
# MAGIC **Upload to this volume** to drop in one or more PDF files. Then come
# MAGIC back and continue running the notebook -- every cell after this one
# MAGIC picks up whatever PDFs it finds there.

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{PDF_VOLUME}")
print(f"Upload PDF(s) to: {PDF_VOLUME_PATH}  (Catalog Explorer -> {CATALOG} > {SCHEMA} > Volumes > {PDF_VOLUME} > Upload)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. List the PDFs found
# MAGIC A clear stop here beats a confusing failure three cells later if the
# MAGIC upload step above got skipped.

# COMMAND ----------

pdf_files = sorted(f.name for f in dbutils.fs.ls(PDF_VOLUME_PATH) if f.name.lower().endswith(".pdf"))
if not pdf_files:
    raise RuntimeError(
        f"No PDFs found in {PDF_VOLUME_PATH}. Upload at least one via Catalog "
        "Explorer (see the cell above), then re-run from here."
    )
print(f"Found {len(pdf_files)} PDF(s): {', '.join(pdf_files)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Extract text and split into chunks
# MAGIC - `pypdf` reads each page's text directly off the UC volume path --
# MAGIC   Volumes are FUSE-mounted at `/Volumes/...`, so no download/copy step
# MAGIC   is needed.
# MAGIC - `chunk_text` is a plain character-window splitter: cuts at the
# MAGIC   nearest sentence end (or word boundary) before `chunk_size`, then
# MAGIC   backs up by `chunk_overlap` characters so consecutive chunks share
# MAGIC   context -- a real embedding library (e.g. LangChain's text
# MAGIC   splitters) does the same idea with more options; this stays plain
# MAGIC   Python since the repo's philosophy is minimal dependencies for
# MAGIC   logic this simple.
# MAGIC - `chunk_id` is a stable hash of (file, page, chunk index), so
# MAGIC   re-running on the same PDFs produces the same primary keys instead
# MAGIC   of duplicating rows.

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
    raise RuntimeError(
        "Extracted 0 text chunks from the PDF(s) found. If a PDF is scanned "
        "images rather than real text, pypdf can't read it -- it would need "
        "OCR first, which this notebook doesn't do."
    )
print(f"{len(rows)} chunks from {len(pdf_files)} PDF(s)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Embed the chunks (Hugging Face path only)
# MAGIC - Skipped entirely when `embedding_source = databricks` -- in that
# MAGIC   case Vector Search calls the Databricks embedding endpoint itself
# MAGIC   during the sync in step 9, so nothing needs computing here.
# MAGIC - When `embedding_source = huggingface`, embeddings are computed here
# MAGIC   on the driver with `sentence-transformers` and added as a column.
# MAGIC   Driver-side is fine for a handful of PDFs; a large corpus would
# MAGIC   want a pandas UDF to spread the work across the cluster instead --
# MAGIC   left out here to keep this notebook a straight-through walkthrough.

# COMMAND ----------

EMBEDDING_DIM = None
if EMBEDDING_SOURCE == "huggingface":
    from sentence_transformers import SentenceTransformer

    hf_model = SentenceTransformer(HF_EMBEDDING_MODEL)
    vectors = hf_model.encode([r["content"] for r in rows], show_progress_bar=True).tolist()
    for r, vector in zip(rows, vectors):
        r["embedding"] = vector
    EMBEDDING_DIM = len(vectors[0])
    print(f"Embedded {len(rows)} chunks with {HF_EMBEDDING_MODEL} ({EMBEDDING_DIM} dims)")
else:
    print("embedding_source = databricks -- embeddings computed during the Vector Search sync, not here")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Write the chunks to a Delta table
# MAGIC `CREATE OR REPLACE` -- safe to re-run after uploading more PDFs or
# MAGIC changing chunk size. `delta.enableChangeDataFeed` is **required**:
# MAGIC Vector Search's Delta Sync Index reads the table's change feed to
# MAGIC know what changed since the last sync, and creation fails without it.

# COMMAND ----------

from pyspark.sql.types import ArrayType, FloatType, IntegerType, StringType, StructField, StructType

fields = [
    StructField("chunk_id", StringType(), False),
    StructField("source_file", StringType(), False),
    StructField("page_number", IntegerType(), False),
    StructField("chunk_index", IntegerType(), False),
    StructField("content", StringType(), False),
]
if EMBEDDING_SOURCE == "huggingface":
    fields.append(StructField("embedding", ArrayType(FloatType()), False))

chunks_df = spark.createDataFrame(rows, schema=StructType(fields))
chunks_df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(FULL_CHUNKS_TABLE)
spark.sql(f"ALTER TABLE {FULL_CHUNKS_TABLE} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")

print(f"Wrote {chunks_df.count()} rows to {FULL_CHUNKS_TABLE}")
display(chunks_df.limit(5))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Create (or reuse) the Vector Search endpoint
# MAGIC `endpoint_exists` / `create_endpoint_and_wait` make this idempotent --
# MAGIC first run creates it (can take several minutes to come online), every
# MAGIC run after that just confirms it's ready and moves on.

# COMMAND ----------

from databricks.vector_search.client import VectorSearchClient

vsc = VectorSearchClient()

if not vsc.endpoint_exists(VS_ENDPOINT):
    print(f"Creating Vector Search endpoint {VS_ENDPOINT} (this can take several minutes)...")
    vsc.create_endpoint_and_wait(name=VS_ENDPOINT, endpoint_type="STANDARD", verbose=True)
else:
    print(f"Endpoint {VS_ENDPOINT} already exists -- confirming it's ready...")
    vsc.wait_for_endpoint(VS_ENDPOINT)
print(f"Endpoint {VS_ENDPOINT} is online")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. Create (or sync) the Delta Sync Index
# MAGIC - First run creates the index against `FULL_CHUNKS_TABLE`; every run
# MAGIC   after that calls `.sync()` instead, which picks up any rows added
# MAGIC   or changed since the last sync via the change feed from step 8.
# MAGIC - `pipeline_type="TRIGGERED"` means it only re-embeds/re-syncs when
# MAGIC   you call `.sync()` (i.e. when you re-run this notebook) rather than
# MAGIC   continuously watching the table -- cheaper for a notebook you run
# MAGIC   by hand, at the cost of not picking up table changes automatically.
# MAGIC - **An index's embedding config is fixed at creation.** If you switch
# MAGIC   the `embedding_source` widget after the index already exists,
# MAGIC   delete it first: `vsc.delete_index(VS_ENDPOINT, FULL_INDEX_NAME)`,
# MAGIC   then re-run this cell to recreate it with the new config.

# COMMAND ----------

if vsc.index_exists(VS_ENDPOINT, FULL_INDEX_NAME):
    print(f"Index {FULL_INDEX_NAME} already exists -- syncing...")
    index = vsc.get_index(VS_ENDPOINT, FULL_INDEX_NAME)
    index.sync()
else:
    print(f"Creating index {FULL_INDEX_NAME} ({EMBEDDING_SOURCE} embeddings)...")
    if EMBEDDING_SOURCE == "databricks":
        index = vsc.create_delta_sync_index_and_wait(
            endpoint_name=VS_ENDPOINT,
            index_name=FULL_INDEX_NAME,
            source_table_name=FULL_CHUNKS_TABLE,
            pipeline_type="TRIGGERED",
            primary_key="chunk_id",
            embedding_source_column="content",
            embedding_model_endpoint_name=DATABRICKS_EMBEDDING_ENDPOINT,
            verbose=True,
        )
    else:
        index = vsc.create_delta_sync_index_and_wait(
            endpoint_name=VS_ENDPOINT,
            index_name=FULL_INDEX_NAME,
            source_table_name=FULL_CHUNKS_TABLE,
            pipeline_type="TRIGGERED",
            primary_key="chunk_id",
            embedding_vector_column="embedding",
            embedding_dimension=EMBEDDING_DIM,
            verbose=True,
        )
print(f"Index {FULL_INDEX_NAME} ready, saved in Unity Catalog under {CATALOG}.{SCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11. Try it out
# MAGIC - `databricks`-sourced indexes accept a plain text query -- Vector
# MAGIC   Search embeds it with the same endpoint used to build the index.
# MAGIC - `huggingface`-sourced (self-managed) indexes don't know which model
# MAGIC   built them, so the query has to be embedded locally first, with the
# MAGIC   same `hf_model` from step 7, and passed as `query_vector`.

# COMMAND ----------


def search(query: str, k: int = 4):
    if EMBEDDING_SOURCE == "databricks":
        results = index.similarity_search(
            query_text=query,
            columns=["source_file", "page_number", "content"],
            num_results=k,
        )
    else:
        query_vector = hf_model.encode([query]).tolist()[0]
        results = index.similarity_search(
            query_vector=query_vector,
            columns=["source_file", "page_number", "content"],
            num_results=k,
        )
    return results["result"]["data_array"]


# COMMAND ----------

# MAGIC %md Replace this with a real question about your uploaded PDF(s).

# COMMAND ----------

for source_file, page_number, content, score in search("What is this document about?"):
    print(f"[{source_file} p.{int(page_number)}, score={score:.3f}] {content[:200]}...")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next steps
# MAGIC - **Agent Bricks -> Knowledge Assistant** (no-code path): in the
# MAGIC   Databricks UI, Agent Bricks -> Knowledge Assistant -> point it at
# MAGIC   `FULL_INDEX_NAME` (printed above) as a knowledge source. It builds
# MAGIC   the retrieval + answer-generation pipeline on top of this index
# MAGIC   automatically.
# MAGIC - **Custom tool** (matches the pattern in `sales/databricks_agent/`):
# MAGIC   wrap `index.similarity_search(...)` in a `@tool`-decorated Python
# MAGIC   function and add it to a LangGraph agent's tool list alongside (or
# MAGIC   instead of) the UC SQL function tools -- same ReAct loop, now able
# MAGIC   to answer questions from the PDF as well as the structured tables.
# MAGIC - **More PDFs later:** upload them to the same volume and re-run this
# MAGIC   notebook top to bottom -- steps 6-10 pick up every PDF currently in
# MAGIC   the volume, not just new ones, so nothing needs tracking by hand.
# MAGIC - **Cost note:** the Vector Search endpoint created in step 9 keeps
# MAGIC   running (and billing) until deleted -- `vsc.delete_endpoint(VS_ENDPOINT)`
# MAGIC   when you're done experimenting, if this was just a trial.
