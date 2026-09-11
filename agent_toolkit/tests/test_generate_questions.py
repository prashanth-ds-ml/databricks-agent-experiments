"""Tests for generate_questions.py -- specifically the two real bugs this
project already hit once and shouldn't hit again:

1. `row_id` (and other id/PK-like numeric columns) getting suggested as a
   "total X" metric, because nothing excluded identifier columns from the
   numerical bucket.
2. `looks_like_id`'s original blanket `endswith("id")` check matching
   ordinary words like "valid", "paid", "grid" -- found while writing
   these tests, not in production, which is the point of having them.
"""

from generate_questions import columns_by_kind, looks_like_id, questions_for_table


class TestLooksLikeId:
    def test_matches_underscore_id_columns(self):
        assert looks_like_id("customer_id")
        assert looks_like_id("row_id")
        assert looks_like_id("product_id")

    def test_matches_bare_id(self):
        assert looks_like_id("id")
        assert looks_like_id("ID")  # case-insensitive

    def test_matches_known_no_underscore_id_names(self):
        assert looks_like_id("rowid")
        assert looks_like_id("uuid")

    def test_does_not_match_ordinary_words_ending_in_id(self):
        # the bug: a blanket endswith("id") matches all of these
        assert not looks_like_id("valid")
        assert not looks_like_id("paid")
        assert not looks_like_id("grid")
        assert not looks_like_id("avoid")
        assert not looks_like_id("solid")

    def test_does_not_match_unrelated_names(self):
        assert not looks_like_id("sales")
        assert not looks_like_id("unit_price")


def _profile(columns):
    """Build a minimal fake profile dict shaped like profiler.py's output."""
    return {"row_count": 100, "column_count": len(columns), "columns": columns}


class TestColumnsByKind:
    def test_excludes_inferred_primary_key_from_numerical(self):
        profile = _profile({
            "row_id": {"kind": "numerical", "is_inferred_primary_key": True},
            "sales": {"kind": "numerical"},
        })

        result = columns_by_kind(profile)

        assert "row_id" not in result["numerical"]
        assert "sales" in result["numerical"]

    def test_excludes_id_like_names_from_numerical_even_if_not_flagged_as_pk(self):
        # customer_id is a real id but isn't necessarily THE inferred PK
        # for a table (row_id might win that slot instead) -- it should
        # still never be treated as a summable metric
        profile = _profile({
            "customer_id": {"kind": "numerical", "is_inferred_primary_key": False},
            "units": {"kind": "numerical"},
        })

        result = columns_by_kind(profile)

        assert "customer_id" not in result["numerical"]
        assert "units" in result["numerical"]

    def test_excludes_high_cardinality_categorical_columns(self):
        profile = _profile({
            "email": {"kind": "categorical", "high_cardinality": True},
            "ship_mode": {"kind": "categorical", "high_cardinality": False},
        })

        result = columns_by_kind(profile)

        assert "email" not in result["categorical"]
        assert "ship_mode" in result["categorical"]

    def test_keeps_temporal_columns(self):
        profile = _profile({"order_date": {"kind": "temporal"}})

        result = columns_by_kind(profile)

        assert result["temporal"] == ["order_date"]


class TestQuestionsForTable:
    def test_generates_a_totals_question_when_categorical_and_numerical_both_exist(self):
        profile = _profile({
            "ship_mode": {"kind": "categorical", "high_cardinality": False},
            "sales": {"kind": "numerical"},
        })

        questions = questions_for_table("candy_sales", profile)

        assert any("top 5 ship_mode values" in q and "total sales" in q for q in questions)

    def test_never_asks_for_a_total_of_an_id_column(self):
        profile = _profile({
            "row_id": {"kind": "numerical", "is_inferred_primary_key": True},
            "ship_mode": {"kind": "categorical", "high_cardinality": False},
        })

        questions = questions_for_table("candy_sales", profile)

        assert not any("row_id" in q for q in questions)

    def test_no_numerical_or_categorical_columns_yields_no_questions(self):
        profile = _profile({"order_date": {"kind": "temporal"}})

        questions = questions_for_table("t", profile)

        assert len(questions) == 1  # only the trend-over-time question
        assert "trend over order_date" in questions[0]
