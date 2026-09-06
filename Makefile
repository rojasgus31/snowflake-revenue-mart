.PHONY: install ingest build test app lint clean all docs snowflake-proof

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
	uv run sqlfluff lint transform/models

clean:
	rm -rf warehouse.duckdb transform/target transform/dbt_packages

all: ingest build test

docs:
	uv run dbt docs generate --project-dir transform --profiles-dir transform
	uv run dbt docs serve --project-dir transform --profiles-dir transform

# Captures durable proof of a real Snowflake run into docs/evidence/, since a
# Snowflake trial expires after 30 days but this evidence does not. See
# snowflake/README.md for the role/warehouse split this target relies on:
# LOADER + WH_LOAD_XS creates tables in RAW, then TRANSFORMER + WH_TRANSFORM_XS
# runs the dbt build. Credentials (SNOWFLAKE_ACCOUNT, _USER, _PASSWORD,
# _DATABASE) must already be exported per snowflake/README.md.
snowflake-proof:
	@if [ "$$SNOWFLAKE_ROLE" != "LOADER" ]; then \
		echo "SNOWFLAKE_ROLE must be LOADER to load RAW (got '$$SNOWFLAKE_ROLE'). See snowflake/README.md."; \
		exit 1; \
	fi
	@if [ "$$SNOWFLAKE_WAREHOUSE" != "WH_LOAD_XS" ]; then \
		echo "SNOWFLAKE_WAREHOUSE must be WH_LOAD_XS for the LOADER role (got '$$SNOWFLAKE_WAREHOUSE'). See snowflake/README.md."; \
		exit 1; \
	fi
	mkdir -p docs/evidence
	{ \
		uv run python ingest/load_raw.py --target snowflake && \
		SNOWFLAKE_ROLE=TRANSFORMER SNOWFLAKE_WAREHOUSE=WH_TRANSFORM_XS \
			uv run dbt build --project-dir transform --profiles-dir transform --target snowflake; \
	} 2>&1 | tee docs/evidence/snowflake_build.log
	cp transform/target/run_results.json docs/evidence/snowflake_run_results.json
	@echo ""
	@echo "Captured docs/evidence/snowflake_build.log and docs/evidence/snowflake_run_results.json."
	@echo "Still needed by hand (see docs/evidence/README.md):"
	@echo "  - Snowsight screenshot of the mart's row count"
	@echo "  - Snowsight screenshot of the three roles (LOADER, TRANSFORMER, REPORTER)"
	@echo "  - Snowsight screenshot of warehouse credit usage"
