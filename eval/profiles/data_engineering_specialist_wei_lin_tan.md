# Wei Lin Tan

wei.lin.tan@example.com | linkedin.com/in/wei-lin-tan | github.com/weilintan

## Summary
Data engineer working on batch and streaming ingestion. Most of my work is making a pipeline someone else depends on stop being a surprise.

## Experience

**Corvid Logistics** — Data Engineer I (Jun 2026 – Present)
- Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery.
- Cut warehouse spend 28% by repartitioning the 12 largest Parquet tables and pruning unused columns.
- Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting.

**Lakeshore Institute of Technology Research Computing** — Data Engineering Intern (May 2025 – Apr 2026)
- Migrated 3 lab pipelines from cron scripts to Airflow, with retries and alerting.
- Loaded 1.4TB of sensor archives into Postgres and cut a common query from 90 seconds to 4.
- Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command.

## Projects

**Slate Ingest** (github.com/weilintan/slate-ingest) — Idempotent file ingestion framework
- Loads 500k-row vendor drops into Postgres with content hashing, so a replayed file is a no-op.
- Ships a Docker Compose stack that stands the whole pipeline up in under 2 minutes.

**Column Drift** — Schema change detector
- Diffs 200 warehouse tables between runs and reports column additions, drops, and type changes.
- Posts a daily digest that replaced a manual review of 3 upstream feeds.

## Skills
Python, SQL, Spark, Airflow, dbt, Snowflake, Postgres, Kafka, AWS, Docker,
Terraform, BigQuery, Parquet, Bash, Git

## Education
B.S. Computer Science, Lakeshore Institute of Technology (May 2026)

## Achievements
**Lakeshore Capstone Award** (2026) — best systems project, cohort of 90
