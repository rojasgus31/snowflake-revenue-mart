# Running against Snowflake

DuckDB is the default target so the project runs with no account. Snowflake is
the production target; the model code is identical.

> **Scoping note:** the Snowflake path below is fully implemented --
> `ingest/load_raw.py --target snowflake`, `01_bootstrap.sql`, and the
> `snowflake` dbt target in `transform/profiles.yml` all exist and are
> code-complete -- but it has **not been executed against a live Snowflake
> account**. This project was built and validated end-to-end on DuckDB (92/92
> dbt build, 7/7 pytest), and no Snowflake trial account was provisioned for
> the assessment. That is a scoping decision, not a gap papered over: the
> model SQL is engine-portable by construction (`try_cast`, `split_part`,
> `date_trunc`, `dbt_utils.date_spine` are all valid, unmodified Snowflake
> syntax), so running it for real is expected to be a credentials-and-`make`
> exercise rather than a rewrite -- but that expectation has not been proven
> against a real account, and you should verify it before trusting this path
> in production.

## 1. Bootstrap

Sign up for a Snowflake trial (30 days, $400 in credits, no card required).
In a worksheet as `ACCOUNTADMIN`, replace `<YOUR_USER>` in `01_bootstrap.sql`
with your username and run the whole script.

## 2. Set credentials

Both `profiles.yml` and the loader read these from the environment, so nothing is
ever committed:

```bash
export SNOWFLAKE_ACCOUNT=ORGNAME-ACCOUNTNAME
export SNOWFLAKE_USER=your_user
export SNOWFLAKE_PASSWORD=your_password
export SNOWFLAKE_DATABASE=REVENUE_ANALYTICS
```

Get `SNOWFLAKE_ACCOUNT` from Snowsight: the account menu at the bottom left,
hover your account, then "Copy account identifier". The legacy
`<locator>.<region>` form also works.

## 3. Load, then build — with DIFFERENT roles

The loader and dbt run as different roles ON PURPOSE, which is the whole point
of the three-role split in `01_bootstrap.sql`. LOADER can create tables in RAW
and can touch nothing else; TRANSFORMER can read RAW and owns everything
downstream. Each role is granted usage on only its own warehouse, so the role
and the warehouse must be changed together.

Load RAW as LOADER:

```bash
export SNOWFLAKE_ROLE=LOADER
export SNOWFLAKE_WAREHOUSE=WH_LOAD_XS
uv run python ingest/load_raw.py --target snowflake
```

Then transform as TRANSFORMER:

```bash
export SNOWFLAKE_ROLE=TRANSFORMER
export SNOWFLAKE_WAREHOUSE=WH_TRANSFORM_XS
uv run dbt build --project-dir transform --profiles-dir transform --target snowflake
```

Running the loader as TRANSFORMER fails: that role has `select` on RAW but not
`create table`, and no usage on `WH_LOAD_XS`.

## 4. Verify

```sql
use role transformer;
select count(*) from revenue_analytics.raw.raw_oracle_orders;   -- 128
select count(*) from revenue_analytics.analytics.mart_revenue_performance;  -- 289
```

`dbt build` should report the same 94 results it does on DuckDB.

## Authentication note

Snowflake has been enforcing MFA on password sign-in for newer accounts, which
breaks password-only programmatic access. If the connection is rejected despite
correct credentials, switch to key-pair authentication: generate an RSA pair,
`alter user <you> set rsa_public_key='...'`, and swap `password` for
`private_key_path` in the `snowflake` output of `transform/profiles.yml`. The
loader reads the same environment variables and would need the equivalent
change in `_snowflake_credentials_from_env`.

## Cost note

Both warehouses are XSMALL with a 60-second auto-suspend. A full build over
450 rows costs a small fraction of one credit. Idle warehouses, not query
volume, are what drain a trial.
