"""Tests for dashboard_generator.py's pure chart-selection logic and the
overall dashboard JSON shape it builds -- no Databricks calls involved,
these just take a fake profile dict (shaped like profiler.py's real
output) and check what gets picked.
"""

from dashboard_generator import (
    build_dashboard_json,
    pick_categorical_columns,
    pick_temporal_column,
)


def _profile(row_count, columns):
    return {"row_count": row_count, "column_count": len(columns), "columns": columns}


class TestPickCategoricalColumns:
    def test_picks_low_cardinality_categorical_columns(self):
        profile = _profile(100, {
            "division": {"kind": "categorical", "distinct_count": 3, "high_cardinality": False},
        })

        assert pick_categorical_columns(profile, limit=2) == ["division"]

    def test_skips_high_cardinality_columns(self):
        profile = _profile(100, {
            "email": {"kind": "categorical", "distinct_count": 98, "high_cardinality": True},
        })

        assert pick_categorical_columns(profile, limit=2) == []

    def test_skips_the_inferred_primary_key(self):
        profile = _profile(100, {
            "product_id": {
                "kind": "categorical",
                "distinct_count": 100,
                "high_cardinality": False,
                "is_inferred_primary_key": True,
            },
        })

        assert pick_categorical_columns(profile, limit=2) == []

    def test_skips_constant_columns(self):
        # distinct_count 1 (or 0) can't make a meaningful bar chart
        profile = _profile(100, {
            "country": {"kind": "categorical", "distinct_count": 1, "high_cardinality": False},
        })

        assert pick_categorical_columns(profile, limit=2) == []

    def test_respects_the_limit(self):
        profile = _profile(100, {
            "a": {"kind": "categorical", "distinct_count": 3, "high_cardinality": False},
            "b": {"kind": "categorical", "distinct_count": 4, "high_cardinality": False},
            "c": {"kind": "categorical", "distinct_count": 5, "high_cardinality": False},
        })

        assert len(pick_categorical_columns(profile, limit=2)) == 2

    def test_ignores_non_categorical_columns(self):
        profile = _profile(100, {
            "sales": {"kind": "numerical", "distinct_count": 50},
        })

        assert pick_categorical_columns(profile, limit=2) == []


class TestPickTemporalColumn:
    def test_finds_a_temporal_column(self):
        profile = _profile(100, {
            "sales": {"kind": "numerical"},
            "order_date": {"kind": "temporal"},
        })

        assert pick_temporal_column(profile) == "order_date"

    def test_returns_none_when_no_temporal_column_exists(self):
        profile = _profile(100, {"sales": {"kind": "numerical"}})

        assert pick_temporal_column(profile) is None


class TestBuildDashboardJson:
    def test_includes_a_row_count_counter_for_every_table(self):
        profiles = {
            "products": _profile(15, {}),
            "sales": _profile(10194, {}),
        }

        content = build_dashboard_json("workspace", "candy_distributor", profiles)

        widget_titles = [
            w["widget"]["spec"]["frame"]["title"] for w in content["pages"][0]["layout"]
        ]
        assert "products: rows" in widget_titles
        assert "sales: rows" in widget_titles

    def test_adds_a_bar_chart_dataset_for_a_categorical_column(self):
        profiles = {
            "products": _profile(15, {
                "division": {"kind": "categorical", "distinct_count": 3, "high_cardinality": False},
            }),
        }

        content = build_dashboard_json("workspace", "candy_distributor", profiles)

        dataset_names = [d["name"] for d in content["datasets"]]
        assert "ds_products_division_top" in dataset_names

    def test_adds_a_trend_line_dataset_when_a_temporal_column_exists(self):
        profiles = {
            "sales": _profile(10194, {"order_date": {"kind": "temporal"}}),
        }

        content = build_dashboard_json("workspace", "candy_distributor", profiles)

        dataset_names = [d["name"] for d in content["datasets"]]
        assert "ds_sales_order_date_trend" in dataset_names

    def test_caps_at_max_tables(self):
        profiles = {f"t{i}": _profile(1, {}) for i in range(10)}

        content = build_dashboard_json("workspace", "s", profiles)

        # 1 counter widget per table when capped -- fewer widgets than
        # input tables confirms the cap actually applied
        assert len(content["pages"][0]["layout"]) < len(profiles)
