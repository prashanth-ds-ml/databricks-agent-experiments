# Tutorial: Turn a PDF into a Databricks Knowledge Assistant

A beginner-friendly walkthrough of [`pdf_to_vector_index.py`](pdf_to_vector_index.py) --
written so you can hand this to someone who has never used Databricks and
they can follow along. If you want the terser, build-log version instead,
see [`README.md`](README.md) in this folder.

**What you'll end up with:** a searchable index of your PDF's content,
stored in Databricks, that you (or a no-code "Knowledge Assistant") can
ask questions against.

**Want zero ongoing cost instead?** This tutorial walks through the
"billed" pair of notebooks (`pdf_to_vector_index.py` + `pdf_qa_agent.py`),
which uses a Databricks Vector Search endpoint that bills continuously
until you delete it, plus pay-per-token model calls. There's also a
**free** pair -- [`free_pdf_to_embeddings.py`](free_pdf_to_embeddings.py)
and [`free_pdf_qa.py`](free_pdf_qa.py) -- that does the same job with a
local Hugging Face model for embeddings and answers instead, and no
Vector Search endpoint at all. Same widgets-then-Run-All shape as
everything below, just swap which pair of notebooks you open. See the
comparison table in [`README.md`](README.md) for the trade-offs.

## Databricks concepts used here, in plain English

You don't need to know Databricks to follow this -- here's everything
this tutorial touches, in one line each. (Longer version, with more
pieces: [`../docs/COMPONENTS.md`](../docs/COMPONENTS.md).)

- **Unity Catalog** -- the filing system for everything below. Think
  folders-with-permissions: `catalog > schema > table/volume`.
- **Volume** -- a folder inside Unity Catalog for regular files (like your
  PDF), as opposed to structured tables.
- **Delta table** -- a database table. Your PDF's text ends up as rows
  here, one row per chunk of text.
- **Cluster** -- the computer(s) that actually run your code. A notebook
  needs one attached before you can run any cell.
- **Foundation Model API** -- ready-to-use AI models hosted by Databricks
  (chat models, and the embedding models used here) -- no setup, just call
  them by name.
- **Embedding** -- a list of numbers that represents the *meaning* of a
  piece of text, so a computer can compare "how similar" two pieces of
  text are.
- **Vector Search index** -- a special, searchable store of embeddings.
  Ask it a question and it returns the most similar chunks of text.
- **Agent Bricks / Knowledge Assistant** -- a no-code Databricks feature
  that turns a Vector Search index into a question-answering chatbot.

## Before you start

1. **Get a Databricks workspace.** If your organization already has one,
   use that. Otherwise Databricks offers a free trial.
2. **Start a cluster.** In the left sidebar: **Compute > Create compute**.
   - Pick **Databricks Runtime 14.3 LTS** or newer (the plain one, not
     "ML" -- nothing here needs the ML runtime).
   - "Single node" is enough for a handful of PDFs. Cheaper and starts
     faster than a multi-node cluster.
   - No GPU needed for the default settings in this notebook.
3. **Open the notebook.** Import [`pdf_to_vector_index.py`](pdf_to_vector_index.py)
   into your Databricks workspace (**Workspace > Import**), then attach it
   to the cluster you just created (top-left dropdown in the notebook).
4. **Check Vector Search is available.** Most workspaces have it on by
   default. You'll find out for certain at step 9 below -- if it errors
   with a permissions message, that's what to ask your Databricks admin
   about.

## Walking through the notebook, cell by cell

The notebook is split into numbered sections with `%md` (markdown) cells
explaining the technical "why" right above the code. This tutorial gives
the plainer version of the same steps.

### 1. Widgets (the settings box at the top)

Running this cell adds a row of boxes at the top of the notebook --
catalog name, schema name, which PDF folder to use, and so on. You can
change any of these before running the rest, or just use the defaults.

The one worth understanding: **"Embedding source"**, a dropdown with two
choices.
- `databricks` (the default) -- Databricks does the AI work for you.
  Simplest option, nothing to install.
- `huggingface` -- your own cluster downloads a small open-source AI
  model and does the work itself. Good if you specifically want to try an
  open-source model.

If you're not sure, leave it on `databricks`.

### 2. Install dependencies

