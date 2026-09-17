# Databricks notebook source
# MAGIC %md
# MAGIC # Embeddings, Explained With Simple Sentences
# MAGIC A 5-minute primer -- run this **before** [`rag_concepts_demo.py`](rag_concepts_demo.py)
# MAGIC when presenting to someone new to embeddings. Uses a dozen plain,
# MAGIC obviously-different example sentences instead of PDF text, so the
# MAGIC core idea is unmistakable before you show it applied to a real
# MAGIC document. Same model, same technique, same plot style as the real
# MAGIC demo -- once someone's seen the pattern here, the PDF version reads
# MAGIC as "oh, it's the same thing" instead of a new idea to absorb.
# MAGIC
# MAGIC Zero billed services -- one small local model, two open-source
# MAGIC plotting/math libraries.

# COMMAND ----------

# MAGIC %md ## 1. Widgets

# COMMAND ----------

dbutils.widgets.text("hf_embedding_model", "sentence-transformers/all-MiniLM-L6-v2", "Embedding model (matches every other notebook in this folder)")
dbutils.widgets.text("your_own_sentence", "What did the stock market do today?", "Try your own sentence -- shown in Part 4")

# COMMAND ----------

# MAGIC %md ## 2. Install dependencies (with retry)

# COMMAND ----------

import subprocess
import sys
import time

PIP_ARGS = ["-U", "sentence-transformers", "scikit-learn", "plotly"]


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

HF_EMBEDDING_MODEL = dbutils.widgets.get("hf_embedding_model")
YOUR_SENTENCE = dbutils.widgets.get("your_own_sentence")
print(f"Model: {HF_EMBEDDING_MODEL}")

# COMMAND ----------

# MAGIC %md
# MAGIC # Part 1: An embedding is just... a list of numbers
# MAGIC That's the whole concept. A sentence goes in, a fixed-length list of
# MAGIC numbers comes out -- chosen so that sentences with similar meaning
# MAGIC end up with similar numbers. Nothing more mysterious than that.

# COMMAND ----------

import numpy as np
from sentence_transformers import SentenceTransformer

embed_model = SentenceTransformer(HF_EMBEDDING_MODEL)

example_sentence = "The dog is playing in the park."
vector = embed_model.encode(example_sentence)

print(f"Sentence: {example_sentence!r}")
print(f"Turned into {len(vector)} numbers. First 10 of them:")
print(np.round(vector[:10], 3))

# COMMAND ----------

# MAGIC %md
# MAGIC # Part 2: Similar meaning -> similar numbers
# MAGIC Twelve short sentences, four topics, three sentences per topic --
# MAGIC each one worded completely differently from the others. If
# MAGIC embeddings really capture meaning, sentences about the same topic
# MAGIC should score highly similar to each other, and low to every other
# MAGIC topic, **despite sharing almost no words.**

# COMMAND ----------

sentences = [
    ("The dog is playing in the park.", "animals"),
    ("A puppy is running around outside.", "animals"),
    ("My cat sleeps all day on the couch.", "animals"),
    ("I love eating pizza with extra cheese.", "food"),
    ("My favorite meal is a cheesy pasta dish.", "food"),
    ("This restaurant serves amazing burgers.", "food"),
    ("The stock market fell sharply today.", "finance"),
    ("Share prices dropped significantly this morning.", "finance"),
    ("Investors are worried about rising interest rates.", "finance"),
    ("The football team won the championship game.", "sports"),
    ("She scored the winning goal in the final minute.", "sports"),
    ("The basketball match went into overtime.", "sports"),
]
texts = [s for s, _ in sentences]
topics = [t for _, t in sentences]

vectors = embed_model.encode(texts)
unit_vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
similarity_matrix = unit_vectors @ unit_vectors.T

# COMMAND ----------

import plotly.express as px

short_labels = [f"{t[:28]}..." if len(t) > 28 else t for t in texts]
fig = px.imshow(
    similarity_matrix,
    x=short_labels, y=short_labels,
    color_continuous_scale="Viridis", zmin=0, zmax=1,
    title="Similarity between every pair of sentences (1.0 = identical meaning, 0 = unrelated)",
)
fig.update_layout(height=700)
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC **What to point out:** four bright 3x3 squares along the diagonal --
# MAGIC each one is a topic, scoring high against itself. Everything off
# MAGIC those squares is noticeably darker. Look at "A puppy is running
# MAGIC around outside" vs. "The dog is playing in the park" -- almost no
# MAGIC words in common, still scores high. That's the difference between
# MAGIC matching *words* and matching *meaning*.

# COMMAND ----------

