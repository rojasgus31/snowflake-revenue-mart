# Revenue Performance Mart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable Snowflake + dbt analytics pipeline over the assessment dataset that produces `analytics.mart_revenue_performance`, quarantines every known data defect with a stated reason, and publishes a public Streamlit dashboard requiring no warehouse credentials.

**Architecture:** A Python loader lands the four CSVs into a RAW schema as all-VARCHAR tables with ingestion metadata (Fivetran-shaped). dbt transforms RAW through staging (typecast, DQ routing to rejects tables), intermediate (region and cost enrichment, monthly aggregation), and marts (conformed star). The grain conflict between order-level delivery status and monthly forecast is resolved with two fact tables — `fct_orders` and `fct_forecast` — full-outer-joined into a monthly mart. Identical model code runs against DuckDB (committed to the repo, reviewer-runnable) and a Snowflake trial.

**Tech Stack:** Python 3.12, uv, dbt-core 1.10 with dbt-duckdb and dbt-snowflake adapters, DuckDB 1.5, dbt_utils, PySpark 3.5 on Temurin 17, Streamlit, pytest, sqlfluff.

**Spec:** `docs/superpowers/specs/2026-09-04-snowflake-revenue-mart-design.md`

## Global Constraints

- **Python 3.12**, not 3.13. dbt adapter support for 3.13 is uneven; pin it in `uv venv --python 3.12`.
- **Every model must compile on both DuckDB and Snowflake.** Use only SQL both engines support: `try_cast`, `split_part`, `coalesce`, `nullif`, `sum`, `count`, window functions, `date_trunc`. No engine-specific functions. If a construct is needed on one engine only, wrap it in a macro with `{% if target.type == 'snowflake' %}`.
- **`dbt build` must exit 0 with rejects present.** Rejected rows are data, not build failures. Only genuine contract violations fail the build.
- **No row is deleted.** Every source row lands in either a mart-feeding model or a rejects table with a non-null `dq_failure_reason`.
- **Money columns are `decimal(18,2)`.** Never `float` — floating-point revenue sums do not reconcile.
- **Defect IDs D1–D12** from spec §3 are the canonical names. Tests and reject reasons reference them.
- **Reject reason vocabulary** (exact strings): `AMBIGUOUS_DUPLICATE`, `MISSING_ORDER_DATE`, `MISSING_UNIT_PRICE`, `INVALID_QUANTITY`.
- **Region vocabulary** (exact strings): `LATAM`, `North America`, `EMEA`, `APAC`, `UNMAPPED`.
- **Delivery status vocabulary** (exact strings): `On Time`, `Late`, `Not Delivered`, `Cancelled`, `Unknown`.
- **Revenue recognition** (spec R1, R2): actuals are `Shipped` orders only, recognized on `order_date`.

---

## File Structure

```
.
├── Makefile                          # every command a reviewer needs
├── pyproject.toml                    # uv-managed deps
├── data/raw/*.csv                    # the four source files (moved from repo root)
├── ingest/
│   └── load_raw.py                   # CSV -> RAW schema, all VARCHAR + metadata
├── transform/                        # dbt project root
│   ├── dbt_project.yml
│   ├── profiles.yml                  # duckdb (default) + snowflake targets
│   ├── packages.yml                  # dbt_utils
│   ├── seeds/
│   │   └── seed_region.csv
│   ├── macros/
│   │   └── delivery_status.sql       # R4 logic, single definition
│   ├── models/
│   │   ├── staging/
│   │   │   ├── _sources.yml
│   │   │   ├── _staging__models.yml
│   │   │   ├── stg_oracle__orders_flagged.sql    # ephemeral: typecast + DQ flags
│   │   │   ├── stg_oracle__orders.sql            # flagged where reason is null
│   │   │   ├── stg_oracle__orders_rejects.sql    # flagged where reason is not null
│   │   │   ├── stg_salesforce__accounts.sql
│   │   │   ├── stg_adaptive__forecast.sql
│   │   │   └── stg_erp__products.sql
│   │   ├── intermediate/
│   │   │   ├── _intermediate__models.yml
│   │   │   ├── int_orders_enriched.sql
│   │   │   └── int_order_revenue_monthly.sql     # the PySpark twin
│   │   └── marts/
│   │       ├── _marts__models.yml
│   │       ├── dim_date.sql       dim_region.sql
│   │       ├── dim_customer.sql   dim_product.sql
│   │       ├── fct_orders.sql     fct_forecast.sql
│   │       ├── mart_revenue_performance.sql
│   │       └── dq_reject_summary.sql
│   └── tests/                        # singular defect-regression tests
├── spark/
│   └── int_order_revenue_monthly_spark.py
├── app/
│   └── streamlit_app.py
├── snowflake/
│   └── 01_bootstrap.sql              # databases, schemas, roles, warehouse
├── tests/
│   ├── test_load_raw.py
│   └── test_spark_parity.py
├── .github/workflows/ci.yml
└── docs/
    ├── POWER_BI.md
    └── superpowers/{specs,plans}/
```

Staging is the only layer that knows source column names. `stg_oracle__orders_flagged` is ephemeral and exists so the keep-path and reject-path share one definition of "what is wrong with this row" — the DQ rules are written once.

---

### Task 1: Environment and scaffolding

**Files:**
- Create: `pyproject.toml`, `Makefile`, `.python-version`
- Move: `*.csv` → `data/raw/`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: a `.venv` with `dbt` and `duckdb` on PATH via `uv run`; `data/raw/<four csvs>`; `make` targets `install`, `ingest`, `build`, `test`, `app`

- [ ] **Step 1: Install the Java runtime PySpark needs (Task 12) while other work proceeds**

```bash
brew install --cask temurin@17
```

Expected: completes, or reports it is already installed. `java --version` currently fails; after this it prints `openjdk 17.x`.

- [ ] **Step 2: Move the source CSVs out of the repo root**

```bash
mkdir -p data/raw
git mv oracle_orders.csv salesforce_accounts.csv adaptive_forecast.csv product_master.csv data/raw/ 2>/dev/null \
  || mv oracle_orders.csv salesforce_accounts.csv adaptive_forecast.csv product_master.csv data/raw/
ls data/raw/
```

Expected: four `.csv` files listed.

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "revenue-performance-mart"
version = "0.1.0"
description = "Snowflake + dbt revenue performance mart over the Sr. Data Engineer assessment dataset"
requires-python = ">=3.12,<3.13"
dependencies = [
    "dbt-core~=1.10.0",
    "dbt-duckdb~=1.10.0",
    "dbt-snowflake~=1.10.0",
    "duckdb~=1.5.0",
    "pandas~=2.2.0",
    "streamlit~=1.40.0",
    "plotly~=5.24.0",
]

