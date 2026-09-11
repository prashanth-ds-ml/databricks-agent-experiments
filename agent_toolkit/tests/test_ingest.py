"""Tests for ingest.py's pure logic: identifier sanitization, file
discovery/skip rules, and the CSV encoding fallback. None of these touch
Databricks -- they're plain functions over strings and local files, which
is exactly why they're worth testing directly rather than only ever
exercising them through a live ingestion run.
"""

from ingest import (
    count_csv_rows,
    discover_files,
    sanitize_identifier,
    table_name_for,
)


class TestSanitizeIdentifier:
    def test_lowercases_and_strips(self):
        assert sanitize_identifier("  Division  ", set()) == "division"

    def test_spaces_become_underscores(self):
        assert sanitize_identifier("Order ID", set()) == "order_id"

    def test_slash_becomes_underscore(self):
        # the real header that motivated this whole function
        assert sanitize_identifier("Country/Region", set()) == "country_region"

    def test_leading_digit_gets_prefixed(self):
        # a bare "2024" isn't a legal SQL identifier
        assert sanitize_identifier("2024", set()) == "c_2024"

    def test_empty_after_cleaning_falls_back_to_col(self):
        assert sanitize_identifier("!!!", set()) == "col"

    def test_collision_gets_a_numeric_suffix(self):
        seen = set()
        first = sanitize_identifier("Total", seen)
        second = sanitize_identifier("Total!", seen)  # also sanitizes to "total"
        third = sanitize_identifier("Total?", seen)
        assert first == "total"
        assert second == "total_2"
        assert third == "total_3"

    def test_already_clean_name_is_unchanged(self):
        assert sanitize_identifier("customer_id", set()) == "customer_id"


class TestTableNameFor:
    def test_strips_extension(self):
        assert table_name_for("Candy_Sales.csv") == "candy_sales"

    def test_sanitizes_the_stem(self):
        assert table_name_for("US Candy Distributor.csv") == "us_candy_distributor"


class TestDiscoverFiles:
    def test_skips_data_dictionary_by_name(self, tmp_path):
        (tmp_path / "products.csv").write_text("a,b\n1,2\n")
        (tmp_path / "candy_distributor_data_dictionary.csv").write_text("a\n1\n")

        kept, skipped = discover_files(str(tmp_path), None, max_mb=20, include_large=False)

        assert kept == ["products.csv"]
        assert len(skipped) == 1
        assert skipped[0][0] == "candy_distributor_data_dictionary.csv"
        assert "dictionary" in skipped[0][1]

    def test_skips_files_over_the_size_threshold(self, tmp_path):
        small = tmp_path / "small.csv"
        small.write_text("a,b\n1,2\n")  # a few bytes
        big = tmp_path / "big.csv"
        big.write_text("a,b\n" + ("1,2\n" * 5000))  # ~20KB, clearly over a 0.01MB cap

        kept, skipped = discover_files(str(tmp_path), None, max_mb=0.01, include_large=False)

        assert kept == ["small.csv"]
        assert any(f == "big.csv" for f, _ in skipped)

    def test_include_large_overrides_the_size_skip(self, tmp_path):
        big = tmp_path / "big.csv"
        big.write_text("a,b\n" + ("1,2\n" * 5000))

        kept, skipped = discover_files(str(tmp_path), None, max_mb=0.01, include_large=True)

        assert kept == ["big.csv"]
        assert skipped == []

    def test_explicit_file_list_bypasses_all_filtering(self, tmp_path):
        # even a file that would normally be skipped by name
        (tmp_path / "data_dictionary.csv").write_text("a\n1\n")

        kept, skipped = discover_files(
            str(tmp_path), ["data_dictionary.csv"], max_mb=20, include_large=False
        )

        assert kept == ["data_dictionary.csv"]
        assert skipped == []

    def test_ignores_non_csv_files(self, tmp_path):
        (tmp_path / "products.csv").write_text("a,b\n1,2\n")
        (tmp_path / "logo.png").write_bytes(b"\x89PNG")

        kept, skipped = discover_files(str(tmp_path), None, max_mb=20, include_large=False)

        assert kept == ["products.csv"]
        assert skipped == []  # non-csv files are silently ignored, not "skipped"


class TestEncodingFallback:
    def test_reads_a_non_utf8_file_without_raising(self, tmp_path):
        path = tmp_path / "latin1.csv"
        path.write_bytes("café,naïve\n1,2\n".encode("cp1252"))

        # this is the real bug found while testing against refer.csv from
        # the Himalayan Expeditions dataset -- a plain utf-8-sig open()
        # raised UnicodeDecodeError on it
        rows = count_csv_rows(str(path))

        assert rows == 1

    def test_counts_rows_correctly_for_a_plain_utf8_file(self, tmp_path):
        path = tmp_path / "plain.csv"
        path.write_text("a,b\n1,2\n3,4\n5,6\n")

        assert count_csv_rows(str(path)) == 3
