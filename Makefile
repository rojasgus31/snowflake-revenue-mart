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
	uv run sqlfluff lint transform/models

clean:
	rm -rf warehouse.duckdb transform/target transform/dbt_packages

all: ingest build test