[dependency-groups]
dev = [
    "pytest~=8.3.0",
    "pyspark~=3.5.0",
    "sqlfluff~=3.2.0",
    "sqlfluff-templater-dbt~=3.2.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: Create `.python-version`**

```
3.12
```

- [ ] **Step 5: Create the virtualenv and install**

```bash
uv venv --python 3.12 && uv sync --all-groups
```

Expected: `Installed N packages`. If `uv venv --python 3.12` reports the interpreter is unavailable, run `uv python install 3.12` first, then retry.

- [ ] **Step 6: Verify dbt is on PATH inside the venv**

```bash
uv run dbt --version
```

Expected: `installed: 1.10.x`, and `duckdb: 1.10.x` plus `snowflake: 1.10.x` under "Plugins".

- [ ] **Step 7: Create `Makefile`**

Note the leading tabs — Make requires tab indentation, not spaces.

```makefile
.PHONY: install ingest build test app lint clean all

install:
	uv venv --python 3.12
	uv sync --all-groups
	uv run dbt deps --project-dir transform --profiles-dir transform

ingest:
	uv run python ingest/load_raw.py

build:
	uv run dbt build --project-dir transform --profiles-dir transform

test:
	uv run pytest -v
	uv run dbt test --project-dir transform --profiles-dir transform

app:
	uv run streamlit run app/streamlit_app.py

lint:
	uv run sqlfluff lint transform/models --dialect duckdb

clean:
	rm -rf warehouse.duckdb transform/target transform/dbt_packages

all: ingest build test
```

- [ ] **Step 8: Verify Make parses**

```bash
make -n install
```

Expected: prints the three commands without executing them. If it errors `missing separator`, the indentation is spaces — replace with tabs.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml Makefile .python-version uv.lock data/
git commit -m "chore: project scaffolding and source data layout"
```

---

### Task 2: Raw ingestion loader

**Files:**
- Create: `ingest/load_raw.py`
- Test: `tests/test_load_raw.py`

**Interfaces:**
- Consumes: `data/raw/*.csv` from Task 1
- Produces: `load_raw(db_path: str = "warehouse.duckdb", data_dir: str = "data/raw") -> dict[str, int]` returning `{table_name: row_count}`. Creates DuckDB schema `raw` with tables `raw_oracle_orders`, `raw_salesforce_accounts`, `raw_adaptive_forecast`, `raw_erp_products`. Every column is `VARCHAR`; every table gains `_loaded_at TIMESTAMP`, `_source_file VARCHAR`, `_batch_id VARCHAR`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_load_raw.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_load_raw.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ingest'`.

- [ ] **Step 3: Write the implementation**

Create `ingest/__init__.py` (empty file), then `ingest/load_raw.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/test_load_raw.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Run the loader against the real warehouse file**

```bash
make ingest
```

Expected:
```
raw_oracle_orders               128 rows
raw_salesforce_accounts          20 rows
raw_adaptive_forecast           287 rows
raw_erp_products                 12 rows
```

- [ ] **Step 6: Commit**

```bash
git add ingest/ tests/test_load_raw.py
git commit -m "feat: land source CSVs into RAW schema with ingestion metadata"
```

---

### Task 3: dbt project, dual targets, sources

**Files:**
- Create: `transform/dbt_project.yml`, `transform/profiles.yml`, `transform/packages.yml`, `transform/seeds/seed_region.csv`, `transform/models/staging/_sources.yml`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: the `raw` schema from Task 2
- Produces: dbt source `{{ source('oracle', 'raw_oracle_orders') }}` and siblings `salesforce.raw_salesforce_accounts`, `adaptive.raw_adaptive_forecast`, `erp.raw_erp_products`. Seed `seed_region` with columns `region_name`, `is_forecastable`. Targets `duckdb` (default) and `snowflake`.

- [ ] **Step 1: Create `transform/dbt_project.yml`**

```yaml
name: revenue_performance
version: "1.0.0"
config-version: 2
profile: revenue_performance

model-paths: ["models"]
seed-paths: ["seeds"]
macro-paths: ["macros"]
test-paths: ["tests"]
target-path: "target"
clean-targets: ["target", "dbt_packages"]

models:
  revenue_performance:
    staging:
      +schema: staging
      +materialized: view
    intermediate:
      +schema: staging
      +materialized: view
    marts:
      +schema: analytics
      +materialized: table

seeds:
  revenue_performance:
    +schema: staging
```

- [ ] **Step 2: Create `transform/profiles.yml`**

The Snowflake target reads environment variables so no credential is ever committed. `env_var` with a second argument supplies a default, letting the DuckDB target work with none of them set.

```yaml
revenue_performance:
  target: duckdb
  outputs:
    duckdb:
      type: duckdb
      path: ../warehouse.duckdb
      threads: 4

    snowflake:
      type: snowflake
      account: "{{ env_var('SNOWFLAKE_ACCOUNT', '') }}"
      user: "{{ env_var('SNOWFLAKE_USER', '') }}"
      password: "{{ env_var('SNOWFLAKE_PASSWORD', '') }}"
      role: "{{ env_var('SNOWFLAKE_ROLE', 'TRANSFORMER') }}"
      warehouse: "{{ env_var('SNOWFLAKE_WAREHOUSE', 'WH_TRANSFORM_XS') }}"
      database: "{{ env_var('SNOWFLAKE_DATABASE', 'REVENUE_ANALYTICS') }}"
      schema: staging
      threads: 4
      client_session_keep_alive: false
```

- [ ] **Step 3: Create `transform/packages.yml`**

```yaml
packages:
  - package: dbt-labs/dbt_utils
    version: [">=1.3.0", "<2.0.0"]
```

- [ ] **Step 4: Install packages**

```bash
uv run dbt deps --project-dir transform --profiles-dir transform
```

Expected: `Installing dbt-labs/dbt_utils` then `Installed from version 1.3.x`.

- [ ] **Step 5: Create `transform/seeds/seed_region.csv`**

`UNMAPPED` is a real member of the region dimension, not an absence. Marking it `is_forecastable = false` records why it can never match a forecast row (spec D2).

```csv
region_name,is_forecastable
LATAM,true
North America,true
EMEA,true
APAC,true
UNMAPPED,false
```

- [ ] **Step 6: Create `transform/models/staging/_sources.yml`**

```yaml
version: 2

sources:
  - name: oracle
    description: >
      Order transactions extracted from Oracle Fusion Cloud. Landed as-is:
      all columns VARCHAR, no deduplication, no coercion.
    schema: raw
    tables:
      - name: raw_oracle_orders
        description: One row per order line, as delivered by the extract.

  - name: salesforce
    description: Customer accounts from Salesforce. Sole source of region.
    schema: raw
    tables:
      - name: raw_salesforce_accounts

  - name: adaptive
    description: Revenue plan from Adaptive Planning, at product x region x month.
    schema: raw
    tables:
      - name: raw_adaptive_forecast

  - name: erp
    description: Product master with standard cost. Sole source of cost.
    schema: raw
    tables:
      - name: raw_erp_products
```

- [ ] **Step 7: Add dbt artifacts to `.gitignore`**

```bash
printf 'transform/target/\ntransform/dbt_packages/\ntransform/logs/\nlogs/\n' >> .gitignore
```

- [ ] **Step 8: Verify the project parses and the seed loads**

```bash
uv run dbt seed --project-dir transform --profiles-dir transform
```

Expected: `1 of 1 OK loaded seed file ... seed_region` and `Completed successfully`.

- [ ] **Step 9: Verify sources resolve**

```bash
uv run dbt compile --project-dir transform --profiles-dir transform
```

Expected: `Completed successfully`. A `Compilation Error ... source not found` means the schema name in `_sources.yml` does not match the `raw` schema created in Task 2.

- [ ] **Step 10: Commit**

```bash
git add transform/ .gitignore
git commit -m "feat: dbt project with DuckDB and Snowflake targets"
```

---

### Task 4: Oracle orders staging and reject routing

This is the data quality core. It implements D1, D4, D5, D6, D9, and D11.

**Files:**
- Create: `transform/models/staging/stg_oracle__orders_flagged.sql`, `stg_oracle__orders.sql`, `stg_oracle__orders_rejects.sql`, `transform/models/staging/_staging__models.yml`
- Test: `transform/tests/assert_ord00005_quarantined.sql`, `transform/tests/assert_orders_partition_is_complete.sql`

**Interfaces:**
- Consumes: `source('oracle', 'raw_oracle_orders')` from Task 3
- Produces: `stg_oracle__orders` with columns `order_id, customer_id, product_id, order_date, promised_delivery_date, actual_delivery_date, quantity, unit_price, gross_revenue, plant_city, plant_country, order_status, source_batch`. `stg_oracle__orders_rejects` has those same columns plus `dq_failure_reason`. Downstream tasks read `stg_oracle__orders` only.

- [ ] **Step 1: Write the failing tests**

Create `transform/tests/assert_ord00005_quarantined.sql`. A dbt singular test passes when it returns zero rows.

```sql
-- D1: ORD00005 appears twice with conflicting quantity (31 vs 99) and no
-- column that could identify the later record. Both copies must be rejected;
-- keeping either one would fabricate a fact. Fails if any copy reached the
-- clean model, or if the rejects are labelled with the wrong reason.

select 'leaked into clean model' as failure, count(*) as row_count
from {{ ref('stg_oracle__orders') }}
where order_id = 'ORD00005'
having count(*) > 0

union all

select 'not quarantined as an ambiguous duplicate' as failure, count(*) as row_count
from {{ ref('stg_oracle__orders_rejects') }}
where order_id = 'ORD00005'
  and dq_failure_reason = 'AMBIGUOUS_DUPLICATE'
having count(*) <> 2
```

Create `transform/tests/assert_orders_partition_is_complete.sql`:

```sql
-- Clean and rejected rows must partition the source exactly: no row invented,
-- no row silently dropped. This is the guarantee that makes the rejects table
-- meaningful rather than decorative.

with counts as (
    select
        (select count(*) from {{ source('oracle', 'raw_oracle_orders') }}) as source_rows,
        (select count(*) from {{ ref('stg_oracle__orders') }}) as clean_rows,
        (select count(*) from {{ ref('stg_oracle__orders_rejects') }}) as rejected_rows
)

select *
from counts
where source_rows <> clean_rows + rejected_rows
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run dbt test --project-dir transform --profiles-dir transform
```

Expected: FAIL — `Compilation Error ... depends on a node named 'stg_oracle__orders' which was not found`.

- [ ] **Step 3: Write the flagged model**

Create `transform/models/staging/stg_oracle__orders_flagged.sql`. Ephemeral, so the DQ rules exist in exactly one place and both the clean and reject models inherit them.

```sql
{{ config(materialized='ephemeral') }}

-- Typecast the raw text and classify every row against the data quality rules
-- from the design (D1, D4, D5, D6, D9, D11). This model decides nothing about
-- what to do with a bad row -- it only names the problem. The keep and reject
-- models below branch on that single verdict, so the rules never drift apart.

with source as (

    select * from {{ source('oracle', 'raw_oracle_orders') }}

),

typed as (

    select
        trim(order_id)      as order_id,
        trim(customer_id)   as customer_id,
        trim(product_id)    as product_id,

        try_cast(nullif(trim(order_date), '') as date)              as order_date,
        try_cast(nullif(trim(promised_delivery_date), '') as date)  as promised_delivery_date,
        try_cast(nullif(trim(actual_delivery_date), '') as date)    as actual_delivery_date,

        try_cast(nullif(trim(quantity), '') as integer)             as quantity,
        try_cast(nullif(trim(unit_price), '') as decimal(18, 2))    as unit_price,

        -- D11: plant_location arrives as "City, Country" in one column,
        -- which blocks any grouping by country.
        trim(split_part(plant_location, ',', 1)) as plant_city,
        trim(split_part(plant_location, ',', 2)) as plant_country,

        trim(order_status) as order_status,

        -- D9: two extracts are present. ORD9xxxx is a later batch, not
        -- corruption. Tagging it lets an analyst spot extract-level skew.
        case
            when trim(order_id) like 'ORD9%' then 'BATCH_B'
            else 'BATCH_A'
        end as source_batch

    from source

),

with_duplicate_count as (

    select
        *,
        count(*) over (partition by order_id) as order_id_occurrences
    from typed

),

classified as (

    select
        order_id,
        customer_id,
        product_id,
        order_date,
        promised_delivery_date,
        actual_delivery_date,
        quantity,
        unit_price,
        cast(quantity * unit_price as decimal(18, 2)) as gross_revenue,
        plant_city,
        plant_country,
        order_status,
        source_batch,

        -- Order matters: a duplicated key is reported as such even when the
        -- row also has a missing field, because the duplicate is the defect
        -- that has to be fixed upstream.
        case
            when order_id_occurrences > 1               then 'AMBIGUOUS_DUPLICATE'
            when order_date is null                     then 'MISSING_ORDER_DATE'
            when unit_price is null                     then 'MISSING_UNIT_PRICE'
            when quantity is null or quantity <= 0      then 'INVALID_QUANTITY'
        end as dq_failure_reason

    from with_duplicate_count

)

select * from classified
```

- [ ] **Step 4: Write the clean and reject models**

Create `transform/models/staging/stg_oracle__orders.sql`:

```sql
-- Orders that passed every data quality rule. This is the only orders model
-- any downstream layer reads.

select
    order_id,
    customer_id,
    product_id,
    order_date,
    promised_delivery_date,
    actual_delivery_date,
    quantity,
    unit_price,
    gross_revenue,
    plant_city,
    plant_country,
    order_status,
    source_batch

from {{ ref('stg_oracle__orders_flagged') }}
where dq_failure_reason is null
```

Create `transform/models/staging/stg_oracle__orders_rejects.sql`:

```sql
-- Rows excluded from analytics, retained with the reason. Quarantining rather
-- than deleting is what lets the reconciliation test in Task 11 prove that no
-- revenue disappeared, and gives the source-system owner an actionable list.

select
    order_id,
    customer_id,
    product_id,
    order_date,
    promised_delivery_date,
    actual_delivery_date,
    quantity,
    unit_price,
    gross_revenue,
    plant_city,
    plant_country,
    order_status,
    source_batch,
    dq_failure_reason

from {{ ref('stg_oracle__orders_flagged') }}
where dq_failure_reason is not null
```

- [ ] **Step 5: Write the schema tests**

Create `transform/models/staging/_staging__models.yml`:

```yaml
version: 2

models:
  - name: stg_oracle__orders
    description: >
      Order lines that passed every data quality rule, typecast and with
      plant location normalised. One row per order.
    columns:
      - name: order_id
        description: Primary key. Guaranteed unique here because D1 duplicates are rejected.
        data_tests: [unique, not_null]
      - name: customer_id
        data_tests: [not_null]
      - name: product_id
        data_tests: [not_null]
      - name: order_date
        data_tests: [not_null]
      - name: unit_price
        data_tests: [not_null]
      - name: gross_revenue
        data_tests: [not_null]
      - name: order_status
        data_tests:
          - accepted_values:
              values: ["Shipped", "Open", "Cancelled"]
      - name: source_batch
        data_tests:
          - accepted_values:
              values: ["BATCH_A", "BATCH_B"]
      - name: plant_country
        description: Split out of the compound plant_location string (D11).
        data_tests:
          - accepted_values:
              values: ["Costa Rica", "USA"]

  - name: stg_oracle__orders_rejects
    description: >
      Rows excluded from analytics, retained with the reason they failed.
      Never deleted -- Task 11 reconciles against this table.
    columns:
      - name: dq_failure_reason
        data_tests:
          - not_null
          - accepted_values:
              values:
                - AMBIGUOUS_DUPLICATE
                - MISSING_ORDER_DATE
                - MISSING_UNIT_PRICE
                - INVALID_QUANTITY
```

- [ ] **Step 6: Build and verify the tests pass**

```bash
uv run dbt build --project-dir transform --profiles-dir transform
```

Expected: `Completed successfully`, with `stg_oracle__orders`, `stg_oracle__orders_rejects`, the two singular tests, and the schema tests all `PASS`.

- [ ] **Step 7: Verify the reject counts match the profiled defects**

```bash
duckdb warehouse.duckdb -c "select dq_failure_reason, count(*) from staging.stg_oracle__orders_rejects group by 1 order by 1"
```

Expected exactly:
```
AMBIGUOUS_DUPLICATE   2
INVALID_QUANTITY      1
MISSING_ORDER_DATE    1
MISSING_UNIT_PRICE    1
```
Five rejected rows, so `stg_oracle__orders` holds 123 of the 128 source rows.

- [ ] **Step 8: Commit**

```bash
git add transform/models/staging/ transform/tests/
git commit -m "feat: orders staging with data quality reject routing"
```

---

### Task 5: Remaining staging models

**Files:**
- Create: `transform/models/staging/stg_salesforce__accounts.sql`, `stg_adaptive__forecast.sql`, `stg_erp__products.sql`
- Modify: `transform/models/staging/_staging__models.yml`

**Interfaces:**
- Consumes: the three remaining sources from Task 3
- Produces: `stg_salesforce__accounts` (`customer_id, account_id, account_name, region, account_owner, segment, is_active`); `stg_adaptive__forecast` (`forecast_id, product_id, region, forecast_month, forecast_revenue, forecast_quantity`); `stg_erp__products` (`product_id, product_name, product_family, lifecycle_status, standard_cost, is_active_product`)

These three sources are clean — profiling found no defects in them. No reject tables are needed; the schema tests below are what prove that claim, and will fail loudly if a future extract changes.

- [ ] **Step 1: Write `stg_salesforce__accounts.sql`**

```sql
-- Customer accounts. Sole source of region, which the forecast join depends on.
-- D10: inactive accounts are kept. An order placed while an account was active
-- is still real historical revenue; filtering belongs in the BI layer.

select
    trim(customer_id)   as customer_id,
    trim(account_id)    as account_id,
    trim(account_name)  as account_name,
    trim(region)        as region,
    trim(account_owner) as account_owner,
    trim(segment)       as segment,
    upper(trim(active_flag)) = 'TRUE' as is_active

from {{ source('salesforce', 'raw_salesforce_accounts') }}
```

- [ ] **Step 2: Write `stg_adaptive__forecast.sql`**

```sql
-- Revenue plan at product x region x month. This is the grain the mart adopts,
-- because it is the coarsest of the two inputs and cannot be disaggregated
-- without inventing allocation weights.

select
    trim(forecast_id)  as forecast_id,
    trim(product_id)   as product_id,
    trim(region)       as region,
    cast(forecast_month as date) as forecast_month,
    cast(forecast_revenue as decimal(18, 2)) as forecast_revenue,
    cast(forecast_quantity as integer)       as forecast_quantity

from {{ source('adaptive', 'raw_adaptive_forecast') }}
```

- [ ] **Step 3: Write `stg_erp__products.sql`**

```sql
-- Product master. Sole source of standard_cost, and therefore the only input
-- to gross margin. D10: discontinued and pending products are kept, because
-- orders were placed against them.

select
    trim(product_id)        as product_id,
    trim(product_name)      as product_name,
    trim(product_family)    as product_family,
    trim(lifecycle_status)  as lifecycle_status,
    cast(standard_cost as decimal(18, 2)) as standard_cost,
    trim(lifecycle_status) = 'Active' as is_active_product

from {{ source('erp', 'raw_erp_products') }}
```

- [ ] **Step 4: Add the schema tests**

Append to `transform/models/staging/_staging__models.yml`:

```yaml
  - name: stg_salesforce__accounts
    description: Customer accounts. The only path from an order to a region.
    columns:
      - name: customer_id
        data_tests: [unique, not_null]
      - name: region
        data_tests:
          - not_null
          - accepted_values:
              values: ["LATAM", "North America", "EMEA", "APAC"]
      - name: segment
        data_tests:
          - accepted_values:
              values: ["Enterprise", "Mid-Market", "SMB"]

  - name: stg_adaptive__forecast
    description: Revenue plan at product x region x month.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns: [product_id, region, forecast_month]
    columns:
      - name: forecast_id
        data_tests: [unique, not_null]
      - name: forecast_revenue
        data_tests: [not_null]
      - name: region
        data_tests:
          - accepted_values:
              values: ["LATAM", "North America", "EMEA", "APAC"]
      - name: product_id
        data_tests:
          - relationships:
              to: ref('stg_erp__products')
              field: product_id

  - name: stg_erp__products
    description: Product master. Sole source of standard cost.
    columns:
      - name: product_id
        data_tests: [unique, not_null]
      - name: standard_cost
        description: Never null in this extract; margin depends on it.
        data_tests: [not_null]
      - name: lifecycle_status
        data_tests:
          - accepted_values:
              values: ["Active", "Discontinued", "Pending"]
```

- [ ] **Step 5: Build and verify**

```bash
uv run dbt build --project-dir transform --profiles-dir transform
```

Expected: `Completed successfully`. All four staging models built, every test passing. The `unique_combination_of_columns` test confirms the forecast grain assumption the mart relies on.

- [ ] **Step 6: Commit**

```bash
git add transform/models/staging/
git commit -m "feat: salesforce, forecast, and product staging models"
```

---

### Task 6: Delivery status macro and order enrichment

Implements D2, D3, D7, D8, and rule R4.

**Files:**
- Create: `transform/macros/delivery_status.sql`, `transform/models/intermediate/int_orders_enriched.sql`, `transform/models/intermediate/_intermediate__models.yml`
- Test: `transform/tests/assert_unmapped_region_retained.sql`, `transform/tests/assert_margin_null_without_cost.sql`

**Interfaces:**
- Consumes: `stg_oracle__orders`, `stg_salesforce__accounts`, `stg_erp__products` from Tasks 4–5
- Produces: `int_orders_enriched` with every `stg_oracle__orders` column plus `region`, `segment`, `account_name`, `product_family`, `standard_cost`, `has_standard_cost`, `gross_margin`, `delivery_status`, `revenue_month`, `is_recognised_revenue`. Macro `{{ delivery_status(order_status_column, actual_column, promised_column, order_date_column) }}` returning a SQL `case` expression.

- [ ] **Step 1: Write the failing tests**

Create `transform/tests/assert_unmapped_region_retained.sql`:

```sql
-- D2: CUST999 has no Salesforce account, so it has no region. Its revenue is
-- still real and must survive into the model tagged UNMAPPED, where it shows
-- up as variance no forecast explains. Dropping it would break the
-- reconciliation guarantee in Task 11.

select 'CUST999 revenue was dropped instead of tagged UNMAPPED' as failure
from (select 1 as probe) as p
where not exists (
    select 1
    from {{ ref('int_orders_enriched') }}
    where customer_id = 'CUST999' and region = 'UNMAPPED'
)

union all

select 'a mapped customer was wrongly tagged UNMAPPED' as failure
from {{ ref('int_orders_enriched') }}
where region = 'UNMAPPED' and customer_id <> 'CUST999'
```

Create `transform/tests/assert_margin_null_without_cost.sql`:

```sql
-- D3: PROD999 is absent from the product master, so its standard cost is
-- unknown. Revenue is knowable, margin is not. Defaulting the cost to zero
-- would report a 100% margin -- a plausible-looking lie. Margin must be NULL,
-- and revenue must survive.

select 'margin was fabricated for a product with no known cost' as failure
from {{ ref('int_orders_enriched') }}
where has_standard_cost = false and gross_margin is not null

union all

select 'revenue was dropped for a product with no known cost' as failure
from {{ ref('int_orders_enriched') }}
where product_id = 'PROD999' and gross_revenue is null
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run dbt build --project-dir transform --profiles-dir transform
```

Expected: FAIL — `depends on a node named 'int_orders_enriched' which was not found`.

- [ ] **Step 3: Write the delivery status macro**

Create `transform/macros/delivery_status.sql`. Defining rule R4 once means `fct_orders` and any future consumer cannot disagree about what "Late" means.

```sql
{% macro delivery_status(order_status_column, actual_column, promised_column, order_date_column) %}
-- Business rule R4, evaluated in order of precedence.
--   D7: every Cancelled order in this extract carries a delivery date, which
--       is a source-system contradiction. Status wins; the date is ignored.
--   D8: one row is delivered before it was ordered. The dates are untrustworthy
--       but the revenue is not, so the status degrades to Unknown and the row
--       is retained.
case
    when {{ order_status_column }} = 'Cancelled'              then 'Cancelled'
    when {{ actual_column }} is null                          then 'Not Delivered'
    when {{ actual_column }} < {{ order_date_column }}        then 'Unknown'
    when {{ actual_column }} <= {{ promised_column }}         then 'On Time'
    else 'Late'
end
{% endmacro %}
```

- [ ] **Step 4: Write `int_orders_enriched.sql`**

```sql
-- Attach the two dimensions an order needs but does not carry: region (only
-- reachable through the customer) and standard cost (only in the product
-- master). Both joins are LEFT, because a failed lookup must degrade one
-- attribute rather than delete a row of real revenue.

with orders as (

    select * from {{ ref('stg_oracle__orders') }}

),

accounts as (

    select * from {{ ref('stg_salesforce__accounts') }}

),

products as (

    select * from {{ ref('stg_erp__products') }}

),

enriched as (

    select
        orders.order_id,
        orders.customer_id,
        orders.product_id,
        orders.order_date,
        orders.promised_delivery_date,
        orders.actual_delivery_date,
        orders.quantity,
        orders.unit_price,
        orders.gross_revenue,
        orders.plant_city,
        orders.plant_country,
        orders.order_status,
        orders.source_batch,

        -- D2: an order whose customer is absent from Salesforce has no region.
        -- UNMAPPED keeps the revenue visible and, because it matches no
        -- forecast row, surfaces it as unexplained variance.
        coalesce(accounts.region, 'UNMAPPED') as region,
        accounts.segment,
        accounts.account_name,
        accounts.is_active as is_active_account,

        products.product_family,
        products.lifecycle_status,
        products.standard_cost,
        products.product_id is not null as has_standard_cost,

        -- D3: NULL rather than zero when the cost is unknown.
        case
            when products.standard_cost is not null
                then cast((orders.unit_price - products.standard_cost) * orders.quantity as decimal(18, 2))
        end as gross_margin,

        {{ delivery_status('orders.order_status',
                           'orders.actual_delivery_date',
                           'orders.promised_delivery_date',
                           'orders.order_date') }} as delivery_status,

        -- R2: revenue is recognised on order_date, aligning actuals to the
        -- forecast's monthly grain.
        cast(date_trunc('month', orders.order_date) as date) as revenue_month,

        -- R1: only shipped orders are revenue. Open is pipeline; cancelled is
        -- excluded. The forecast still counted the cancelled demand, and that
        -- gap is genuine business variance rather than a defect to engineer away.
        orders.order_status = 'Shipped' as is_recognised_revenue

    from orders
    left join accounts on orders.customer_id = accounts.customer_id
    left join products on orders.product_id = products.product_id

)

select * from enriched
```

- [ ] **Step 5: Write the schema tests**

Create `transform/models/intermediate/_intermediate__models.yml`:

```yaml
version: 2

models:
  - name: int_orders_enriched
    description: >
      Order lines with region and standard cost attached. Left joins throughout:
      a failed dimension lookup degrades an attribute, never deletes revenue.
    columns:
      - name: order_id
        data_tests: [unique, not_null]
      - name: region
        data_tests:
          - not_null
          - accepted_values:
              values: ["LATAM", "North America", "EMEA", "APAC", "UNMAPPED"]
      - name: delivery_status
        data_tests:
          - not_null
          - accepted_values:
              values: ["On Time", "Late", "Not Delivered", "Cancelled", "Unknown"]
      - name: revenue_month
        data_tests: [not_null]
      - name: gross_revenue
        data_tests: [not_null]
```

- [ ] **Step 6: Build and verify the tests pass**

```bash
uv run dbt build --project-dir transform --profiles-dir transform
```

Expected: `Completed successfully`. Both singular tests PASS, confirming D2 and D3 are handled as designed.

- [ ] **Step 7: Inspect the delivery status distribution**

```bash
duckdb warehouse.duckdb -c "select delivery_status, count(*) from staging.int_orders_enriched group by 1 order by 2 desc"
```

Expected: `Cancelled` 13, plus `On Time`, `Late`, `Not Delivered` (the Open orders), and exactly one `Unknown` (D8). No NULLs.

- [ ] **Step 8: Commit**

```bash
git add transform/macros/ transform/models/intermediate/ transform/tests/
git commit -m "feat: enrich orders with region, cost, and delivery status"
```

---

### Task 7: Monthly revenue aggregation

The transform that Task 12 reimplements in PySpark. Keeping it small and pure is what makes the parity test meaningful.

**Files:**
- Create: `transform/models/intermediate/int_order_revenue_monthly.sql`
- Modify: `transform/models/intermediate/_intermediate__models.yml`

**Interfaces:**
- Consumes: `int_orders_enriched` from Task 6
- Produces: `int_order_revenue_monthly` at grain `(product_id, region, revenue_month)` with columns `product_id, region, revenue_month, actual_revenue, actual_quantity, actual_margin, order_count, on_time_count, late_count, cancelled_count, orders_with_known_cost`. Task 12's PySpark script must produce this exact schema and these exact values.

- [ ] **Step 1: Write the failing test**

Append to `transform/models/intermediate/_intermediate__models.yml`:

```yaml
  - name: int_order_revenue_monthly
    description: >
      Recognised revenue aggregated to the forecast's grain. Implemented twice --
      here in SQL and in spark/int_order_revenue_monthly_spark.py -- with a
      parity test asserting the two agree.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns: [product_id, region, revenue_month]
    columns:
      - name: actual_revenue
        data_tests: [not_null]
      - name: order_count
        data_tests: [not_null]
      - name: region
        data_tests:
          - accepted_values:
              values: ["LATAM", "North America", "EMEA", "APAC", "UNMAPPED"]
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run dbt build --project-dir transform --profiles-dir transform
```

Expected: FAIL — `Compilation Error ... model 'int_order_revenue_monthly' ... was not found`.

- [ ] **Step 3: Write the model**

```sql
-- Roll recognised revenue up to the forecast's grain so the two can be
-- compared. Cancelled and open orders are excluded from the revenue measures
-- per R1 but still counted, because "the forecast assumed these would ship and
-- they did not" is exactly the story the mart exists to tell.

with enriched as (

    select * from {{ ref('int_orders_enriched') }}

),

aggregated as (

    select
        product_id,
        region,
        revenue_month,

        cast(sum(case when is_recognised_revenue then gross_revenue else 0 end)
             as decimal(18, 2)) as actual_revenue,

        sum(case when is_recognised_revenue then quantity else 0 end) as actual_quantity,

        -- NULL-safe by construction: a NULL margin (D3) contributes nothing to
        -- the sum, and orders_with_known_cost below reports the coverage so a
        -- partial total is never mistaken for a complete one.
        cast(sum(case when is_recognised_revenue then coalesce(gross_margin, 0) else 0 end)
             as decimal(18, 2)) as actual_margin,

        count(*) as order_count,
        sum(case when delivery_status = 'On Time' then 1 else 0 end)  as on_time_count,
        sum(case when delivery_status = 'Late' then 1 else 0 end)     as late_count,
        sum(case when order_status = 'Cancelled' then 1 else 0 end)   as cancelled_count,
        sum(case when has_standard_cost then 1 else 0 end)            as orders_with_known_cost

    from enriched
    group by product_id, region, revenue_month

)

select * from aggregated
```

- [ ] **Step 4: Build and verify**

```bash
uv run dbt build --project-dir transform --profiles-dir transform
```

Expected: `Completed successfully`.

- [ ] **Step 5: Verify recognised revenue is less than total revenue**

```bash
duckdb warehouse.duckdb -c "
select
  (select sum(actual_revenue) from staging.int_order_revenue_monthly) as recognised,
  (select sum(gross_revenue) from staging.int_orders_enriched) as all_statuses"
```

Expected: `recognised` is strictly less than `all_statuses`. If they are equal, R1 is not being applied and cancelled or open orders are leaking into revenue.

- [ ] **Step 6: Commit**

```bash
git add transform/models/intermediate/
git commit -m "feat: aggregate recognised revenue to forecast grain"
```

---

### Task 8: Dimensions

**Files:**
- Create: `transform/models/marts/dim_date.sql`, `dim_region.sql`, `dim_customer.sql`, `dim_product.sql`, `transform/models/marts/_marts__models.yml`

**Interfaces:**
- Consumes: staging models from Tasks 4–5, seed `seed_region` from Task 3
- Produces: `dim_date` (`date_month, year_number, month_number, month_name, quarter_number`, one row per month Jan–Dec 2025); `dim_region` (`region_name, is_forecastable`); `dim_customer` (`customer_id, account_id, account_name, region, segment, account_owner, is_active`, including a synthetic `CUST999` row); `dim_product` (`product_id, product_name, product_family, lifecycle_status, standard_cost, is_active_product`, including a synthetic `PROD999` row)

The synthetic rows matter: they let `fct_orders` keep referential integrity to the dimensions without discarding the orphaned orders (D2, D3), which is the standard warehouse treatment for a late-arriving dimension member.

- [ ] **Step 1: Write `dim_date.sql`**

```sql
-- Monthly date spine covering the full calendar year, not just the months with
-- data. A month where nothing shipped must still appear in the report as a
-- zero, otherwise a total collapse looks identical to a missing extract.

with months as (

    {{ dbt_utils.date_spine(
        datepart="month",
        start_date="cast('2025-01-01' as date)",
        end_date="cast('2026-01-01' as date)"
    ) }}

)

select
    cast(date_month as date)             as date_month,
    extract(year from date_month)        as year_number,
    extract(month from date_month)       as month_number,
    extract(quarter from date_month)     as quarter_number,
    cast(extract(year from date_month) as varchar)
        || '-' || lpad(cast(extract(month from date_month) as varchar), 2, '0') as year_month_label

from months
```

- [ ] **Step 2: Write `dim_region.sql`**

```sql
-- UNMAPPED is a member of this dimension, not an absence. is_forecastable
-- records why it can never match a forecast row (D2).

select
    region_name,
    cast(is_forecastable as boolean) as is_forecastable

from {{ ref('seed_region') }}
```

- [ ] **Step 3: Write `dim_customer.sql`**

```sql
-- Type 1: current state only. The sources carry no change history, so a
-- snapshot over them would capture nothing. SCD2 is designed for in the spec
-- and becomes worth building at the first load with a changed account.
--
-- The synthetic row is the standard treatment for a late-arriving dimension
-- member: it preserves referential integrity from the fact table without
-- discarding the orphaned order's revenue (D2).

with accounts as (

    select * from {{ ref('stg_salesforce__accounts') }}

),

orphaned_customers as (

    select distinct customer_id
    from {{ ref('int_orders_enriched') }}
    where region = 'UNMAPPED'

)

select
    customer_id,
    account_id,
    account_name,
    region,
    segment,
    account_owner,
    is_active,
    false as is_synthetic
from accounts

union all

select
    customer_id,
    null            as account_id,
    'Unknown Customer (' || customer_id || ')' as account_name,
    'UNMAPPED'      as region,
    'Unknown'       as segment,
    null            as account_owner,
    false           as is_active,
    true            as is_synthetic
from orphaned_customers
```

- [ ] **Step 4: Write `dim_product.sql`**

```sql
-- Same late-arriving-member treatment as dim_customer. standard_cost stays
-- NULL for the synthetic row so margin remains honestly unknown (D3) rather
-- than silently zero.

with products as (

    select * from {{ ref('stg_erp__products') }}

),

orphaned_products as (

    select distinct product_id
    from {{ ref('int_orders_enriched') }}
    where has_standard_cost = false

)

select
    product_id,
    product_name,
    product_family,
    lifecycle_status,
    standard_cost,
    is_active_product,
    false as is_synthetic
from products

union all

select
    product_id,
    'Unknown Product (' || product_id || ')' as product_name,
    'Unknown'  as product_family,
    'Unknown'  as lifecycle_status,
    null       as standard_cost,
    false      as is_active_product,
    true       as is_synthetic
from orphaned_products
```

- [ ] **Step 5: Write the schema tests**

Create `transform/models/marts/_marts__models.yml`:

```yaml
version: 2

models:
  - name: dim_date
    description: Monthly spine for calendar 2025. Empty months appear as zeros, not gaps.
    columns:
      - name: date_month
        data_tests: [unique, not_null]

  - name: dim_region
    columns:
      - name: region_name
        data_tests: [unique, not_null]

  - name: dim_customer
    description: >
      Type 1. Includes synthetic rows for customers present in orders but
      absent from Salesforce, preserving referential integrity without
      discarding revenue.
    columns:
      - name: customer_id
        data_tests: [unique, not_null]
      - name: region
        data_tests:
          - relationships:
              to: ref('dim_region')
              field: region_name

  - name: dim_product
    description: Type 1, with synthetic rows for products absent from the master.
    columns:
      - name: product_id
        data_tests: [unique, not_null]
```

- [ ] **Step 6: Build and verify**

```bash
uv run dbt build --project-dir transform --profiles-dir transform
```

Expected: `Completed successfully`.

- [ ] **Step 7: Verify the synthetic members exist**

```bash
duckdb warehouse.duckdb -c "
select 'customer' as dim, customer_id as id from analytics.dim_customer where is_synthetic
union all
select 'product', product_id from analytics.dim_product where is_synthetic"
```

Expected: exactly two rows — `customer | CUST999` and `product | PROD999`.

- [ ] **Step 8: Commit**

```bash
git add transform/models/marts/
git commit -m "feat: conformed dimensions with late-arriving member handling"
```

---

### Task 9: Fact tables

**Files:**
- Create: `transform/models/marts/fct_orders.sql`, `transform/models/marts/fct_forecast.sql`
- Modify: `transform/models/marts/_marts__models.yml`

**Interfaces:**
- Consumes: `int_orders_enriched` (Task 6), `stg_adaptive__forecast` (Task 5)
- Produces: `fct_orders` at order grain with `order_id, customer_id, product_id, region, revenue_month, order_date, promised_delivery_date, actual_delivery_date, quantity, unit_price, gross_revenue, gross_margin, has_standard_cost, order_status, delivery_status, is_recognised_revenue, plant_city, plant_country, source_batch`. `fct_forecast` at `(product_id, region, forecast_month)` with `forecast_id, forecast_revenue, forecast_quantity`.

- [ ] **Step 1: Write `fct_orders.sql`**

```sql
-- Order-line grain. This is the half of the grain conflict that the monthly
-- mart cannot represent: per-order delivery status, promise dates, and plant.
-- Analysts drill from the mart to here.

select
    order_id,
    customer_id,
    product_id,
    region,
    revenue_month,
    order_date,
    promised_delivery_date,
    actual_delivery_date,
    quantity,
    unit_price,
    gross_revenue,
    gross_margin,
    has_standard_cost,
    order_status,
    delivery_status,
    is_recognised_revenue,
    plant_city,
    plant_country,
    source_batch

from {{ ref('int_orders_enriched') }}
```

- [ ] **Step 2: Write `fct_forecast.sql`**

```sql
-- Plan grain: product x region x month. Deliberately not disaggregated to
-- order level -- the source carries no basis for allocating a monthly regional
-- number across individual orders, and inventing one would manufacture
-- precision the business does not have.

select
    forecast_id,
    product_id,
    region,
    forecast_month,
    forecast_revenue,
    forecast_quantity

from {{ ref('stg_adaptive__forecast') }}
```

- [ ] **Step 3: Write the schema tests**

Append to `transform/models/marts/_marts__models.yml`:

```yaml
  - name: fct_orders
    description: >
      Order-line grain, carrying the per-order delivery detail that cannot be
      represented at the monthly mart grain.
    columns:
      - name: order_id
        data_tests: [unique, not_null]
      - name: customer_id
        data_tests:
          - relationships:
              to: ref('dim_customer')
              field: customer_id
      - name: product_id
        data_tests:
          - relationships:
              to: ref('dim_product')
              field: product_id
      - name: region
        data_tests:
          - relationships:
              to: ref('dim_region')
              field: region_name
      - name: delivery_status
        data_tests:
          - accepted_values:
              values: ["On Time", "Late", "Not Delivered", "Cancelled", "Unknown"]

  - name: fct_forecast
    description: Plan grain, product x region x month.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns: [product_id, region, forecast_month]
    columns:
      - name: forecast_id
        data_tests: [unique, not_null]
      - name: forecast_revenue
        data_tests: [not_null]
```

- [ ] **Step 4: Build and verify**

```bash
uv run dbt build --project-dir transform --profiles-dir transform
```

Expected: `Completed successfully`. The `relationships` tests on `fct_orders` are the proof that the synthetic dimension rows from Task 8 do their job — without them, `CUST999` and `PROD999` would fail referential integrity.

- [ ] **Step 5: Commit**

```bash
git add transform/models/marts/
git commit -m "feat: order and forecast fact tables"
```

---

### Task 10: The revenue performance mart

**Files:**
- Create: `transform/models/marts/mart_revenue_performance.sql`
- Modify: `transform/models/marts/_marts__models.yml`
- Test: `transform/tests/assert_forecast_without_actuals_survives.sql`

**Interfaces:**
- Consumes: `int_order_revenue_monthly` (Task 7), `fct_forecast` (Task 9)
- Produces: `mart_revenue_performance` at `(product_id, region, revenue_month)` with `actual_revenue, forecast_revenue, revenue_variance, revenue_variance_pct, actual_margin, margin_coverage_pct, on_time_delivery_rate, order_count, on_time_count, late_count, cancelled_count, variance_flag`

- [ ] **Step 1: Write the failing test**

Create `transform/tests/assert_forecast_without_actuals_survives.sql`:

```sql
-- The full outer join is load-bearing. An inner join would drop forecast rows
-- that received no orders -- which is precisely the variance a revenue
-- performance report exists to surface -- and would drop UNMAPPED actuals,
-- breaking the reconciliation guarantee. This test fails if either side of the
-- join is ever silently lost.

select 'forecast rows without actuals were dropped' as failure
from (select 1 as probe) as p
where (
    select count(*) from {{ ref('fct_forecast') }}
) > (
    select count(*) from {{ ref('mart_revenue_performance') }} where forecast_revenue is not null
)

union all

select 'UNMAPPED actuals were dropped by the join' as failure
from (select 1 as probe) as p
where not exists (
    select 1 from {{ ref('mart_revenue_performance') }} where region = 'UNMAPPED'
)
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run dbt build --project-dir transform --profiles-dir transform
```

Expected: FAIL — `depends on a node named 'mart_revenue_performance' which was not found`.

- [ ] **Step 3: Write the mart**

```sql
-- The deliverable. Actual against plan at the coarsest grain both inputs share.
--
-- FULL OUTER is the whole design. Forecast rows with no orders are the report's
-- primary signal, and actuals with no forecast (UNMAPPED customers, unforecast
-- products) must stay visible rather than vanish into a join. Both sides
-- survive; the coalesced key columns below are what make that work.

with actuals as (

    select * from {{ ref('int_order_revenue_monthly') }}

),

forecast as (

    select * from {{ ref('fct_forecast') }}

),

joined as (

    select
        coalesce(actuals.product_id, forecast.product_id)       as product_id,
        coalesce(actuals.region, forecast.region)               as region,
        coalesce(actuals.revenue_month, forecast.forecast_month) as revenue_month,

        coalesce(actuals.actual_revenue, 0)  as actual_revenue,
        coalesce(actuals.actual_quantity, 0) as actual_quantity,
        coalesce(actuals.actual_margin, 0)   as actual_margin,

        forecast.forecast_revenue,
        forecast.forecast_quantity,

        coalesce(actuals.order_count, 0)             as order_count,
        coalesce(actuals.on_time_count, 0)           as on_time_count,
        coalesce(actuals.late_count, 0)              as late_count,
        coalesce(actuals.cancelled_count, 0)         as cancelled_count,
        coalesce(actuals.orders_with_known_cost, 0)  as orders_with_known_cost

    from actuals
    full outer join forecast
        on  actuals.product_id    = forecast.product_id
        and actuals.region        = forecast.region
        and actuals.revenue_month = forecast.forecast_month

),

measured as (

    select
        product_id,
        region,
        revenue_month,

        actual_revenue,
        actual_quantity,
        forecast_revenue,
        forecast_quantity,

        cast(actual_revenue - coalesce(forecast_revenue, 0) as decimal(18, 2)) as revenue_variance,

        -- NULLIF guards the zero-forecast case, which is common: a product sold
        -- into a region nobody planned for divides by zero without it.
        cast(
            (actual_revenue - coalesce(forecast_revenue, 0))
            / nullif(forecast_revenue, 0)
            as decimal(18, 4)
        ) as revenue_variance_pct,

        actual_margin,

        -- A margin total built from partly-unknown costs (D3) must never be
        -- read as complete. This column is what stops that.
        case
            when order_count > 0
                then cast(orders_with_known_cost * 1.0 / order_count as decimal(18, 4))
        end as margin_coverage_pct,

        order_count,
        on_time_count,
        late_count,
        cancelled_count,

        case
            when (on_time_count + late_count) > 0
                then cast(on_time_count * 1.0 / (on_time_count + late_count) as decimal(18, 4))
        end as on_time_delivery_rate,

        case
            when forecast_revenue is null then 'NO_FORECAST'
            when order_count = 0          then 'NO_ACTUALS'
            when actual_revenue >= forecast_revenue then 'AT_OR_ABOVE_PLAN'
            else 'BELOW_PLAN'
        end as variance_flag

    from joined

)

select * from measured
```

- [ ] **Step 4: Write the schema tests**

Append to `transform/models/marts/_marts__models.yml`:

```yaml
  - name: mart_revenue_performance
    description: >
      Actual against plan at product x region x month, the coarsest grain the
      two inputs share. Built with a full outer join so unplanned revenue and
      unfulfilled plan both stay visible. Per-order delivery detail lives in
      fct_orders; see the spec for why one table cannot hold both grains.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns: [product_id, region, revenue_month]
    columns:
      - name: product_id
        data_tests:
          - not_null
          - relationships:
              to: ref('dim_product')
              field: product_id
      - name: region
        data_tests:
          - not_null
          - relationships:
              to: ref('dim_region')
              field: region_name
      - name: revenue_month
        data_tests: [not_null]
      - name: actual_revenue
        data_tests: [not_null]
      - name: revenue_variance
        data_tests: [not_null]
      - name: variance_flag
        data_tests:
          - accepted_values:
              values: ["NO_FORECAST", "NO_ACTUALS", "AT_OR_ABOVE_PLAN", "BELOW_PLAN"]
```

- [ ] **Step 5: Build and verify the tests pass**

```bash
uv run dbt build --project-dir transform --profiles-dir transform
```

Expected: `Completed successfully`.

- [ ] **Step 6: Inspect the variance distribution**

```bash
duckdb warehouse.duckdb -c "
select variance_flag, count(*) as rows, round(sum(actual_revenue), 2) as actual, round(sum(forecast_revenue), 2) as forecast
from analytics.mart_revenue_performance group by 1 order by 1"
```

Expected: all four flags present. `NO_FORECAST` rows carry actual revenue with a NULL forecast — that is the `UNMAPPED` customer plus any unforecast product, exactly as designed.

- [ ] **Step 7: Commit**

```bash
git add transform/models/marts/ transform/tests/
git commit -m "feat: revenue performance mart with full outer forecast join"
```

---

### Task 11: DQ summary and source reconciliation

The reconciliation test is the strongest single guarantee in the project: every source dollar is accounted for in exactly one bucket.

**Files:**
- Create: `transform/models/marts/dq_reject_summary.sql`
- Test: `transform/tests/assert_revenue_reconciles_to_source.sql`
- Modify: `transform/models/marts/_marts__models.yml`

**Interfaces:**
- Consumes: `stg_oracle__orders_rejects` (Task 4), `fct_orders` (Task 9), `mart_revenue_performance` (Task 10), `source('oracle', ...)` (Task 3)
- Produces: `dq_reject_summary` with `dq_failure_reason, rejected_row_count, rejected_revenue, pct_of_source_rows`

- [ ] **Step 1: Write the failing reconciliation test**

Create `transform/tests/assert_revenue_reconciles_to_source.sql`:

```sql
-- Every computable source dollar lands in exactly one bucket:
--
--   source total = recognised (in the mart)
--                + open       (pipeline, R1)
--                + cancelled  (excluded, R1)
--                + rejected   (quarantined, D1/D4/D5/D6)
--
-- Both sides apply the identical filter -- quantity and unit_price both
-- non-null -- so a row whose revenue cannot be computed is absent from both
-- and cannot mask a leak. If this test fails, revenue is disappearing
-- somewhere in the pipeline and no number in the mart can be trusted.

with source_total as (

    select coalesce(sum(
        try_cast(nullif(trim(quantity), '') as decimal(18, 2))
        * try_cast(nullif(trim(unit_price), '') as decimal(18, 2))
    ), 0) as amount
    from {{ source('oracle', 'raw_oracle_orders') }}
    where try_cast(nullif(trim(quantity), '') as decimal(18, 2)) is not null
      and try_cast(nullif(trim(unit_price), '') as decimal(18, 2)) is not null

),

recognised as (

    select coalesce(sum(actual_revenue), 0) as amount
    from {{ ref('mart_revenue_performance') }}

),

open_and_cancelled as (

    select coalesce(sum(gross_revenue), 0) as amount
    from {{ ref('fct_orders') }}
    where not is_recognised_revenue

),

rejected as (

    select coalesce(sum(gross_revenue), 0) as amount
    from {{ ref('stg_oracle__orders_rejects') }}
    where quantity is not null and unit_price is not null

),

reconciliation as (

    select
        (select amount from source_total)  as source_amount,
        (select amount from recognised)
            + (select amount from open_and_cancelled)
            + (select amount from rejected) as accounted_amount

)

select
    source_amount,
    accounted_amount,
    source_amount - accounted_amount as unexplained_difference
from reconciliation
-- One cent of tolerance for decimal rounding across the aggregation steps.
where abs(source_amount - accounted_amount) > 0.01
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run dbt test --project-dir transform --profiles-dir transform --select assert_revenue_reconciles_to_source
```

Expected: FAIL — `depends on a node named 'dq_reject_summary'` is not the error here; this test should actually compile and **pass** already, since Tasks 4–10 built the pipeline correctly. If it FAILS with a non-zero `unexplained_difference`, revenue is leaking — stop and find where before continuing.

- [ ] **Step 3: Write `dq_reject_summary.sql`**

```sql
-- What was excluded, why, and what it cost. Publishing the price of every
-- exclusion is what keeps quarantining honest: a silent rejects table is
-- indistinguishable from deleting the rows.

with rejects as (

    select * from {{ ref('stg_oracle__orders_rejects') }}

),

source_row_count as (

    select count(*) as total_rows from {{ source('oracle', 'raw_oracle_orders') }}

)

select
    rejects.dq_failure_reason,
    count(*) as rejected_row_count,
    cast(sum(rejects.gross_revenue) as decimal(18, 2)) as rejected_revenue,
    cast(count(*) * 1.0 / max(source_row_count.total_rows) as decimal(18, 4)) as pct_of_source_rows

from rejects
cross join source_row_count
group by rejects.dq_failure_reason
```

- [ ] **Step 4: Add the schema test**

Append to `transform/models/marts/_marts__models.yml`:

```yaml
  - name: dq_reject_summary
    description: >
      Row count and dollar value of every exclusion, by reason. Makes the cost
      of quarantining visible rather than implicit.
    columns:
      - name: dq_failure_reason
        data_tests: [unique, not_null]
      - name: rejected_row_count
        data_tests: [not_null]
```

- [ ] **Step 5: Build the full project and verify everything passes**

```bash
uv run dbt build --project-dir transform --profiles-dir transform
```

Expected: `Completed successfully` with zero errors and zero warnings.

- [ ] **Step 6: Read the reject summary**

```bash
duckdb warehouse.duckdb -c "select * from analytics.dq_reject_summary order by rejected_revenue desc"
```

Expected: four rows, one per reason, with a non-null `rejected_revenue` on `AMBIGUOUS_DUPLICATE` and `MISSING_ORDER_DATE`, and NULL on `MISSING_UNIT_PRICE` (its revenue is not computable — which is exactly why the row was rejected).

- [ ] **Step 7: Commit**

```bash
git add transform/models/marts/ transform/tests/
git commit -m "feat: DQ reject summary and source revenue reconciliation test"
```

---

### Task 12: PySpark twin and parity test

**Files:**
- Create: `spark/int_order_revenue_monthly_spark.py`
- Test: `tests/test_spark_parity.py`

**Interfaces:**
- Consumes: `staging.int_orders_enriched` from the DuckDB warehouse (Task 6)
- Produces: `aggregate_monthly_revenue(orders_df: DataFrame) -> DataFrame` with the identical schema and values as `int_order_revenue_monthly` from Task 7. Also `main()`, runnable as `uv run python spark/int_order_revenue_monthly_spark.py`.

- [ ] **Step 1: Confirm Java is available**

```bash
java --version
```

Expected: `openjdk 17.x`. If it reports `Unable to locate a Java Runtime`, Task 1 Step 1 did not complete — run `brew install --cask temurin@17` and retry.

- [ ] **Step 2: Write the failing parity test**

Create `tests/test_spark_parity.py`:

```python
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
```

- [ ] **Step 3: Run to verify it fails**

```bash
uv run pytest tests/test_spark_parity.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'spark'`.

- [ ] **Step 4: Write the PySpark implementation**

Create `spark/__init__.py` (empty), then `spark/int_order_revenue_monthly_spark.py`:

```python
"""The monthly revenue aggregation, in PySpark.

The same transform as models/intermediate/int_order_revenue_monthly.sql. Kept
in step by tests/test_spark_parity.py, which asserts the two produce identical
output. Written against the DataFrame API rather than spark.sql() so the
comparison is between two genuinely different implementations, not the same
SQL string run twice.
"""

from __future__ import annotations

import duckdb
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

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
```

- [ ] **Step 5: Run the parity test**

```bash
uv run pytest tests/test_spark_parity.py -v
```

Expected: 2 passed. Spark prints warnings about native Hadoop libraries on macOS — harmless.

If `assert_frame_equal` reports a numeric mismatch, the two implementations genuinely disagree; the diff output names the column and the grain key. Fix whichever is wrong rather than loosening the tolerance.

- [ ] **Step 6: Run the Spark job standalone**

```bash
uv run python spark/int_order_revenue_monthly_spark.py
```

Expected: a printed table and a row count matching `select count(*) from staging.int_order_revenue_monthly`.

- [ ] **Step 7: Commit**

```bash
git add spark/ tests/test_spark_parity.py
git commit -m "feat: PySpark twin of the monthly aggregation with parity test"
```

---

### Task 13: Streamlit dashboard

**Files:**
- Create: `app/streamlit_app.py`, `.streamlit/config.toml`

**Interfaces:**
- Consumes: `analytics.mart_revenue_performance`, `analytics.fct_orders`, `analytics.dq_reject_summary` from `warehouse.duckdb`
- Produces: a Streamlit app served by `make app`, deployable to Streamlit Community Cloud with no warehouse credentials

- [ ] **Step 1: Create `.streamlit/config.toml`**

```toml
[server]
headless = true

[browser]
gatherUsageStats = false
```

- [ ] **Step 2: Write the app**

Create `app/streamlit_app.py`:

```python
"""Revenue performance dashboard.

Reads the committed DuckDB file directly, so the deployed app needs no
warehouse credentials and keeps working after the Snowflake trial expires.
Read-only connection: the dashboard is a consumer of the mart, never a writer.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "warehouse.duckdb"

st.set_page_config(page_title="Revenue Performance", page_icon="📊", layout="wide")


@st.cache_data
def query(sql: str) -> pd.DataFrame:
    connection = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return connection.execute(sql).fetch_df()
    finally:
        connection.close()


def render_header() -> None:
    st.title("Revenue Performance")
    st.caption(
        "Actual against plan at product x region x month. Per-order delivery "
        "detail lives in fct_orders — the two grains are deliberately separate."
    )


def render_kpis(mart: pd.DataFrame) -> None:
    actual = mart["actual_revenue"].sum()
    forecast = mart["forecast_revenue"].sum()
    variance = actual - forecast
    on_time = mart["on_time_count"].sum()
    delivered = on_time + mart["late_count"].sum()

    columns = st.columns(4)
    columns[0].metric("Actual revenue", f"${actual:,.0f}")
    columns[1].metric("Forecast revenue", f"${forecast:,.0f}")
    columns[2].metric(
        "Variance",
        f"${variance:,.0f}",
        delta=f"{variance / forecast:.1%}" if forecast else None,
    )
    columns[3].metric(
        "On-time delivery",
        f"{on_time / delivered:.1%}" if delivered else "n/a",
    )


def render_variance_chart(mart: pd.DataFrame) -> None:
    st.subheader("Actual against plan by month")
    monthly = (
        mart.groupby("revenue_month", as_index=False)[["actual_revenue", "forecast_revenue"]]
        .sum()
        .melt(
            id_vars="revenue_month",
            var_name="measure",
            value_name="revenue",
        )
    )
    figure = px.bar(
        monthly,
        x="revenue_month",
        y="revenue",
        color="measure",
        barmode="group",
        labels={"revenue_month": "Month", "revenue": "Revenue"},
    )
    st.plotly_chart(figure, use_container_width=True)


def render_region_breakdown(mart: pd.DataFrame) -> None:
    st.subheader("Variance by region")
    st.caption(
        "UNMAPPED is revenue from a customer absent from Salesforce (defect D2). "
        "It has no region, so no forecast can explain it — retained and visible "
        "rather than dropped."
    )
    by_region = (
        mart.groupby("region", as_index=False)
        .agg(
            actual_revenue=("actual_revenue", "sum"),
            forecast_revenue=("forecast_revenue", "sum"),
            revenue_variance=("revenue_variance", "sum"),
        )
        .sort_values("revenue_variance")
    )
    st.dataframe(by_region, use_container_width=True, hide_index=True)


def render_data_quality() -> None:
    st.subheader("Data quality")
    st.caption(
        "Rows excluded from analytics, retained with the reason. Nothing is "
        "deleted — a dbt test reconciles every source dollar against these buckets."
    )
    st.dataframe(
        query("select * from analytics.dq_reject_summary order by rejected_row_count desc"),
        use_container_width=True,
        hide_index=True,
    )


def main() -> None:
    if not DB_PATH.exists():
        st.error("warehouse.duckdb not found. Run `make ingest && make build` first.")
        return

    render_header()
    mart = query("select * from analytics.mart_revenue_performance")

    regions = st.sidebar.multiselect(
        "Region", sorted(mart["region"].unique()), default=list(mart["region"].unique())
    )
    filtered = mart[mart["region"].isin(regions)] if regions else mart

    render_kpis(filtered)
    render_variance_chart(filtered)
    render_region_breakdown(filtered)
    render_data_quality()


main()
```

- [ ] **Step 3: Run the app**

```bash
make app
```

Expected: `You can now view your Streamlit app in your browser` at `http://localhost:8501`. Open it and confirm four KPI tiles, a grouped bar chart, a region table containing an `UNMAPPED` row, and a data quality table with four rejection reasons.

- [ ] **Step 4: Stop the app and commit the warehouse file**

Press Ctrl+C, then:

```bash
ls -lh warehouse.duckdb
git add -f warehouse.duckdb app/ .streamlit/
git commit -m "feat: streamlit revenue performance dashboard"
```

`-f` is required because `.gitignore` has no entry for it, but be explicit: the warehouse file is committed deliberately so a reviewer — and Streamlit Community Cloud — can run the app with no setup. Confirm it is under 5 MB.

- [ ] **Step 5: Deploy to Streamlit Community Cloud**

Push to GitHub, then at `share.streamlit.io` connect the repository with main file path `app/streamlit_app.py`. Deployment gives a public URL requiring no credentials.

---

### Task 14: Snowflake target

**Files:**
- Create: `snowflake/01_bootstrap.sql`, `snowflake/README.md`

**Interfaces:**
- Consumes: the dbt project from Tasks 3–11
- Produces: a `REVENUE_ANALYTICS` database with `RAW`, `STAGING`, `ANALYTICS` schemas; roles `LOADER`, `TRANSFORMER`, `REPORTER`; warehouses `WH_LOAD_XS` and `WH_TRANSFORM_XS`

- [ ] **Step 1: Write `snowflake/01_bootstrap.sql`**

```sql
-- Run once as ACCOUNTADMIN on a fresh Snowflake trial.
--
-- Three roles matching the three things that touch the warehouse: the loader
-- writes RAW and nothing else, the transformer reads RAW and owns everything
-- downstream, the reporter reads ANALYTICS only. Separate warehouses so a
-- heavy transform cannot starve a dashboard query, and so the two workloads
-- bill separately.

use role accountadmin;

create database if not exists revenue_analytics;

create schema if not exists revenue_analytics.raw;
create schema if not exists revenue_analytics.staging;
create schema if not exists revenue_analytics.analytics;

-- Auto-suspend at the 60-second floor: on a trial, an idle warehouse is the
-- single largest source of wasted credits.
create warehouse if not exists wh_load_xs
    warehouse_size = xsmall
    auto_suspend = 60
    auto_resume = true
    initially_suspended = true;

create warehouse if not exists wh_transform_xs
    warehouse_size = xsmall
    auto_suspend = 60
    auto_resume = true
    initially_suspended = true;

create role if not exists loader;
create role if not exists transformer;
create role if not exists reporter;

grant usage on warehouse wh_load_xs to role loader;
grant usage on warehouse wh_transform_xs to role transformer;
grant usage on warehouse wh_transform_xs to role reporter;

grant usage on database revenue_analytics to role loader;
grant usage on database revenue_analytics to role transformer;
grant usage on database revenue_analytics to role reporter;

-- LOADER: writes RAW, and can read nothing else.
grant usage, create table on schema revenue_analytics.raw to role loader;

-- TRANSFORMER: reads RAW, owns STAGING and ANALYTICS.
grant usage on schema revenue_analytics.raw to role transformer;
grant select on all tables in schema revenue_analytics.raw to role transformer;
grant select on future tables in schema revenue_analytics.raw to role transformer;
grant all on schema revenue_analytics.staging to role transformer;
grant all on schema revenue_analytics.analytics to role transformer;

-- REPORTER: reads the published marts, and cannot see staging or raw.
grant usage on schema revenue_analytics.analytics to role reporter;
grant select on all tables in schema revenue_analytics.analytics to role reporter;
grant select on future tables in schema revenue_analytics.analytics to role reporter;

-- Replace <YOUR_USER> with the trial account's username.
grant role loader to user <YOUR_USER>;
grant role transformer to user <YOUR_USER>;
grant role reporter to user <YOUR_USER>;
```

- [ ] **Step 2: Write `snowflake/README.md`**

````markdown
# Running against Snowflake

DuckDB is the default target so the project runs with no account. Snowflake is
the production target; the model code is identical.

## 1. Bootstrap

Sign up for a Snowflake trial (30 days, $400 in credits, no card required).
In a worksheet as `ACCOUNTADMIN`, replace `<YOUR_USER>` in `01_bootstrap.sql`
with your username and run the whole script.

## 2. Set credentials

`profiles.yml` reads these from the environment, so nothing is ever committed:

```bash
export SNOWFLAKE_ACCOUNT=abc12345.us-east-1
export SNOWFLAKE_USER=your_user
export SNOWFLAKE_PASSWORD=your_password
export SNOWFLAKE_ROLE=TRANSFORMER
export SNOWFLAKE_WAREHOUSE=WH_TRANSFORM_XS
export SNOWFLAKE_DATABASE=REVENUE_ANALYTICS
```

## 3. Load and build

```bash
uv run python ingest/load_raw.py --target snowflake
uv run dbt build --project-dir transform --profiles-dir transform --target snowflake
```

## Cost note

Both warehouses are XSMALL with a 60-second auto-suspend. A full build over
450 rows costs a small fraction of one credit. Idle warehouses, not query
volume, are what drain a trial.
````

- [ ] **Step 3: Verify the SQL parses**

Snowflake is not required for this check — confirm the file is syntactically well-formed and contains no placeholder beyond the documented `<YOUR_USER>`:

```bash
grep -c 'grant' snowflake/01_bootstrap.sql && grep -n '<YOUR_USER>' snowflake/01_bootstrap.sql
```

Expected: a grant count of 18 or more, and `<YOUR_USER>` appearing only in the three `grant role` lines at the end.

- [ ] **Step 4: Commit**

```bash
git add snowflake/
git commit -m "feat: snowflake bootstrap with layered schemas and RBAC"
```

---

### Task 15: Documentation and CI

**Files:**
- Create: `README.md`, `docs/POWER_BI.md`, `.github/workflows/ci.yml`
- Delete: `README.txt` (the original dataset note, superseded)

**Interfaces:**
- Consumes: everything from Tasks 1–14
- Produces: the reviewer-facing entry point and a green CI badge

- [ ] **Step 1: Write `docs/POWER_BI.md`**

````markdown
# Power BI Connection Guide

Power BI Desktop does not run on macOS, so this project ships a Streamlit
dashboard instead. The mart is nonetheless shaped as a conformed star
specifically so connecting Power BI is a configuration exercise rather than a
rewrite. This document is what that configuration looks like.

## Model layout

Import these five tables and set the relationships as follows:

| From | To | Cardinality | Direction |
|---|---|---|---|
| `mart_revenue_performance[product_id]` | `dim_product[product_id]` | many-to-one | single |
| `mart_revenue_performance[region]` | `dim_region[region_name]` | many-to-one | single |
| `mart_revenue_performance[revenue_month]` | `dim_date[date_month]` | many-to-one | single |
| `fct_orders[customer_id]` | `dim_customer[customer_id]` | many-to-one | single |

Mark `dim_date` as the date table on `date_month`. Leave every relationship
single-direction — bidirectional filtering across two fact tables at different
grains produces ambiguous paths and silently wrong totals.

`fct_orders` and `mart_revenue_performance` sit at different grains by design.
They share `dim_product`, `dim_region`, and `dim_date`, so a slicer on any of
those filters both correctly. Never join them to each other directly.

## Measures

```dax
Actual Revenue   = SUM ( mart_revenue_performance[actual_revenue] )
Forecast Revenue = SUM ( mart_revenue_performance[forecast_revenue] )
Revenue Variance = [Actual Revenue] - [Forecast Revenue]

Variance %  =
DIVIDE ( [Revenue Variance], [Forecast Revenue] )   -- DIVIDE, not "/", returns
                                                     -- BLANK on a zero forecast

Gross Margin = SUM ( mart_revenue_performance[actual_margin] )

-- Margin is unknown for products absent from the product master (defect D3).
-- Surface the coverage next to the total so a partial figure is never read as
-- complete.
Margin Coverage % =
DIVIDE (
    SUM ( mart_revenue_performance[orders_with_known_cost] ),
    SUM ( mart_revenue_performance[order_count] )
)

On Time Delivery % =
DIVIDE (
    SUM ( mart_revenue_performance[on_time_count] ),
    SUM ( mart_revenue_performance[on_time_count] )
        + SUM ( mart_revenue_performance[late_count] )
)
```

## Import against DirectQuery

Use **Import**. The mart is a few thousand rows at monthly grain; Import gives
better performance, full DAX support, and no load on the warehouse. DirectQuery
would leave an XSMALL warehouse resuming on every visual interaction, which
costs credits and adds latency for no benefit at this size.

Reconsider DirectQuery only above roughly 100 million rows, or when
sub-minute freshness is a stated requirement. At that point, configure
incremental refresh partitioned on `revenue_month` with a rolling window,
rather than switching the whole model.

## Row-level security

If regional managers should see only their own region, add an RLS role on
`dim_region`:

```dax
[region_name] = LOOKUPVALUE (
    user_region_mapping[region],
    user_region_mapping[email],
    USERPRINCIPALNAME ()
)
```

This requires a user-to-region mapping table, which the assessment dataset does
not include. In production it would be sourced from the same Salesforce extract
that supplies `account_owner`.
````

- [ ] **Step 2: Write `README.md`**

````markdown
# Revenue Performance Mart

A Snowflake data pipeline over four source systems — Oracle Fusion orders,
Salesforce accounts, Adaptive Planning forecast, and an ERP product master —
producing `analytics.mart_revenue_performance`.

Built for the Sr. Data Engineer technical assessment. The brief said a
production-ready build was not required. This one runs.

## Quick start

```bash
make install
make all
make app
```

Roughly two minutes from clone to a dashboard. No Snowflake account needed —
DuckDB is the default target and the model code is identical on both engines.
See `snowflake/README.md` to run it against a real Snowflake trial.

## The central design decision

The brief asks for one table containing forecast revenue and delivery status.
**Those live at different grains.** Forecast is product × region × month.
Delivery status is per order. No single table holds both without either
fabricating forecast precision at order level or discarding delivery detail.

The resolution is two facts and one conformed mart:

- `fct_orders` — order grain, with per-order delivery status
- `fct_forecast` — product × region × month
- `mart_revenue_performance` — monthly, aggregating the first and
  **full-outer-joining** the second

The full outer join is load-bearing. An inner join would drop forecast rows
that received no orders, which is exactly the variance the report exists to
show.

A third option — allocating monthly forecast down to individual orders
pro-rata — was considered and rejected. The source carries no basis for those
allocation weights; inventing them manufactures precision the business does not
have.

## Data quality

Twelve defects were found by profiling. Each has a documented handling decision
with a rationale in
[the design spec](docs/superpowers/specs/2026-09-04-snowflake-revenue-mart-design.md#3-data-quality-defects-and-handling-decisions).

The governing principle: **no row is ever deleted.** Rows that cannot be trusted
are routed to a rejects table with a `dq_failure_reason` and reported in
`dq_reject_summary` with their dollar value. A dbt test then proves the whole
thing balances:

```
source revenue = recognised + open + cancelled + rejected
```

If a single dollar goes missing anywhere in the pipeline, `dbt build` fails.

Two decisions worth calling out:

- **`ORD00005` is duplicated with conflicting quantities (31 and 99)** and the
  source has no timestamp to identify the later record. Both copies are
  rejected. Choosing either one would fabricate a fact; the real fix is
  upstream CDC metadata.
- **`PROD999` has no standard cost**, so its margin is NULL rather than zero.
  Zero would report a 100% margin — a plausible-looking lie. Revenue is
  knowable; margin is not.

## Stack

| Layer | Technology |
|---|---|
| Ingestion | Python, landing all-VARCHAR with load metadata (Fivetran-shaped) |
| Warehouse | Snowflake (primary), DuckDB (local mirror, identical model code) |
| Transformation | dbt-core, layered staging → intermediate → marts |
| Distributed compute | PySpark, with a test asserting parity against the SQL |
| BI | Streamlit (deployed), Power BI ([connection guide](docs/POWER_BI.md)) |
| CI | GitHub Actions running the full build and test suite |

## Layout

```
ingest/        CSV → RAW, no transformation
transform/     dbt project (staging, intermediate, marts)
spark/         PySpark twin of the monthly aggregation
app/           Streamlit dashboard
snowflake/     bootstrap DDL, roles, warehouses
tests/         pytest — ingestion and Spark parity
docs/          design spec, implementation plan, Power BI guide
```

## Deliberately not built

Named so their absence reads as a decision:

| Deferred | Why | Build it when |
|---|---|---|
| SCD2 snapshots | Sources carry no change history | The first load with a changed account |
| Airflow / Dagster | 450 rows, one daily batch; `dbt build` in CI suffices | Multiple schedules or cross-system dependencies |
| Incremental models | Full refresh takes seconds | Roughly 10M+ order rows |
| Streams and Tasks | Batch is adequate for a daily-grain report | Intraday freshness is required |
````

- [ ] **Step 3: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: "17"

      - name: Install dependencies
        run: uv sync --all-groups

      - name: Install dbt packages
        run: uv run dbt deps --project-dir transform --profiles-dir transform

      - name: Land source data
        run: uv run python ingest/load_raw.py

      - name: Build and test the warehouse
        run: uv run dbt build --project-dir transform --profiles-dir transform

      - name: Run the Python test suite
        run: uv run pytest -v
```

- [ ] **Step 4: Remove the superseded dataset note**

```bash
git rm README.txt
```

- [ ] **Step 5: Verify the whole project builds from clean**

```bash
make clean && make install && make all
```

Expected: `Completed successfully` from dbt with every test passing, then pytest reporting all tests passed. This is the exact sequence CI runs and a reviewer will run.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/POWER_BI.md .github/
git commit -m "docs: README, Power BI connection guide, and CI workflow"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: §3 defects D1/D4/D5/D6/D9/D11 → Task 4, D2/D3/D7/D8 → Task 6, D10 → Task 5, D12 → Tasks 7–10. §4 layers → Tasks 2–10. §5 rules R1/R2 → Task 6, R3 → Task 10, R4 → Task 6 macro, R5 → Tasks 3, 6, 8. §6 PySpark → Task 12. §7 testing: schema tests are distributed across Tasks 4–11, defect regression tests in Tasks 4/6/10, reconciliation in Task 11, parity in Task 12. §8 deployment → Tasks 13 (Streamlit), 14 (Snowflake), 15 (Power BI doc). §9 deferrals → Task 15 README table.

**Naming consistency verified across tasks.** `gross_revenue`, `gross_margin`, `has_standard_cost`, `is_recognised_revenue`, `revenue_month`, `delivery_status`, `dq_failure_reason`, `orders_with_known_cost` are spelled identically wherever they appear. The four reject reason strings and five delivery status strings match between the Task 4 model, the Task 4 and Task 6 `accepted_values` tests, and the Task 12 Spark implementation. `int_order_revenue_monthly`'s eleven output columns in Task 7 match the eleven aggregations in Task 12's `aggregate_monthly_revenue`.

**Known risks.** Task 12's `assert_frame_equal` may be sensitive to decimal versus float dtype coercion between DuckDB and Spark; `_normalise` handles this by rounding to two places and passing `check_dtype=False`. `dbt_utils.date_spine` requires `dbt deps` to have run — Task 3 Step 4 does this, and the `make install` target repeats it.
