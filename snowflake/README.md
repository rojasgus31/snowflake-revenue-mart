# Running against Snowflake

DuckDB is the default target so the project runs with no account. Snowflake is
the production target; the model code is identical.

> **Run status:** this path has been executed against a live Snowflake trial
> account, not just written to support one. `dbt build --target snowflake`
> completed with 94 results and 0 errors in about 15 seconds on an XSMALL
> warehouse. The loader wrote all four RAW tables (128 / 20 / 287 / 12 rows).
> Every downstream number matches the DuckDB build exactly:
> `mart_revenue_performance` at 289 rows, `fct_orders` at 122, 6 rejects, 1
> `UNMAPPED` region, and the same reconciliation to the cent
> (`6,168,239.01` recognised + `3,006,773.46` open/cancelled +
> `273,711.64` rejected = `9,448,724.11`). The run log and dbt's own run
> artifact are committed at [`docs/evidence/`](../docs/evidence/); see that
> directory's README for what each file proves. Snowsight screenshots have
> not yet been captured.

## 1. Bootstrap

Sign up for a Snowflake trial (30 days, $400 in credits, no card required).
In a worksheet as `ACCOUNTADMIN`, replace `<YOUR_USER>` in `01_bootstrap.sql`
with your username and run the whole script.

## 2. Set credentials

Both `profiles.yml` and the loader read these from the environment, so nothing is
ever committed:

Password authentication is the default (`SNOWFLAKE_AUTHENTICATOR` unset, or
set to `snowflake`):

```bash
export SNOWFLAKE_ACCOUNT=ORGNAME-ACCOUNTNAME
export SNOWFLAKE_USER=your_user
export SNOWFLAKE_PASSWORD=your_password
export SNOWFLAKE_DATABASE=REVENUE_ANALYTICS
```

Get `SNOWFLAKE_ACCOUNT` from Snowsight: the account menu at the bottom left,
hover your account, then "Copy account identifier". The legacy
`<locator>.<region>` form also works.

For an account with no password — SSO/OAuth-only accounts, which cannot set
`SNOWFLAKE_PASSWORD` at all — use browser-based OAuth sign-in instead:

```bash
export SNOWFLAKE_ACCOUNT=ORGNAME-ACCOUNTNAME
export SNOWFLAKE_USER=your_user
export SNOWFLAKE_DATABASE=REVENUE_ANALYTICS
export SNOWFLAKE_AUTHENTICATOR=OAUTH_AUTHORIZATION_CODE
```

This opens a browser tab for interactive sign-in on first use and caches the
resulting token, so a subsequent scripted run (the loader, then `dbt build`)
does not re-prompt.

Note that `externalbrowser` is a different value, reserved for SAML SSO
accounts specifically. Using it against an account with no SAML Identity
Provider configured fails with a SAML Identity Provider error — a confusing
message if you don't already know your account has no SAML IdP. If you hit
that error, the fix is to use `OAUTH_AUTHORIZATION_CODE` above, not to debug
SAML configuration.

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

## What the live run caught

Local validation on DuckDB — dbt tests, pytest, code review — cannot exercise
an actual Snowflake connection, an actual least-privilege role, or an actual
timezone-aware column type. Running the pipeline for real against a live
account surfaced three defects that none of the above could:

1. **The loader hard-required `SNOWFLAKE_PASSWORD`.** That makes it unusable
   on an SSO/OAuth account that has no password at all. Fixed by making
   password one of two supported credential paths (see the OAuth
   instructions above).
2. **The loader issued `CREATE SCHEMA` on `RAW`.** The `LOADER` role
   correctly has `create table` on the schema but not `create schema` —
   that's the least-privilege split doing its job. The bootstrap script
   already creates the `RAW` schema, so the loader's own attempt was both
   redundant and a permissions failure waiting to happen. Fixed by removing
   it from the loader.
3. **The ingestion timestamp needed `use_logical_type`.** Without it, a
   timezone-aware column landed as the wrong logical type on write. Fixed by
   setting it explicitly.

This is the concrete difference between code-complete and verified: all
three passed code review and every DuckDB test before the live run exposed
them.

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
