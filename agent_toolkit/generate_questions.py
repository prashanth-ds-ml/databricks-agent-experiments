"""Auto-generates a handful of plausible example questions for a dataset,
purely from what the profiler already knows (column kinds, cardinality) --
no LLM call, fully deterministic. Useful for seeding a "Try it out"
section or a Genie/agent starter prompt without hand-writing questions
per dataset.

Usage:
    python generate_questions.py --schema candy_distributor
"""

import argparse
import glob
import json
import os

MAX_QUESTIONS = 6


def load_profiles(schema):
    out_dir = os.path.join(os.path.dirname(__file__), "_profiles_out", schema)
    profiles = {}
    for path in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            profiles[os.path.splitext(os.path.basename(path))[0]] = json.load(f)
    return profiles


def looks_like_id(name: str) -> bool:
    n = name.lower()
    return n == "id" or n.endswith("_id") or n.endswith("id")


def columns_by_kind(profile):
    by_kind = {"numerical": [], "categorical": [], "temporal": []}
    for col, e in profile["columns"].items():
        if e["kind"] == "numerical" and (e.get("is_inferred_primary_key") or looks_like_id(col)):
            continue  # an id column is never a meaningful "total"/"average" metric
        if e["kind"] == "categorical" and e.get("high_cardinality"):
            continue
        if e["kind"] in by_kind:
            by_kind[e["kind"]].append(col)
    return by_kind


def questions_for_table(table, profile):
    qs = []
    cols = columns_by_kind(profile)
    cat, num, tmp = cols["categorical"], cols["numerical"], cols["temporal"]

    if cat and num:
        qs.append(f"What are the top 5 {cat[0]} values in `{table}` by total {num[0]}?")
    if num:
        qs.append(f"What's the range and average {num[0]} in `{table}`?")
    if tmp:
        qs.append(f"How does the row count in `{table}` trend over {tmp[0]}?")
    if cat:
        qs.append(f"How many rows fall into each {cat[0]} category in `{table}`?")
    return qs


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--schema", required=True)
    args = p.parse_args()

    profiles = load_profiles(args.schema)
    if not profiles:
        raise SystemExit(f"No profile JSON found for '{args.schema}'. Run profiler.py first.")

    # Prioritize the biggest table first -- usually the most interesting one.
    ordered = sorted(profiles.items(), key=lambda kv: -kv[1]["row_count"])

    questions = []
    for table, profile in ordered:
        for q in questions_for_table(table, profile):
            if q not in questions:
                questions.append(q)
            if len(questions) >= MAX_QUESTIONS:
                break
        if len(questions) >= MAX_QUESTIONS:
            break

    out_dir = os.path.join(os.path.dirname(__file__), "_profiles_out", args.schema)
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "example_questions.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Example questions for {args.schema}\n\n")
        f.write("Auto-generated from the data profile -- not curated, but a reasonable starting point.\n\n")
        for q in questions:
            f.write(f"- {q}\n")

    print(f"{len(questions)} example questions for {args.schema}:\n")
    for q in questions:
        print(f"  - {q}")
    print(f"\nWrote {md_path}")


if __name__ == "__main__":
    main()
