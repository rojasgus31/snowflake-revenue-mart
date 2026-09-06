# REPORTER role: verified least privilege

The REPORTER role exists so a BI tool can read the published marts and nothing
else. This is the record of that being tested against a live Snowflake account
rather than assumed from the GRANT statements.

## What the test found

Granting privileges correctly is not the same as those privileges being
enforced in a session. Run as REPORTER with default session settings, a query
against the RAW schema **succeeded** when it should have been refused:

```sql
use role reporter;
select current_role(), (select count(*) from revenue_analytics.raw.raw_oracle_orders);
```
```
| ROLE_NOW | RAW_ROWS |
| REPORTER | 128      |
```

The primary role really was REPORTER, and REPORTER's own grants really were
limited to the ANALYTICS schema (8 SELECT, 3 USAGE, nothing on RAW or STAGING).
The access was arriving another way:

```sql
select current_role() as primary_role, current_secondary_roles() as secondary;
```
```
| PRIMARY_ROLE | SECONDARY                                                          |
| REPORTER     | {"roles":"ACCOUNTADMIN,TRANSFORMER,ORGADMIN,LOADER","value":"ALL"} |
```

Snowflake defaults a user to `DEFAULT_SECONDARY_ROLES = ('ALL')`, so every role
granted to the user stays active alongside the primary one. `use role reporter`
narrows the primary role while ACCOUNTADMIN quietly remains in force, and the
privilege check passes through it. The role separation looks correct in the DDL
and is not enforced at runtime.

## The separation, once secondary roles are disabled

```sql
use role reporter;
use secondary roles none;
select count(*) from revenue_analytics.analytics.mart_revenue_performance;  -- allowed
select count(*) from revenue_analytics.raw.raw_oracle_orders;               -- refused
```
```
| MART_ROWS |
| 289       |
```
```
002003 (02000): SQL compilation error:
Schema 'REVENUE_ANALYTICS.RAW' does not exist or not authorized.
```

The grants were right all along. What was missing was disabling the secondary
roles that masked them.

## What this means in practice

A human account used for exploration will usually hold several roles, so
testing role separation from such an account requires `use secondary roles
none` or the result is meaningless.

A service account -- the credential a BI tool or a scheduled job actually
connects with -- should be created with no secondary roles at all:

```sql
create user bi_service_account
    default_role = reporter
    default_secondary_roles = ();
grant role reporter to user bi_service_account;
```

That way the least-privilege boundary holds without depending on the caller
remembering to narrow the session.