Every notebook needs certain Python packages installed before it can run.
This cell installs them, with a few retries built in in case the install
hiccups (a known, harmless flakiness with Databricks' package installer).
It ends by restarting the Python process -- expected, not an error; the
next cell picks right back up.

### 3. Configuration

Reads the settings box from step 1 back into the notebook (it has to --
the restart in step 2 wiped the notebook's memory, though not the
settings box itself). Prints out a summary so you can double check
everything looks right before continuing.

### 4. Create the PDF folder, then upload your PDF

This cell creates a `Volume` (a folder, in Unity Catalog terms) for your
PDFs, if it doesn't exist yet.

**Now stop and do this by hand -- this part can't be automated:**
1. Open **Catalog Explorer** (left sidebar, looks like a filing cabinet).
2. Navigate to your catalog > your schema > **Volumes** > the volume name
   printed by the cell (default: `source_docs`).
3. Click **Upload to this volume** and pick your PDF file(s) from your
   computer.

Once uploaded, come back to the notebook and keep running.

### 5. List the PDFs found

A quick sanity check -- it looks in the folder from step 4 and lists what
it finds. If you see an error here, it means the upload in step 4 didn't
land where expected -- go back and check.

### 6. Extract text and split into chunks

Two things happen here:
- **Extract text**: reads the actual words out of your PDF, page by page.
- **Chunk**: breaks that text into smaller pieces (around 1000 characters
  each, by default). AI models work with limited-size pieces of text at a
  time, so the whole PDF gets cut into bite-sized, slightly overlapping
  pieces instead of being fed in all at once.

If your PDF is a scan of a paper document (a photo, basically, not real
text) this step will come up empty -- that needs a different tool (OCR)
this notebook doesn't include.

### 7. Turn the chunks into embeddings (only if you chose `huggingface`)

If you picked the `databricks` option in step 1, this cell does nothing
-- skip ahead. If you picked `huggingface`, this is where your cluster
downloads a small AI model and converts every chunk of text from step 6
into a list of numbers (an embedding) that captures its meaning.

### 8. Save the chunks to a table

All the chunks from step 6 (plus embeddings from step 7, if you're on the
`huggingface` path) get saved as rows in a Delta table -- an ordinary
database table you could also open and browse in Catalog Explorer.

### 9. Create the Vector Search endpoint

A **Vector Search endpoint** is the "server" that will host your search
index. This cell creates one if it doesn't exist yet (can take a few
minutes the first time -- get a coffee) or just confirms it's ready if
you've already run this notebook before.

### 10. Create (or refresh) the search index

This is the main event: it takes the table from step 8 and builds a
**Vector Search index** out of it -- computing an embedding for every
chunk (using whichever option you picked in step 1) and making the whole
thing searchable.

Run the notebook again later (say, after uploading a second PDF) and this
cell notices the index already exists and just refreshes it, instead of
failing or making a duplicate.

### 11. Try it out

A working example: type a question, get back the most relevant chunks of
text from your PDF, with a similarity score. Change the question in the
last cell to something about your own document and re-run it.

## What you just built

A Vector Search index living in Unity Catalog, containing every chunk of
text from your PDF(s) plus their embeddings -- searchable by meaning, not
just by keyword.

## Turning this into a chatbot (no code required)

1. In the Databricks sidebar, go to **Agent Bricks**.
2. Choose **Knowledge Assistant**.
3. Point it at the index the notebook printed out (something like
   `workspace.knowledge_assistant.doc_chunks_index`).
4. Databricks builds the question-answering chatbot on top of it
   automatically -- no further setup from this notebook needed.

## If Agent Bricks doesn't work

Databricks' Knowledge Assistant is a fairly new ("Beta") feature, and it
can fail with an error like:

```
[ERROR] Multi-source search failed: Error code: 404 - {'error_code': 'ENDPOINT_NOT_FOUND', ...
'model_registration': 'instructed-retriever-1', ...}
```

This means an internal Databricks model that Knowledge Assistant depends
on isn't available in your workspace -- it's not something you did wrong,
and it's not fixable from this notebook (there's nothing to configure;
that model isn't something your account can see or control). If you hit
this:

1. Double-check your index is actually fine by querying it directly (see
   the "Try it out" cell in `pdf_to_vector_index.py`, step 11) -- if that
   returns good results, the problem is isolated to Agent Bricks, not
   your data.
2. Use [`pdf_qa_agent.py`](pdf_qa_agent.py) instead -- a small
   ready-to-run notebook in this same folder that answers questions from
   your PDF using a plain Databricks chat model (no Agent Bricks
   involved at all). Run `pdf_to_vector_index.py` first so the index
   exists, then open and run `pdf_qa_agent.py` the same way.
3. Report the error to Databricks support if you want the no-code
   Knowledge Assistant experience specifically -- this is a platform-side
   gap, not something to keep retrying indefinitely.

## If something goes wrong

- **"No PDFs found"** -- the upload in step 4 didn't land in the right
  volume. Re-check the path Catalog Explorer shows you against what the
  notebook printed.
- **Vector Search endpoint creation fails with a permissions error** --
  your workspace either doesn't have Vector Search enabled, or your user
  needs an extra permission. Ask whoever administers your Databricks
  workspace.
- **pip install fails / times out** -- this is a known, occasionally
  flaky Databricks behavior, not something wrong with your setup; the
  notebook retries automatically a few times. If it fails all three
  times, just re-run the cell.
- **You changed the "Embedding source" dropdown after already building an
  index once** -- an index's embedding setup is locked in when it's
  created. Delete the old one first (`vsc.delete_index(...)`, one line,
  see the comment in step 10 of the notebook) and re-run.

## Don't forget to clean up

If you followed this (billed) tutorial, a Vector Search endpoint keeps
costing money until you delete it, even if you're done experimenting. In
a new cell: `vsc.delete_endpoint("<your endpoint name>")`. The free
pipeline (`free_pdf_to_embeddings.py` / `free_pdf_qa.py`) has nothing
equivalent to clean up -- it only leaves behind a Delta table.
