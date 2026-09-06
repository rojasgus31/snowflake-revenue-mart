{#
    dbt's built-in generate_schema_name macro CONCATENATES the target schema with a
    model's custom schema (e.g. `+schema: staging` on a `dev` target would produce
    `dev_staging`, and on this project's `duckdb` target it produced `main_staging` /
    `main_analytics`). This project's verification commands and the Task 13 Streamlit
    dashboard both query the plain schema names `staging` and `analytics`, so we
    override the macro here to use the custom schema verbatim, falling back to the
    target schema only when a model declares no custom schema at all.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set default_schema = target.schema -%}
    {%- if custom_schema_name is none -%}
        {{ default_schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