# MAGIC %md
# MAGIC # Part 3: The same thing, as a map
# MAGIC Each sentence's numbers get compressed down to 2 dimensions (PCA) so
# MAGIC they can be plotted as points -- colored here by their *actual*
# MAGIC topic (we know it, since we wrote these sentences), so you can see
# MAGIC directly whether the embedding model grouped them the way a person
# MAGIC would.

# COMMAND ----------

import plotly.graph_objects as go
from sklearn.decomposition import PCA

pca = PCA(n_components=2, random_state=0)
coords_2d = pca.fit_transform(vectors)

fig = go.Figure()
for topic in sorted(set(topics)):
    idx = [i for i, t in enumerate(topics) if t == topic]
    fig.add_trace(go.Scatter(
        x=coords_2d[idx, 0], y=coords_2d[idx, 1],
        mode="markers",
        marker=dict(size=12),
        text=[texts[i] for i in idx],
        hoverinfo="text",
        name=topic,
    ))

fig.update_layout(
    title="Twelve sentences, four topics -- grouped by meaning, not by any label we gave the model",
    height=600, legend_title_text="Topic",
)
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC # Part 4: Using it to answer a question
# MAGIC This is the exact move retrieval makes: embed a question the same
# MAGIC way, then find whichever sentences land closest to it. Try changing
# MAGIC the `your_own_sentence` widget above and re-running this cell.

# COMMAND ----------

query_vector = embed_model.encode(YOUR_SENTENCE)
query_unit = query_vector / np.linalg.norm(query_vector)
scores = unit_vectors @ query_unit
ranked = np.argsort(-scores)

print(f"Query: {YOUR_SENTENCE!r}\n")
print("Most similar sentences, best match first:")
for i in ranked[:3]:
    print(f"  {scores[i]:.3f}  [{topics[i]}]  {texts[i]}")

# COMMAND ----------

query_2d = pca.transform(query_vector.reshape(1, -1))[0]

fig = go.Figure()
for topic in sorted(set(topics)):
    idx = [i for i, t in enumerate(topics) if t == topic]
    fig.add_trace(go.Scatter(
        x=coords_2d[idx, 0], y=coords_2d[idx, 1],
        mode="markers", marker=dict(size=12),
        text=[texts[i] for i in idx], hoverinfo="text", name=topic,
    ))

for i in ranked[:3]:
    fig.add_trace(go.Scatter(
        x=[query_2d[0], coords_2d[i, 0]], y=[query_2d[1], coords_2d[i, 1]],
        mode="lines", line=dict(color="crimson", width=1, dash="dot"),
        hoverinfo="skip", showlegend=False,
    ))

fig.add_trace(go.Scatter(
    x=[query_2d[0]], y=[query_2d[1]],
    mode="markers+text", marker=dict(size=20, color="crimson", symbol="star", line=dict(width=1, color="white")),
    text=["Your question"], textposition="top center", hoverinfo="text", hovertext=[YOUR_SENTENCE],
    name="Query",
))
fig.update_layout(title="Your question, plotted against the same twelve sentences", height=600, legend_title_text="Topic")
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC **This is the exact plot you'll see again in `rag_concepts_demo.py`
# MAGIC -- just with real PDF chunks standing in for these twelve sentences,
# MAGIC and no ready-made topic labels (there, similar-meaning groups get
# MAGIC *discovered* automatically instead of being told to you upfront).**

# COMMAND ----------

# MAGIC %md
# MAGIC # Part 5 (bonus): Meaning, not word overlap
# MAGIC One more pair to drive the point home -- two sentences that share
# MAGIC real words but mean different things, versus two that share almost
# MAGIC no words but mean the same thing.

# COMMAND ----------

pairs = [
    ("How much does this cost?", "What is the price of this item?"),
    ("How much does this cost?", "This product costs a lot of money to manufacture."),
]
for a, b in pairs:
    va = embed_model.encode(a)
    vb = embed_model.encode(b)
    sim = (va @ vb) / (np.linalg.norm(va) * np.linalg.norm(vb))
    print(f"{sim:.3f}  {a!r}  <->  {b!r}")

# COMMAND ----------

# MAGIC %md
# MAGIC The first pair shares zero words and scores high -- same question,
# MAGIC different phrasing. The second pair shares the word "cost" and still
# MAGIC scores lower -- one is asking a price, the other is talking about
# MAGIC manufacturing expense. Word overlap alone would get this backwards.
# MAGIC
# MAGIC ## Next: open `rag_concepts_demo.py`
# MAGIC Same techniques, same kind of plot -- now applied to a real PDF.
