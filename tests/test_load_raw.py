import duckdb
import pytest

from ingest.load_raw import load_raw

EXPECTED_ROW_COUNTS = {
    "raw_oracle_orders": 128,
    "raw_salesforce_accounts": 20,
    "raw_adaptive_forecast": 287,
    "raw_erp_products": 12,
}
METADATA_COLUMNS = {"_loaded_at", "_source_file", "_batch_id"}


@pytest.fixture(scope="module")
def loaded_db(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("wh") / "test.duckdb")
    counts = load_raw(db_path=db_path, data_dir="data/raw")
    return db_path, counts


def test_all_source_rows_land(loaded_db):
    _, counts = loaded_db
    assert counts == EXPECTED_ROW_COUNTS


def test_duplicate_order_is_preserved_not_deduped(loaded_db):
    """D1: RAW is append-only. Dedup is a staging decision, never an ingest one."""
    db_path, _ = loaded_db
    con = duckdb.connect(db_path, read_only=True)
    occurrences = con.execute(
        "select count(*) from raw.raw_oracle_orders where order_id = 'ORD00005'"
    ).fetchone()[0]
    assert occurrences == 2


def test_every_column_is_varchar(loaded_db):
    """RAW performs no type coercion, so a malformed value can never be lost at load."""
    db_path, _ = loaded_db
    con = duckdb.connect(db_path, read_only=True)
    types = con.execute(
        """
        select data_type from information_schema.columns
        where table_schema = 'raw' and column_name not in ('_loaded_at')
        """
    ).fetchall()
    assert {t[0] for t in types} == {"VARCHAR"}


def test_metadata_columns_present_on_every_table(loaded_db):
    db_path, _ = loaded_db
    con = duckdb.connect(db_path, read_only=True)
    for table in EXPECTED_ROW_COUNTS:
        columns = {
            row[0]
            for row in con.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'raw' and table_name = ?",
                [table],
            ).fetchall()
        }
        assert METADATA_COLUMNS <= columns, f"{table} is missing ingestion metadata"


def test_null_unit_price_survives_as_empty_string(loaded_db):
    """D4: the defect must reach staging to be classified there."""
    db_path, _ = loaded_db
    con = duckdb.connect(db_path, read_only=True)
    price = con.execute(
        "select unit_price from raw.raw_oracle_orders where order_id = 'ORD90106'"
    ).fetchone()[0]
    assert price in ("", None)
