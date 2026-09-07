"""Data-source layer for the revenue dashboard.

Owns every way the dashboard reaches the `analytics` schema, resolving one of
three backends at runtime, in order:

1. An active Snowpark session -- present when this app is deployed as a
   Streamlit in Snowflake app. Detected with a guarded import so this module
   still loads where `snowflake.snowpark` is not installed (Community Cloud).
2. Snowflake connection settings in the environment -- the same SNOWFLAKE_*
   variables `transform/profiles.yml` and `ingest/load_raw.py` already read,
   including SNOWFLAKE_AUTHENTICATOR. Connected to with
   snowflake-connector-python, guarded the same way, since Streamlit in
   Snowflake has no need for that package and Community Cloud does not
   install it.
3. Otherwise, the committed DuckDB file at the repo root.

Object names differ by backend -- `analytics.mart_revenue_performance` in
DuckDB, `<database>.analytics.mart_revenue_performance` in Snowflake -- so
every caller in this codebase writes queries against the unqualified
`analytics.*` or `raw.*` names, and `query()` is the one place that prefixes
them with the active backend's database qualifier before the SQL runs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).parent.parent / "warehouse.duckdb"

DEFAULT_SNOWFLAKE_DATABASE = "REVENUE_ANALYTICS"
DEFAULT_SNOWFLAKE_ROLE = "REPORTER"
DEFAULT_AUTHENTICATOR = "snowflake"

# There is no local file to stat for a cache key the way the DuckDB path has,
# so a short time-based TTL stands in: it keeps the dashboard responsive
# across reruns while still picking up a fresh mart within a minute of a dbt
# run against the warehouse.
SNOWFLAKE_CACHE_TTL_SECONDS = 60

BACKEND_SNOWPARK = "Snowflake (Streamlit in Snowflake)"
BACKEND_SNOWFLAKE_CONNECTOR = "Snowflake (live connection)"
BACKEND_DUCKDB = "DuckDB (local file)"


class DataSourceError(RuntimeError):
    """Raised when the resolved backend cannot serve a query.

    Callers render this with `st.error` instead of letting a traceback
    surface, the same friendly-error contract the DuckDB-only app had.
    """


def _get_active_snowpark_session():
    """Return the active Snowpark session, or None outside Streamlit in Snowflake.

    Guarded import: `snowflake.snowpark` is only installed inside a Streamlit
    in Snowflake app, never on Community Cloud.
    """
    try:
        from snowflake.snowpark.context import get_active_session
    except ImportError:
        return None

    try:
        return get_active_session()
    except Exception:
        return None


def _snowflake_env_settings() -> dict[str, str] | None:
    """Read SNOWFLAKE_* connection settings from the environment, if present.

    Same variable names `transform/profiles.yml` and `ingest/load_raw.py`
    use, so one `export` block configures ingestion, transformation and this
    dashboard. Returns None (rather than raising) when the essentials are
    absent, so the caller can fall through to the DuckDB backend.
    """
    account = os.environ.get("SNOWFLAKE_ACCOUNT")
    user = os.environ.get("SNOWFLAKE_USER")
    if not account or not user:
        return None

    authenticator = os.environ.get("SNOWFLAKE_AUTHENTICATOR", DEFAULT_AUTHENTICATOR)
    settings = {
        "account": account,
        "user": user,
        "role": os.environ.get("SNOWFLAKE_ROLE", DEFAULT_SNOWFLAKE_ROLE),
        "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", ""),
        "database": os.environ.get("SNOWFLAKE_DATABASE", DEFAULT_SNOWFLAKE_DATABASE),
        "authenticator": authenticator,
    }

    # Only password authentication needs a password -- an SSO/OAuth sign-in
    # has none to give, mirroring the same branch in
    # `ingest/load_raw.py:_snowflake_credentials_from_env`.
    if authenticator == DEFAULT_AUTHENTICATOR:
        password = os.environ.get("SNOWFLAKE_PASSWORD")
        if password:
            settings["password"] = password

    return settings


def _database_qualifier() -> str:
    """The single place backend-specific object naming is decided.

    Empty for DuckDB (objects are already `analytics.<table>`); the
    Snowflake database name, dot-terminated, otherwise -- so
    `f"{_database_qualifier()}analytics.mart_revenue_performance"` resolves
    correctly against either backend.
    """
    if _resolve_backend()["kind"] == "duckdb":
        return ""
    database = os.environ.get("SNOWFLAKE_DATABASE", DEFAULT_SNOWFLAKE_DATABASE)
    return f"{database}."


@st.cache_resource(show_spinner=False)
def _resolve_backend() -> dict:
    """Decide which backend serves this process, once, and cache the choice.

    A Snowpark session or a live connection is a resource worth reusing
    across reruns, not recomputed on every widget interaction.
    """
    session = _get_active_snowpark_session()
    if session is not None:
        return {"kind": "snowpark", "session": session}

    settings = _snowflake_env_settings()
    if settings is not None:
        return {"kind": "connector", "settings": settings}

    return {"kind": "duckdb"}


@st.cache_resource(show_spinner=False)
def _connector_connection(settings_key: tuple):
    """Open (and cache) one snowflake-connector-python connection per process.

    Guarded import: `snowflake.connector` ships transitively with
    dbt-snowflake in this project's own environment, but is absent on
    Community Cloud and inside Streamlit in Snowflake -- both paths must
    still import this module without it.
    """
    from snowflake.connector import connect

    return connect(**dict(settings_key))


def _warehouse_mtime() -> float:
    return DB_PATH.stat().st_mtime


@st.cache_data(show_spinner=False)
def _cached_duckdb_query(sql: str, db_mtime: float) -> pd.DataFrame:
    # db_mtime is passed in purely so Streamlit's cache key includes it:
    # whenever `make build` rewrites warehouse.duckdb the mtime changes,
    # which invalidates the cache instead of serving stale numbers for the
    # life of the process.
    import duckdb

    connection = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return connection.execute(sql).fetch_df()
    finally:
        connection.close()


@st.cache_data(ttl=SNOWFLAKE_CACHE_TTL_SECONDS, show_spinner=False)
def _cached_connector_query(sql: str, settings_key: tuple) -> pd.DataFrame:
    connection = _connector_connection(settings_key)
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        df = cursor.fetch_pandas_all()
        df.columns = [c.lower() for c in df.columns]
        return df
    finally:
        cursor.close()


@st.cache_data(ttl=SNOWFLAKE_CACHE_TTL_SECONDS, show_spinner=False)
def _cached_snowpark_query(sql: str) -> pd.DataFrame:
    session = _resolve_backend()["session"]
    df = session.sql(sql).to_pandas()
    df.columns = [c.lower() for c in df.columns]
    return df


def backend_name() -> str:
    """The active backend's display name, for the dashboard's own caption."""
    kind = _resolve_backend()["kind"]
    return {
        "snowpark": BACKEND_SNOWPARK,
        "connector": BACKEND_SNOWFLAKE_CONNECTOR,
        "duckdb": BACKEND_DUCKDB,
    }[kind]


def query(sql: str) -> pd.DataFrame:
    """Run `sql`, written against unqualified `analytics.*` objects.

    Applies the active backend's database qualifier, dispatches to whichever
    backend `_resolve_backend` chose, and raises `DataSourceError` with an
    actionable message instead of letting a driver-specific exception (a
    missing DuckDB file, a missing table, a failed Snowflake connection)
    reach the caller as a traceback.
    """
    backend = _resolve_backend()
    qualifier = _database_qualifier()
    qualified_sql = (
        sql.replace("analytics.", f"{qualifier}analytics.")
        .replace("staging.", f"{qualifier}staging.")
        .replace("raw.", f"{qualifier}raw.")
    )

    try:
        if backend["kind"] == "duckdb":
            if not DB_PATH.exists():
                raise DataSourceError(
                    "warehouse.duckdb not found. Run `make ingest && make build` first."
                )
            import duckdb

            try:
                return _cached_duckdb_query(qualified_sql, _warehouse_mtime())
            except duckdb.Error as error:
                raise DataSourceError(
                    "The warehouse looks incomplete (a required table could "
                    "not be read). Run `make ingest && make build` to "
                    f"rebuild it.\n\nDetails: {error}"
                ) from error

        if backend["kind"] == "connector":
            settings_key = tuple(sorted(backend["settings"].items()))
            return _cached_connector_query(qualified_sql, settings_key)

        return _cached_snowpark_query(qualified_sql)

    except DataSourceError:
        raise
    except Exception as error:
        raise DataSourceError(
            "Could not read from Snowflake. Check the SNOWFLAKE_* "
            f"environment variables (see snowflake/README.md).\n\nDetails: {error}"
        ) from error
