"""The monthly revenue aggregation, in PySpark.

The same transform as models/intermediate/int_order_revenue_monthly.sql. Kept
in step by tests/test_spark_parity.py, which asserts the two produce identical
output. Written against the DataFrame API rather than spark.sql() so the
comparison is between two genuinely different implementations, not the same
SQL string run twice.
"""

from __future__ import annotations

import sys
import types

# --- Environment compatibility shim (not part of the business logic) -------
# Python 3.12 removed the stdlib `distutils` module. pyspark 3.5.x still does
# `from distutils.version import LooseVersion` at runtime inside
# `createDataFrame()` when converting a pandas DataFrame, and normally relies
# on `setuptools` to backport it. `setuptools` is not installed in this repo's
# venv (and is not a declared dependency of pyspark in uv.lock), so without
# this shim `spark.createDataFrame(...)` raises `ModuleNotFoundError:
# No module named 'distutils'`. This provides only the one attribute pyspark
# needs, using nothing but the standard library -- no package is installed,
# and pyproject.toml/uv.lock are untouched.
if "distutils.version" not in sys.modules:
    try:
        import distutils.version  # noqa: F401
    except ModuleNotFoundError:
        _distutils = types.ModuleType("distutils")
        _version = types.ModuleType("distutils.version")

        class LooseVersion:
            def __init__(self, vstring: str) -> None:
                self.vstring = vstring
                self.version = [
                    int(part) if part.isdigit() else part
                    for part in vstring.split(".")
                ]

            def __lt__(self, other: "LooseVersion") -> bool:
                return self.version < other.version

        _version.LooseVersion = LooseVersion
        _distutils.version = _version
        sys.modules["distutils"] = _distutils
        sys.modules["distutils.version"] = _version
# -----------------------------------------------------------------------------

import duckdb
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

# --- Arrow configuration note -------------------------------------------------
# Without Arrow-based conversion, pyspark's pure-Python schema inference over
# a pandas DataFrame mishandles pandas' nullable "boolean" extension dtype
# (used here for is_active_account, which contains a real NULL) and raises
# `PySparkTypeError: [CANNOT_MERGE_TYPE] Can not merge type BooleanType and
# StructType`. pyarrow IS installed, but Arrow conversion is off by default in
# this pyspark build. SparkSession is a JVM-wide singleton, so this config
# must be set on the builder at the point a session is actually created --
# see `main()` below for standalone runs, and tests/test_spark_parity.py's
# `spark` fixture for the test suite. Importing this module has no side
# effects: it must not create or configure a session itself.
ARROW_ENABLED_CONFIG = ("spark.sql.execution.arrow.pyspark.enabled", "true")
# -----------------------------------------------------------------------------

GRAIN = ["product_id", "region", "revenue_month"]


def aggregate_monthly_revenue(orders: DataFrame) -> DataFrame:
    """Roll recognised revenue up to product x region x month.

    Mirrors business rules R1 (shipped orders only) and D3 (a NULL margin
    contributes nothing, with coverage reported separately).
    """
    recognised = F.col("is_recognised_revenue")

    return (
        orders.groupBy(*GRAIN)
        .agg(
            F.sum(F.when(recognised, F.col("gross_revenue")).otherwise(0))
            .cast("decimal(18,2)")
            .alias("actual_revenue"),

            F.sum(F.when(recognised, F.col("quantity")).otherwise(0))
            .cast("long")
            .alias("actual_quantity"),

            F.sum(
                F.when(recognised, F.coalesce(F.col("gross_margin"), F.lit(0))).otherwise(0)
            )
            .cast("decimal(18,2)")
            .alias("actual_margin"),

            F.count(F.lit(1)).alias("order_count"),

            F.sum(F.when(F.col("delivery_status") == "On Time", 1).otherwise(0))
            .alias("on_time_count"),

            F.sum(F.when(F.col("delivery_status") == "Late", 1).otherwise(0))
            .alias("late_count"),

            F.sum(F.when(F.col("order_status") == "Cancelled", 1).otherwise(0))
            .alias("cancelled_count"),

            F.sum(F.when(F.col("has_standard_cost"), 1).otherwise(0))
            .alias("orders_with_known_cost"),
        )
    )


def main() -> None:
    spark = (
        SparkSession.builder.appName("int_order_revenue_monthly")
        .master("local[*]")
        .config(*ARROW_ENABLED_CONFIG)
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    connection = duckdb.connect("warehouse.duckdb", read_only=True)
    orders = connection.execute("select * from staging.int_orders_enriched").fetch_df()
    connection.close()

    result = aggregate_monthly_revenue(spark.createDataFrame(orders))
    result.orderBy(*GRAIN).show(20, truncate=False)
    print(f"{result.count()} rows at grain {GRAIN}")

    spark.stop()


if __name__ == "__main__":
    main()
