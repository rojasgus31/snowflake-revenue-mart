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
