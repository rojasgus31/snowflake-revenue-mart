# Streamlit in Snowflake

`snowflake/streamlit_in_snowflake.sql` deploys the same dashboard
(`app/streamlit_app.py`, `app/data_source.py`) natively inside a Snowflake
account, instead of on Streamlit Community Cloud. This document explains what
that buys, what it costs, and why the project keeps both deployments rather
than replacing one with the other.

> This is documentation and a deployment artifact only. None of the SQL in
> `streamlit_in_snowflake.sql` has been run -- the session token for this
> project's Snowflake trial is currently expired and there is no working
> connection to test against. See `snowflake/README.md` for the live run this
> project already completed against DuckDB's Snowflake counterpart.

## What it gives you

- **No data copy.** The DuckDB-backed Community Cloud app reads a warehouse
  file that has to be rebuilt and re-committed every time the mart changes.
  A Streamlit in Snowflake app queries `revenue_analytics.analytics` directly
  through `data_source.py`'s Snowpark branch -- the dashboard is always as
  fresh as the last `dbt build`, with nothing to regenerate or recommit.
- **Snowflake's own RBAC.** The app runs as the REPORTER role (see
  `snowflake/01_bootstrap.sql`), which holds `select` on the `ANALYTICS`
  schema and nothing else. Access to the dashboard is governed by the same
  role grants that govern every other query against the warehouse, not by a
  separate application-level permission system.

## What it costs you

- **Not a public link.** Viewing the app requires an account in this
  Snowflake organization with the REPORTER role (or whichever role
  `streamlit_in_snowflake.sql`'s final grant names) -- there is no URL a
  stranger can open. That is the opposite of what a job-application artifact
  needs: something anyone can click without provisioning an account.
- **A trial account expires.** This project's Snowflake trial has a 30-day
  clock (see `snowflake/README.md`). When it lapses, the Streamlit in
  Snowflake deployment stops existing along with the rest of the account --
  there is nothing left to keep serving traffic.

## Why both deployments are deliberate, not redundant

The Community Cloud deployment reading the committed `warehouse.duckdb` file
remains the public artifact -- the one linked from this repository's README,
viewable with no account, no warehouse, no credentials, and no expiry. The
Streamlit in Snowflake deployment is the demonstration that the same
application code also runs unmodified against a live, RBAC-governed
warehouse. Each does something the other structurally cannot: one is
permanent and public, the other is live and access-controlled. Dropping
either would drop a capability, not just a copy.

## Packages

Streamlit in Snowflake provides `streamlit`, `pandas`, and `plotly` from its
own Anaconda-backed package channel -- the same three packages
`requirements.txt` pins for Community Cloud, so no packaging change was
needed for either surface to run the shared dashboard code. `duckdb` is
neither needed nor available in a Streamlit in Snowflake app: there is no
local file for it to open, since Snowpark supplies the live connection
instead. This is exactly why `data_source.py` imports both `duckdb` and the
Snowflake packages lazily, inside the branch that uses them, rather than at
module top level -- each deployment target is missing the packages the other
one needs, and an unconditional import would break whichever platform
doesn't have them installed.
