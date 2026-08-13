# Gabriel Ferreira

gabriel.ferreira@example.com | linkedin.com/in/gabriel-ferreira | github.com/gferreira-dev

## Summary
Data engineer maintaining the ingestion and warehouse layer for a freight billing platform. Most of what I do is make failures visible before someone downstream finds them.

## Experience

**Alder Freight Systems** — Junior Data Engineer (Jul 2026 – Present)
- Maintained the Airflow pipelines that load carrier invoices into the Snowflake warehouse.
- Rewrote a fragile shipment ingest job in Python, adding retries and structured logging.
- Added dbt tests to the billing models after a schema change reached the reporting layer unnoticed.

**Porto Vista State University Facilities Analytics** — Data Engineering Intern (Jan 2026 – Jun 2026)
- Loaded building meter readings into Postgres and documented the table layout for analysts.
- Replaced a spreadsheet handoff with a scheduled export written in Python.
- Wrote Bash tooling that checked feed freshness before the morning reports ran.

## Projects

**Ledger Loader** — Personal finance ETL
- Parses bank exports into a normalized Postgres schema with idempotent loads.
- Handles duplicate transactions by hashing the source row rather than trusting file order.

**Route Cache** (github.com/gferreira-dev/route-cache) — Transit schedule store
- Caches GTFS schedule data in Parquet and serves lookups without a network call.
- Includes a Docker Compose setup so a fresh clone runs end to end.

## Skills
Python, SQL, Spark, Airflow, dbt, Snowflake, Postgres, Kafka, AWS, Docker,
Terraform, BigQuery, Parquet, Bash, Git

## Education
B.S. Computer Engineering, Porto Vista State University (June 2026)
