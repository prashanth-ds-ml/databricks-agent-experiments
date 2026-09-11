"""Tests for profiler.py's pure logic: normalizing SQL type names and
building the per-table summary SQL. No Databricks connection involved --
build_summary_sql just returns a string; these tests check that string
has the right shape for each column kind rather than actually running it.
"""

from profiler import NUMERIC_TYPES, TEMPORAL_TYPES, base_type, build_summary_sql


class TestBaseType:
    def test_strips_precision_and_scale(self):
        assert base_type("DECIMAL(12,2)") == "DECIMAL"

    def test_uppercases(self):
        assert base_type("string") == "STRING"

    def test_type_with_no_parens_is_unchanged(self):
        assert base_type("INT") == "INT"


class TestBuildSummarySql:
    def test_always_includes_row_count(self):
        sql = build_summary_sql("workspace", "s", "t", [])
        assert "COUNT(*) AS row_count" in sql

    def test_numeric_column_gets_full_stats(self):
        sql = build_summary_sql("workspace", "s", "t", [("price", "DOUBLE")])

        assert "MIN(`price`)" in sql
        assert "MAX(`price`)" in sql
        assert "AVG(`price`)" in sql
        assert "PERCENTILE_APPROX(`price`, 0.5)" in sql
        assert "STDDEV(`price`)" in sql

    def test_temporal_column_gets_only_min_max(self):
        sql = build_summary_sql("workspace", "s", "t", [("order_date", "DATE")])

        assert "MIN(`order_date`)" in sql
        assert "MAX(`order_date`)" in sql
        assert "AVG(`order_date`)" not in sql

    def test_categorical_column_gets_no_aggregate_stats(self):
        # categorical value counts come from a separate GROUP BY query in
        # profile_table(), not this summary -- only nulls/distinct here
        sql = build_summary_sql("workspace", "s", "t", [("ship_mode", "STRING")])

        assert "COUNT(DISTINCT `ship_mode`)" in sql
        assert "MIN(`ship_mode`)" not in sql

    def test_every_column_gets_null_and_distinct_counts(self):
        sql = build_summary_sql("workspace", "s", "t", [("x", "STRING")])

        assert "COUNT(*) - COUNT(`x`) AS `x__nulls`" in sql
        assert "COUNT(DISTINCT `x`)" in sql

    def test_queries_the_fully_qualified_table(self):
        sql = build_summary_sql("workspace", "candy_distributor", "candy_sales", [])
        assert "FROM workspace.candy_distributor.candy_sales" in sql


def test_numeric_and_temporal_type_sets_do_not_overlap():
    assert NUMERIC_TYPES.isdisjoint(TEMPORAL_TYPES)
