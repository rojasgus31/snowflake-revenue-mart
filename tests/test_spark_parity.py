"""Assert the SQL and PySpark implementations of the monthly aggregation agree.

Same logic, two engines. If they diverge, one of them is wrong -- and a
transform that only works on the engine it was written for is not portable.
"""

import duckdb
import pandas as pd
import pytest

pyspark = pytest.importorskip("pyspark")

from pyspark.sql import SparkSession  # noqa: E402

from spark.int_order_revenue_monthly_spark import aggregate_monthly_revenue  # noqa: E402

GRAIN = ["product_id", "region", "revenue_month"]
DB_PATH = "warehouse.duckdb"


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.appName("parity-test").master("local[2]").getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    """Sort by grain and coerce numerics so the comparison tests values, not layout."""
    frame = frame.sort_values(GRAIN).reset_index(drop=True)
    frame["revenue_month"] = pd.to_datetime(frame["revenue_month"]).dt.date
    for column in frame.columns:
        if column not in GRAIN:
            frame[column] = pd.to_numeric(frame[column]).round(2)
    return frame[sorted(frame.columns)]


def test_spark_matches_sql(spark):
    connection = duckdb.connect(DB_PATH, read_only=True)
    sql_result = connection.execute(
        "select * from staging.int_order_revenue_monthly"
    ).fetch_df()
    orders = connection.execute("select * from staging.int_orders_enriched").fetch_df()
    connection.close()

    spark_result = aggregate_monthly_revenue(spark.createDataFrame(orders)).toPandas()

    pd.testing.assert_frame_equal(
        _normalise(sql_result),
        _normalise(spark_result),
        check_dtype=False,
        obj="SQL vs PySpark monthly aggregation",
    )


def test_spark_excludes_unrecognised_revenue(spark):
    """R1 must hold in the Spark implementation too, not only in SQL."""
    connection = duckdb.connect(DB_PATH, read_only=True)
    orders = connection.execute("select * from staging.int_orders_enriched").fetch_df()
    connection.close()

    result = aggregate_monthly_revenue(spark.createDataFrame(orders)).toPandas()

    total_all_statuses = orders["gross_revenue"].sum()
    assert result["actual_revenue"].sum() < total_all_statuses
    assert result["cancelled_count"].sum() == 13
