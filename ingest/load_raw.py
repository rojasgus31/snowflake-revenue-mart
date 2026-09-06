"""Land source CSVs into a RAW schema without transforming them.

Mirrors what a managed connector (Fivetran, Airbyte) delivers: every column
arrives as text, nothing is coerced or deduplicated, and each row carries the
metadata needed to trace it back to a load. Type decisions and data quality
rulings belong in staging, where they are visible and testable.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb

SOURCE_TABLES = {
    "oracle_orders.csv": "raw_oracle_orders",
    "salesforce_accounts.csv": "raw_salesforce_accounts",
    "adaptive_forecast.csv": "raw_adaptive_forecast",
    "product_master.csv": "raw_erp_products",
}


def load_raw(
    db_path: str = "warehouse.duckdb",
    data_dir: str = "data/raw",
) -> dict[str, int]:
    """Load every source CSV into the raw schema. Returns row counts by table."""
    batch_id = uuid.uuid4().hex
    loaded_at = datetime.now(timezone.utc)
    connection = duckdb.connect(db_path)
    connection.execute("create schema if not exists raw")

    row_counts: dict[str, int] = {}
    for file_name, table_name in SOURCE_TABLES.items():
        csv_path = Path(data_dir) / file_name
        if not csv_path.exists():
            raise FileNotFoundError(f"Source file not found: {csv_path}")
        row_counts[table_name] = _load_one(
            connection, csv_path, table_name, batch_id, loaded_at
        )

    connection.close()
    return row_counts


def _load_one(
    connection: duckdb.DuckDBPyConnection,
    csv_path: Path,
    table_name: str,
    batch_id: str,
    loaded_at: datetime,
) -> int:
    """Replace one raw table from one CSV, forcing every column to VARCHAR."""
    connection.execute(f"drop table if exists raw.{table_name}")
    connection.execute(
        f"""
        create table raw.{table_name} as
        select
            *,
            ? as _loaded_at,
            ? as _source_file,
            ? as _batch_id
        from read_csv(?, header = true, all_varchar = true)
        """,
        [loaded_at, csv_path.name, batch_id, str(csv_path)],
    )
    return connection.execute(f"select count(*) from raw.{table_name}").fetchone()[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="warehouse.duckdb")
    parser.add_argument("--data-dir", default="data/raw")
    args = parser.parse_args()

    for table_name, count in load_raw(args.db_path, args.data_dir).items():
        print(f"{table_name:<28} {count:>6} rows")


if __name__ == "__main__":
    main()
