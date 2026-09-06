"""Land source CSVs into a RAW schema without transforming them.

Mirrors what a managed connector (Fivetran, Airbyte) delivers: every column
arrives as text, nothing is coerced or deduplicated, and each row carries the
metadata needed to trace it back to a load. Type decisions and data quality
rulings belong in staging, where they are visible and testable.

Two targets share this file:

- ``duckdb`` (the default): loads into a local warehouse.duckdb file. This is
  the path this project was built and validated against, and needs no
  account.
- ``snowflake``: loads into the RAW schema of a real Snowflake account, using
  the same credential environment variables transform/profiles.yml reads.
  This path is implemented but has not been executed against a live account
  in this project (see snowflake/README.md for why).

Both targets share the batch bookkeeping (a fresh ``_batch_id``/``_loaded_at``
per run) and the source-file-to-table mapping below, so the RAW contract --
four tables, every business column VARCHAR, three metadata columns -- stays
identical regardless of where it lands.
"""

from __future__ import annotations

import argparse
import os
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

# The same environment variables transform/profiles.yml reads for the
# `snowflake` dbt target. Keeping the names identical means one `export` block
# (see snowflake/README.md) configures both the loader and the transform step.
SNOWFLAKE_ENV_VARS = {
    "account": "SNOWFLAKE_ACCOUNT",
    "user": "SNOWFLAKE_USER",
    "password": "SNOWFLAKE_PASSWORD",
    "role": "SNOWFLAKE_ROLE",
    "warehouse": "SNOWFLAKE_WAREHOUSE",
    "database": "SNOWFLAKE_DATABASE",
}


def _new_batch() -> tuple[str, datetime]:
    """Generate the batch id and load timestamp shared by every table in a run."""
    return uuid.uuid4().hex, datetime.now(timezone.utc)


def _resolve_csv_path(data_dir: str, file_name: str) -> Path:
    """Look up one source CSV, failing loudly if the extract is missing."""
    csv_path = Path(data_dir) / file_name
    if not csv_path.exists():
        raise FileNotFoundError(f"Source file not found: {csv_path}")
    return csv_path


def load_raw(
    db_path: str = "warehouse.duckdb",
    data_dir: str = "data/raw",
) -> dict[str, int]:
    """Load every source CSV into DuckDB's raw schema. Returns row counts by table."""
    batch_id, loaded_at = _new_batch()

    row_counts: dict[str, int] = {}
    with duckdb.connect(db_path) as connection:
        connection.execute("create schema if not exists raw")

        for file_name, table_name in SOURCE_TABLES.items():
            csv_path = _resolve_csv_path(data_dir, file_name)
            row_counts[table_name] = _load_one_duckdb(
                connection, csv_path, table_name, batch_id, loaded_at
            )

    return row_counts


def _load_one_duckdb(
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


def _snowflake_credentials_from_env() -> dict[str, str]:
    """Read Snowflake connection settings from the environment.

    Uses the exact variable names transform/profiles.yml reads for the
    `snowflake` dbt target. Never hardcode a credential here -- if one of
    these is unset, fail with a clear message rather than connecting with a
    blank or guessed value.
    """
    missing = [
        env_name for env_name in SNOWFLAKE_ENV_VARS.values() if not os.environ.get(env_name)
    ]
    if missing:
        raise SystemExit(
            "Cannot load into Snowflake: missing environment variable(s) "
            f"{', '.join(missing)}. Set the same variables transform/profiles.yml "
            "expects for the `snowflake` target -- see snowflake/README.md."
        )
    credentials = {key: os.environ[env_name] for key, env_name in SNOWFLAKE_ENV_VARS.items()}
    credentials["schema"] = "raw"
    return credentials


def load_raw_to_snowflake(data_dir: str = "data/raw") -> dict[str, int]:
    """Load every source CSV into Snowflake's RAW schema. Returns row counts by table.

    Column-for-column, this produces the same RAW contract as `load_raw()`:
    every business column VARCHAR, plus `_loaded_at`, `_source_file` and
    `_batch_id`. Implemented but not exercised against a live account in this
    project -- see snowflake/README.md.
    """
    try:
        import pandas as pd
        from snowflake.connector import connect
        from snowflake.connector.pandas_tools import write_pandas
    except ImportError as exc:
        raise SystemExit(
            "Snowflake support requires the snowflake-connector-python and "
            "pandas packages, which ship transitively with dbt-snowflake. "
            "Run `uv sync` and retry, or use --target duckdb instead.\n"
            f"Original import error: {exc}"
        ) from exc

    batch_id, loaded_at = _new_batch()
    credentials = _snowflake_credentials_from_env()

    row_counts: dict[str, int] = {}
    connection = connect(**credentials)
    try:
        connection.cursor().execute("create schema if not exists raw")
        for file_name, table_name in SOURCE_TABLES.items():
            csv_path = _resolve_csv_path(data_dir, file_name)
            row_counts[table_name] = _load_one_snowflake(
                connection, write_pandas, pd, csv_path, table_name, batch_id, loaded_at
            )
    finally:
        connection.close()

    return row_counts


def _load_one_snowflake(
    connection,
    write_pandas,
    pd,
    csv_path: Path,
    table_name: str,
    batch_id: str,
    loaded_at: datetime,
) -> int:
    """Replace one RAW table in Snowflake from one CSV, every column VARCHAR."""
    frame = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    frame.columns = [column.upper() for column in frame.columns]
    frame["_LOADED_AT"] = loaded_at
    frame["_SOURCE_FILE"] = csv_path.name
    frame["_BATCH_ID"] = batch_id

    success, _, num_rows, _ = write_pandas(
        connection,
        frame,
        table_name.upper(),
        auto_create_table=True,
        overwrite=True,
    )
    if not success:
        raise RuntimeError(f"write_pandas did not report success for {table_name}")
    return num_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=["duckdb", "snowflake"],
        default="duckdb",
        help="Where to land the raw CSVs. Defaults to duckdb, which needs no account.",
    )
    parser.add_argument("--db-path", default="warehouse.duckdb", help="duckdb target only")
    parser.add_argument("--data-dir", default="data/raw")
    args = parser.parse_args()

    if args.target == "duckdb":
        row_counts = load_raw(args.db_path, args.data_dir)
    else:
        row_counts = load_raw_to_snowflake(args.data_dir)

    for table_name, count in row_counts.items():
        print(f"{table_name:<28} {count:>6} rows")


if __name__ == "__main__":
    main()
