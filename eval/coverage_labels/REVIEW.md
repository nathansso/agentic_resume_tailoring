# Semantic-coverage labels for review (issue #126)

Each pair is one requirement from a job posting and one resume bullet (or, for `education` pairs, one education entry as text). `covered`: it shows the candidate meets the requirement, even in other words or at a more specific level. `not_covered`: it does not: it only names a related tool or area, states interest or a plan, or falls short of what the requirement asks (years, leadership, production, scale; a different field or level of degree; a degree that is only expected where an earned one is asked). These are the labels the user confirmed (2026-09-30), kept for audit; every proposal was kept but `ss_ownership_run`, relabelled `not_covered`. To change one, correct `label` in `pairs.json` and re-run `python eval/fit_coverage_threshold.py analyze` to refit. Disagreements with Jev come first.

## Disagreements with Jev (2)

**1. `ss_ownership_run`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Takes ownership of production systems." (required)
- Bullet: "Maintained the Airflow pipelines that load carrier invoices into the Snowflake warehouse."
- Label: `not_covered`. Maintaining pipelines is not a stated instance of taking ownership, which is v2's own rule.
- Jev: score 0.61 (covered)

**2. `ss_process_benchmark`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Ability to work within established processes and development standards." (required)
- Bullet: "Published the work as a reproducible Jupyter notebook with 30 unit-tested transformation functions."
- Label: `not_covered`. A notebook bullet; no established process or standard named. Benchmark case (0.67 under v1).
- Jev: score 0.58 (covered)

## Agreements (129)

**3. `l_abtest`** (literal, profile bullet, label user-confirmed)
- Requirement: "Experience designing and analyzing A/B tests." (required)
- Bullet: "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically flat."
- Label: `covered`. Ran an A/B testing readout over 12 promotions.
- Jev: score 0.90 (covered)

**4. `l_airflow`** (literal, profile bullet, label user-confirmed)
- Requirement: "Experience building data pipelines with Apache Airflow." (required)
- Bullet: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Label: `covered`. Built Airflow DAGs at 40M events a day.
- Jev: score 0.97 (covered)

**5. `l_dbt`** (literal, profile bullet, label user-confirmed)
- Requirement: "Experience with dbt for data transformation and testing." (required)
- Bullet: "Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting."
- Label: `covered`. Added dbt tests to 60 models.
- Jev: score 0.96 (covered)

**6. `l_docker`** (literal, profile bullet, label user-confirmed)
- Requirement: "Experience packaging and shipping services with Docker." (preferred)
- Bullet: "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms."
- Label: `covered`. Packaged a service in Docker.
- Jev: score 0.94 (covered)

**7. `l_kafka`** (literal, profile bullet, label user-confirmed)
- Requirement: "Experience working with Kafka for streaming data." (preferred)
- Bullet: "Shipped a Kafka feature stream feeding 3 production models with sub-minute freshness."
- Label: `covered`. Shipped a Kafka feature stream.
- Jev: score 0.97 (covered)

**8. `l_langchain`** (literal, profile bullet, label user-confirmed)
- Requirement: "Experience building LLM agents with LangChain." (required)
- Bullet: "Built LangChain agents that call 6 campus APIs and return grounded answers with citations."
- Label: `covered`. Built LangChain agents calling 6 APIs.
- Jev: score 0.97 (covered)

**9. `l_pytorch`** (literal, profile bullet, label user-confirmed)
- Requirement: "Hands-on experience training models in PyTorch." (required)
- Bullet: "Trained a PyTorch sequence model on 1.1M clinical events, improving readmission recall by 12 points over the baseline."
- Label: `covered`. Trained a PyTorch model on 1.1M events.
- Jev: score 0.98 (covered)

**10. `l_rag`** (literal, profile bullet, label user-confirmed)
- Requirement: "Experience building retrieval-augmented generation (RAG) systems." (required)
- Bullet: "Built a RAG pipeline over 120k support documents with pgvector, lifting answer accuracy from 61% to 79%."
- Label: `covered`. Built a RAG pipeline over 120k documents.
- Jev: score 0.98 (covered)

**11. `l_react`** (literal, profile bullet, label user-confirmed)
- Requirement: "Experience building web front ends with React and TypeScript." (required)
- Bullet: "Built a React and TypeScript scheduling tool that replaced a paper process for 12 departments."
- Label: `covered`. Built a React and TypeScript tool.
- Jev: score 0.97 (covered)

**12. `l_sklearn`** (literal, profile bullet, label user-confirmed)
- Requirement: "Proficiency with scikit-learn for building predictive models." (required)
- Bullet: "Built a customer churn model in scikit-learn over 480k subscription records, raising AUC from 0.71 to 0.83 against the incumbent rule set."
- Label: `covered`. Built a churn model in scikit-learn.
- Jev: score 0.96 (covered)

**13. `l_spark`** (literal, profile bullet, label user-confirmed)
- Requirement: "Experience with Apache Spark for large-scale data processing." (required)
- Bullet: "Built a Spark job that deduplicated 90M ad impression rows nightly, trimming runtime from 50 minutes to 18."
- Label: `covered`. Built a Spark job over 90M rows nightly.
- Jev: score 0.96 (covered)

**14. `l_spring`** (literal, profile bullet, label user-confirmed)
- Requirement: "Experience building REST APIs with Spring Boot." (required)
- Bullet: "Shipped 14 REST endpoints in Spring Boot backing a customer portal used by 8,000 accounts."
- Label: `covered`. Shipped 14 REST endpoints in Spring Boot.
- Jev: score 0.97 (covered)

**15. `l_terraform`** (literal, profile bullet, label user-confirmed)
- Requirement: "Infrastructure-as-code experience with Terraform." (preferred)
- Bullet: "Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command."
- Label: `covered`. Wrote a Terraform module for S3 and IAM.
- Jev: score 0.94 (covered)

**16. `s_api_design`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience designing and building APIs." (required)
- Bullet: "Added GraphQL resolvers for 9 entity types behind an existing REST gateway."
- Label: `covered`. Added GraphQL resolvers behind an existing gateway.
- Jev: score 0.88 (covered)

**17. `s_ci`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience with continuous integration and automated testing." (required)
- Bullet: "Added GitHub Actions CI that ran 220 tests on every pull request."
- Label: `covered`. CI running 220 tests on every pull request.
- Jev: score 0.94 (covered)

**18. `s_cost_opt`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience optimizing the cost and performance of cloud data platforms." (preferred)
- Bullet: "Cut warehouse spend 28% by repartitioning the 12 largest Parquet tables and pruning unused columns."
- Label: `covered`. Cut warehouse spend 28% through table layout.
- Jev: score 0.91 (covered)

**19. `s_data_quality`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience implementing data quality checks and validation." (required)
- Bullet: "Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting."
- Label: `covered`. Tests caught 14 schema regressions before reporting.
- Jev: score 0.96 (covered)

**20. `s_dataviz`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience communicating insights through dashboards and data visualization." (preferred)
- Bullet: "Published an interactive Tableau workbook opened 900 times in its first month."
- Label: `covered`. Published an interactive Tableau workbook.
- Jev: score 0.88 (covered)

**21. `s_dist_training`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience with distributed computing for machine learning workloads." (required)
- Bullet: "Built a Ray-backed training pipeline that cut a 9-hour job to 2 hours across 4 GPUs."
- Label: `covered`. Distributed a training job across 4 GPUs.
- Jev: score 0.93 (covered)

**22. `s_etl_scale`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience with data engineering at scale." (required)
- Bullet: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Label: `covered`. Moving 40M events a day into a warehouse is data engineering at scale.
- Jev: score 0.96 (covered)

**23. `s_experiment`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience with statistical hypothesis testing and experimentation." (required)
- Bullet: "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically flat."
- Label: `covered`. Used bootstrap confidence intervals to call promotions flat.
- Jev: score 0.94 (covered)

**24. `s_llm_eval`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience evaluating large language model applications." (required)
- Bullet: "Wrote an evaluation set of 200 graded questions that gated every prompt change."
- Label: `covered`. Built an eval set that gated every prompt change.
- Jev: score 0.85 (covered)

**25. `s_ml_deploy`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience deploying machine learning models to production." (required)
- Bullet: "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms."
- Label: `covered`. Served a model behind an endpoint with a latency target.
- Jev: score 0.92 (covered)

**26. `s_ml_monitoring`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience monitoring machine learning systems in production." (preferred)
- Bullet: "Added drift monitoring in Grafana that caught a 7% input distribution shift within a day."
- Label: `covered`. Drift monitoring that caught a 7% input shift.
- Jev: score 0.88 (covered)

**27. `s_orchestration`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience with workflow orchestration and job scheduling." (required)
- Bullet: "Migrated 3 lab pipelines from cron scripts to Airflow, with retries and alerting."
- Label: `covered`. Moved cron scripts to a scheduler with retries and alerting.
- Jev: score 0.94 (covered)

**28. `s_teaching`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Ability to teach and explain technical concepts to others." (preferred)
- Bullet: "Ran weekly lab sections for 30 students on Git, Linux, and testing practice."
- Label: `covered`. Taught weekly lab sections to 30 students.
- Jev: score 0.93 (covered)

**29. `s_timeseries`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience with time series analysis." (preferred)
- Bullet: "Built demand forecasts for 34 clinic sites in Python and statsmodels, reducing weekly schedule error by 19%."
- Label: `covered`. Demand forecasting across 34 sites is time series work.
- Jev: score 0.91 (covered)

**30. `s_warehouse_models`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience designing data warehouse schemas." (required)
- Bullet: "Modeled 8 core marketing tables in dbt with documented tests and freshness checks."
- Label: `covered`. Modeled 8 core tables with tests and freshness checks.
- Jev: score 0.74 (covered)

**31. `s_web_apps`** (semantic, profile bullet, label user-confirmed)
- Requirement: "Experience building user-facing web applications." (required)
- Bullet: "Built a React and TypeScript scheduling tool that replaced a paper process for 12 departments."
- Label: `covered`. Built a scheduling tool used by 12 departments.
- Jev: score 0.96 (covered)

**32. `p_aws_architecture`** (partial, profile bullet, label user-confirmed)
- Requirement: "Experience designing highly available multi-region AWS architectures." (required)
- Bullet: "Deployed with Docker and Terraform on AWS, holding 99% uptime over 6 months."
- Label: `not_covered`. Deployed on AWS, but no multi-region or architecture design.
- Jev: score 0.08 (not_covered)

**33. `p_cicd_owner`** (partial, profile bullet, label user-confirmed)
- Requirement: "Experience owning the CI/CD and release infrastructure for a product team." (required)
- Bullet: "Added GitHub Actions CI that ran 220 tests on every pull request."
- Label: `not_covered`. Added CI; no ownership of release infrastructure shown.
- Jev: score 0.44 (not_covered)

**34. `p_exec_present`** (partial, profile bullet, label user-confirmed)
- Requirement: "Experience presenting analyses to executive leadership." (preferred)
- Bullet: "Partnered with 3 product teams on an A/B testing framework now used for every release readout."
- Label: `not_covered`. Worked with product teams; no executive audience shown.
- Jev: score 0.17 (not_covered)

**35. `p_kafka_years`** (partial, profile bullet, label user-confirmed)
- Requirement: "5+ years of professional experience with Kafka." (required)
- Bullet: "Shipped a Kafka feature stream feeding 3 production models with sub-minute freshness."
- Label: `not_covered`. One stream, no years of experience shown.
- Jev: score 0.08 (not_covered)

**36. `p_lead_team`** (partial, profile bullet, label user-confirmed)
- Requirement: "Experience leading a team of data engineers." (required)
- Bullet: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Label: `not_covered`. Individual build; no leadership shown.
- Jev: score 0.03 (not_covered)

**37. `p_llm_pretrain`** (partial, profile bullet, label user-confirmed)
- Requirement: "Experience pretraining or fine-tuning large language models with billions of parameters." (required)
- Bullet: "Fine-tuned a Hugging Face classifier routing 12 question types to the right tool."
- Label: `not_covered`. Fine-tuned a small classifier, not a large language model.
- Jev: score 0.08 (not_covered)

**38. `p_ml_production`** (partial, profile bullet, label user-confirmed)
- Requirement: "Experience deploying and operating machine learning models in production." (required)
- Bullet: "Trained demand models in scikit-learn and PyTorch on 300k transactions for 2 campus partners."
- Label: `not_covered`. Training for campus partners; no deployment or operation shown.
- Jev: score 0.07 (not_covered)

**39. `p_petabyte`** (partial, profile bullet, label user-confirmed)
- Requirement: "Experience operating petabyte-scale data systems." (required)
- Bullet: "Loaded 1.4TB of sensor archives into Postgres and cut a common query from 90 seconds to 4."
- Label: `not_covered`. 1.4TB is three orders of magnitude short.
- Jev: score 0.04 (not_covered)

**40. `p_platform_architect`** (partial, profile bullet, label user-confirmed)
- Requirement: "Experience architecting data platforms end to end." (required)
- Bullet: "Rewrote a fragile shipment ingest job in Python, adding retries and structured logging."
- Label: `not_covered`. Rewrote one job; no platform architecture.
- Jev: score 0.08 (not_covered)

**41. `p_publications`** (partial, profile bullet, label user-confirmed)
- Requirement: "Publications at top-tier machine learning conferences." (preferred)
- Bullet: "Implemented 5 replay strategies and measured forgetting across a 10-task sequence."
- Label: `not_covered`. Research experiments, not a publication.
- Jev: score 0.02 (not_covered)

**42. `p_realtime_inference`** (partial, profile bullet, label user-confirmed)
- Requirement: "Experience building real-time inference services handling millions of requests per second." (required)
- Bullet: "Shipped a Kafka feature stream feeding 3 production models with sub-minute freshness."
- Label: `not_covered`. A feature stream for 3 models; no inference service at that scale.
- Jev: score 0.10 (not_covered)

**43. `p_spark_petabyte`** (partial, profile bullet, label user-confirmed)
- Requirement: "Experience tuning Spark jobs on petabyte-scale datasets." (required)
- Bullet: "Parsed 3,100 county result files with pandas and Spark, reconciling 5 conflicting schemas."
- Label: `not_covered`. Spark named, but 3,100 files is nowhere near petabyte scale or tuning.
- Jev: score 0.03 (not_covered)

**44. `p_sql_expert`** (partial, profile bullet, label user-confirmed)
- Requirement: "Expert SQL, including query optimization on multi-terabyte databases." (required)
- Bullet: "Cleaned and joined 9 semesters of enrollment data in SQL and pandas, resolving 4,200 duplicate student records."
- Label: `not_covered`. Used SQL to clean data; no optimization or terabyte scale.
- Jev: score 0.04 (not_covered)

**45. `a_airflow`** (aspiration, synthetic bullet, label user-confirmed)
- Requirement: "Experience building data pipelines with Airflow." (required)
- Bullet: "Learning Airflow in order to replace the existing cron scripts."
- Label: `not_covered`. Learning, not having built.
- Jev: score 0.03 (not_covered)

**46. `a_aws`** (aspiration, synthetic bullet, label user-confirmed)
- Requirement: "Experience deploying applications on AWS." (required)
- Bullet: "Planning to earn an AWS certification and move the team's services to the cloud."
- Label: `not_covered`. A plan, nothing deployed.
- Jev: score 0.03 (not_covered)

**47. `a_dbt`** (aspiration, synthetic bullet, label user-confirmed)
- Requirement: "Experience modeling data with dbt." (required)
- Bullet: "Completed a short course introducing dbt and hope to use it on upcoming projects."
- Label: `not_covered`. A course and a hope; no work with dbt.
- Jev: score 0.03 (not_covered)

**48. `a_deep_learning`** (aspiration, synthetic bullet, label user-confirmed)
- Requirement: "Experience training deep learning models." (required)
- Bullet: "Excited to get started with deep learning and currently reading about transformer architectures."
- Label: `not_covered`. Reading and excitement only.
- Jev: score 0.02 (not_covered)

**49. `a_go`** (aspiration, synthetic bullet, label user-confirmed)
- Requirement: "Experience programming in Go." (required)
- Bullet: "Wants to learn Go and has started working through the language tour."
- Label: `not_covered`. Started a tutorial.
- Jev: score 0.03 (not_covered)

**50. `a_k8s`** (aspiration, synthetic bullet, label user-confirmed)
- Requirement: "Experience with Kubernetes." (required)
- Bullet: "Eager to learn Kubernetes and apply it to the team's training jobs."
- Label: `not_covered`. Eager to learn is not experience.
- Jev: score 0.02 (not_covered)

**51. `a_rag`** (aspiration, synthetic bullet, label user-confirmed)
- Requirement: "Experience building retrieval-augmented generation systems." (required)
- Bullet: "Eager to learn retrieval-augmented generation and build a prototype soon."
- Label: `not_covered`. Eager to learn; no prototype yet.
- Jev: score 0.02 (not_covered)

**52. `a_react`** (aspiration, synthetic bullet, label user-confirmed)
- Requirement: "Experience building React applications." (required)
- Bullet: "Looking forward to building user interfaces in React as part of the roadmap."
- Label: `not_covered`. Looking forward to, not done.
- Jev: score 0.02 (not_covered)

**53. `a_rust`** (aspiration, synthetic bullet, label user-confirmed)
- Requirement: "Professional experience programming in Rust." (required)
- Bullet: "Interested in systems programming and planning to pick up Rust this year."
- Label: `not_covered`. A plan to learn.
- Jev: score 0.02 (not_covered)

**54. `a_spark`** (aspiration, synthetic bullet, label user-confirmed)
- Requirement: "Hands-on experience with Apache Spark." (required)
- Bullet: "Exploring Apache Spark through online tutorials to prepare for larger datasets."
- Label: `not_covered`. Tutorials are exploration, not experience.
- Jev: score 0.06 (not_covered)

**55. `a_terraform`** (aspiration, synthetic bullet, label user-confirmed)
- Requirement: "Experience with infrastructure as code using Terraform." (required)
- Bullet: "Keen to adopt Terraform for the group's infrastructure once the migration settles."
- Label: `not_covered`. An intention, nothing built.
- Jev: score 0.03 (not_covered)

**56. `n_angular`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience building single-page applications in Angular." (required)
- Bullet: "Built a React and TypeScript scheduling tool that replaced a paper process for 12 departments."
- Label: `not_covered`. React is a different framework.
- Jev: score 0.02 (not_covered)

**57. `n_aws_data`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience with AWS data services such as Redshift and Glue." (required)
- Bullet: "Ingested 2 years of GTFS feeds into BigQuery and modeled on-time performance across 190 routes."
- Label: `not_covered`. BigQuery is Google Cloud.
- Jev: score 0.03 (not_covered)

**58. `n_causal`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience with causal inference methods such as difference-in-differences and instrumental variables." (preferred)
- Bullet: "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically flat."
- Label: `not_covered`. Experimentation is related to causal inference, not those methods.
- Jev: score 0.08 (not_covered)

**59. `n_dagster`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience with Dagster or Prefect." (preferred)
- Bullet: "Migrated 3 lab pipelines from cron scripts to Airflow, with retries and alerting."
- Label: `not_covered`. Airflow is a sibling orchestrator, not those.
- Jev: score 0.04 (not_covered)

**60. `n_flink`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience with Apache Flink for stream processing." (required)
- Bullet: "Built a Spark job that deduplicated 90M ad impression rows nightly, trimming runtime from 50 minutes to 18."
- Label: `not_covered`. Spark batch is related, not Flink stream processing.
- Jev: score 0.02 (not_covered)

**61. `n_grpc`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience building gRPC services." (preferred)
- Bullet: "Shipped 14 REST endpoints in Spring Boot backing a customer portal used by 8,000 accounts."
- Label: `not_covered`. REST endpoints are not gRPC.
- Jev: score 0.02 (not_covered)

**62. `n_inference_opt`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience with model serving and inference optimization." (preferred)
- Bullet: "Cut training time 35% by moving augmentation into a prefetching data loader."
- Label: `not_covered`. Training speed-up, not serving or inference.
- Jev: score 0.06 (not_covered)

**63. `n_java`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience developing backend services in Java or Kotlin." (required)
- Bullet: "Built a Go service that replaced 3 cron jobs, processing 200k records an hour."
- Label: `not_covered`. Go is a different language.
- Jev: score 0.03 (not_covered)

**64. `n_kubernetes`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience running workloads on Kubernetes." (required)
- Bullet: "Containerized the ingest job with Docker so it runs identically on 2 campus servers."
- Label: `not_covered`. Docker is not Kubernetes.
- Jev: score 0.05 (not_covered)

**65. `n_mongodb`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience with MongoDB and other NoSQL databases." (preferred)
- Bullet: "Loaded 1.4TB of sensor archives into Postgres and cut a common query from 90 seconds to 4."
- Label: `not_covered`. Postgres is relational, not a NoSQL store.
- Jev: score 0.02 (not_covered)

**66. `n_nlp`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience with natural language processing." (required)
- Bullet: "Fine-tuned 4 image classification backbones on a 40k-image microscopy set, reaching 91% top-1."
- Label: `not_covered`. Vision models, not language.
- Jev: score 0.01 (not_covered)

**67. `n_powerbi`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience building Power BI reports." (preferred)
- Bullet: "Published an interactive Tableau workbook opened 900 times in its first month."
- Label: `not_covered`. Tableau is a sibling tool, not Power BI.
- Jev: score 0.04 (not_covered)

**68. `n_recsys`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience building recommendation systems." (required)
- Bullet: "Built a customer churn model in scikit-learn over 480k subscription records, raising AUC from 0.71 to 0.83 against the incumbent rule set."
- Label: `not_covered`. A churn classifier is not a recommender.
- Jev: score 0.03 (not_covered)

**69. `n_redshift`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience with Amazon Redshift." (required)
- Bullet: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Label: `not_covered`. Snowflake is a different warehouse.
- Jev: score 0.02 (not_covered)

**70. `n_tensorflow`** (near_miss, profile bullet, label user-confirmed)
- Requirement: "Experience building models with TensorFlow." (required)
- Bullet: "Trained a PyTorch sequence model on 1.1M clinical events, improving readmission recall by 12 points over the baseline."
- Label: `not_covered`. PyTorch is a sibling framework, not TensorFlow.
- Jev: score 0.03 (not_covered)

**71. `u_accounting`** (unrelated, profile bullet, label user-confirmed)
- Requirement: "CPA certification and experience with month-end financial close." (required)
- Bullet: "Built a Python autograder that cut assignment turnaround from 4 days to 1."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**72. `u_clearance`** (unrelated, profile bullet, label user-confirmed)
- Requirement: "Active Secret security clearance." (preferred)
- Bullet: "Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command."
- Label: `not_covered`. Unrelated requirement.
- Jev: score 0.01 (not_covered)

**73. `u_contracts`** (unrelated, profile bullet, label user-confirmed)
- Requirement: "Experience drafting and negotiating commercial contracts." (required)
- Bullet: "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically flat."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**74. `u_design`** (unrelated, profile bullet, label user-confirmed)
- Requirement: "A portfolio of graphic and brand design work." (preferred)
- Bullet: "Shipped a Kafka feature stream feeding 3 production models with sub-minute freshness."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**75. `u_firmware`** (unrelated, profile bullet, label user-confirmed)
- Requirement: "Experience with PCB layout and embedded firmware." (required)
- Bullet: "Automated a weekly cohort report in pandas and matplotlib, cutting a 6-hour manual Excel process to 15 minutes."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**76. `u_machining`** (unrelated, profile bullet, label user-confirmed)
- Requirement: "Experience with CNC machining and precision manufacturing." (required)
- Bullet: "Wrote an evaluation set of 200 graded questions that gated every prompt change."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.02 (not_covered)

**77. `u_nursing`** (unrelated, profile bullet, label user-confirmed)
- Requirement: "Clinical nursing experience in an acute-care setting." (required)
- Bullet: "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**78. `u_recruiting`** (unrelated, profile bullet, label user-confirmed)
- Requirement: "Experience in talent acquisition and recruiting." (preferred)
- Bullet: "Built an Airflow retraining schedule with automated evaluation gates."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**79. `u_sales`** (unrelated, profile bullet, label user-confirmed)
- Requirement: "Experience managing enterprise sales accounts." (required)
- Bullet: "Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**80. `u_social`** (unrelated, profile bullet, label user-confirmed)
- Requirement: "Experience running paid social media campaigns." (preferred)
- Bullet: "Wrote a Node.js sync service moving 30k roster records nightly into PostgreSQL."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**81. `u_travel`** (unrelated, profile bullet, label user-confirmed)
- Requirement: "A valid driver's license and willingness to travel 25% of the time." (required)
- Bullet: "Shipped a Kafka consumer in Scala that backfilled 3 weeks of missing click events."
- Label: `not_covered`. Unrelated requirement.
- Jev: score 0.01 (not_covered)

**82. `b_app_security`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience with application security and authentication." (preferred)
- Bullet: "Wrote a Node.js sync service moving 30k roster records nightly into PostgreSQL."
- Label: `not_covered`. Nothing about security in a sync service.
- Jev: score 0.03 (not_covered)

**83. `b_big_data`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience working with big data technologies such as Hadoop or Spark." (required)
- Bullet: "Built a Spark job that deduplicated 90M ad impression rows nightly, trimming runtime from 50 minutes to 18."
- Label: `covered`. A Spark job over 90M rows.
- Jev: score 0.97 (covered)

**84. `b_cloud_infra`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience provisioning and managing cloud infrastructure." (required)
- Bullet: "Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command."
- Label: `covered`. Provisioned S3 buckets and IAM roles as code.
- Jev: score 0.94 (covered)

**85. `b_communication`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Strong written communication skills." (required)
- Bullet: "Posts a daily digest that replaced a manual review of 3 upstream feeds."
- Label: `not_covered`. Automation of a review, not evidence of writing or communicating.
- Jev: score 0.21 (not_covered)

**86. `b_containers`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience with containerization." (preferred)
- Bullet: "Wrote a Docker image that reproduced the analysis stack for 8 downstream analysts."
- Label: `covered`. Wrote a Docker image for 8 analysts.
- Jev: score 0.95 (covered)

**87. `b_cross_functional`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience collaborating with cross-functional teams." (required)
- Bullet: "Partnered with 3 product teams on an A/B testing framework now used for every release readout."
- Label: `covered`. Worked with 3 product teams on a shared framework.
- Jev: score 0.94 (covered)

**88. `b_dev_tools`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience building internal developer tools." (preferred)
- Bullet: "Built a Python autograder that cut assignment turnaround from 4 days to 1."
- Label: `covered`. Built a tool the teaching staff used daily.
- Jev: score 0.85 (covered)

**89. `b_low_latency`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience building low-latency, high-throughput services." (required)
- Bullet: "Shipped a retrieval service over 2M documents with pgvector and Redis caching at 120ms p95."
- Label: `covered`. 120ms p95 over 2M documents; throughput is not stated.
- Jev: score 0.89 (covered)

**90. `b_mlops`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience with MLOps practices and experiment tracking." (preferred)
- Bullet: "Tracked 60 experiment runs in MLflow and wrote the comparison that selected the shipped checkpoint."
- Label: `covered`. MLflow run tracking and checkpoint selection.
- Jev: score 0.91 (covered)

**91. `b_model_eval`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience with model evaluation and error analysis." (required)
- Bullet: "Wrote evaluation tooling that produced per-class confusion matrices for 3 papers under review."
- Label: `covered`. Per-class confusion matrices for 3 papers.
- Jev: score 0.93 (covered)

**92. `b_operate_k8s`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience operating production Kubernetes clusters." (required)
- Bullet: "Runs on Kubernetes from a Docker image pinned to a reproducible CUDA build."
- Label: `not_covered`. Runs on Kubernetes; no operation of clusters shown.
- Jev: score 0.17 (not_covered)

**93. `b_pipeline_ownership`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience owning production data pipelines." (required)
- Bullet: "Maintained the Airflow pipelines that load carrier invoices into the Snowflake warehouse."
- Label: `covered`. Maintained the pipelines a warehouse depends on.
- Jev: score 0.75 (covered)

**94. `b_python_skill`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Strong Python programming skills." (required)
- Bullet: "Automated a weekly cohort report in pandas and matplotlib, cutting a 6-hour manual Excel process to 15 minutes."
- Label: `covered`. pandas and matplotlib are Python, though the bullet never says so.
- Jev: score 0.73 (covered)

**95. `b_reproduce_research`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Ability to read and reproduce machine learning research papers." (preferred)
- Bullet: "Reproduced 2 published baselines within 1 point of their reported numbers."
- Label: `covered`. Reproduced 2 published baselines within 1 point.
- Jev: score 0.93 (covered)

**96. `b_sql_tuning`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience with SQL query optimization." (preferred)
- Bullet: "Loaded 1.4TB of sensor archives into Postgres and cut a common query from 90 seconds to 4."
- Label: `covered`. Cut a common query from 90 seconds to 4.
- Jev: score 0.91 (covered)

**97. `b_streaming`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience with real-time data streaming." (required)
- Bullet: "Shipped a Kafka consumer in Scala that backfilled 3 weeks of missing click events."
- Label: `covered`. A Kafka consumer is stream processing, though this one backfilled.
- Jev: score 0.87 (covered)

**98. `b_tdd`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience practicing test-driven development." (preferred)
- Bullet: "Raised service test coverage from 46% to 78% with JUnit and contract tests."
- Label: `not_covered`. Raised test coverage; nothing shows tests written first.
- Jev: score 0.14 (not_covered)

**99. `b_team_lead`** (boundary, profile bullet, label user-confirmed)
- Requirement: "Experience leading and managing a team." (preferred)
- Bullet: "Ran weekly lab sections for 30 students on Git, Linux, and testing practice."
- Label: `not_covered`. Taught lab sections; did not lead a team.
- Jev: score 0.31 (not_covered)

**100. `ss_agile_adjacent`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Experience following agile processes and sprint ceremonies." (preferred)
- Bullet: "Raised service test coverage from 46% to 78% with JUnit and contract tests."
- Label: `not_covered`. Raised coverage; no agile process shown.
- Jev: score 0.03 (not_covered)

**101. `ss_code_review`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Collaborates with teammates through code review." (required)
- Bullet: "Reviewed pull requests from the rest of the team and kept the CI pipeline green."
- Label: `covered`. Reviewed teammates' pull requests.
- Jev: score 0.94 (covered)

**102. `ss_comm_adjacent`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Strong verbal and written communication skills." (required)
- Bullet: "Built a React and TypeScript scheduling tool that replaced a paper process for 12 departments."
- Label: `not_covered`. Built a tool; no communication shown.
- Jev: score 0.06 (not_covered)

**103. `ss_cross_team`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Experience collaborating across teams." (required)
- Bullet: "Partnered with 3 product teams on an A/B testing framework now used for every release readout."
- Label: `covered`. Partnered with 3 product teams on a shared framework.
- Jev: score 0.95 (covered)

**104. `ss_deadline_benchmark`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Excellent problem-solving skills and the ability to work under tight deadlines." (required)
- Bullet: "Automated a weekly cohort report in pandas and matplotlib, cutting a 6-hour manual Excel process to 15 minutes."
- Label: `not_covered`. An automation bullet; nothing about a deadline. Benchmark case (0.58 under v1 of the bullet question).
- Jev: score 0.28 (not_covered)

**105. `ss_deadline_real`** (soft_skill, synthetic bullet, label user-confirmed)
- Requirement: "Ability to work under tight deadlines." (required)
- Bullet: "Delivered the client migration two weeks before the contractual deadline while the team was two engineers short."
- Label: `covered`. Names a deadline and delivering two weeks ahead of it, short-handed.
- Jev: score 0.96 (covered)

**106. `ss_detail_adjacent`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Meticulous attention to detail." (required)
- Bullet: "Cut training time 35% by moving augmentation into a prefetching data loader."
- Label: `not_covered`. A speed-up; nothing about care or accuracy.
- Jev: score 0.11 (not_covered)

**107. `ss_detail_regressions`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Strong attention to detail." (required)
- Bullet: "Built an evaluation harness scoring 40 scenarios per release, catching 6 regressions before launch."
- Label: `covered`. Scored 40 scenarios per release and caught 6 regressions before launch.
- Jev: score 0.84 (covered)

**108. `ss_detail_schema`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Attention to detail and a commitment to data quality." (required)
- Bullet: "Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting."
- Label: `covered`. Tests caught 14 schema regressions before reporting.
- Jev: score 0.88 (covered)

**109. `ss_fast_paced`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Thrives in a fast-paced environment with shifting priorities." (preferred)
- Bullet: "Built a Go service that replaced 3 cron jobs, processing 200k records an hour."
- Label: `not_covered`. A build; no pace or shifting priorities shown.
- Jev: score 0.08 (not_covered)

**110. `ss_handoff_docs`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Ability to document work so that others can continue it." (required)
- Bullet: "Documented the deployment steps so the next intern could run a release unaided."
- Label: `covered`. Documented the release steps so the next intern could run one unaided: a handoff.
- Jev: score 0.97 (covered)

**111. `ss_knowledge_share`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Strong documentation and knowledge-sharing habits." (preferred)
- Bullet: "Documented the feature preparation steps so the team could rerun the study after I left."
- Label: `covered`. Documented the steps so the team could rerun the study after leaving.
- Jev: score 0.92 (covered)

**112. `ss_ownership_adjacent`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Takes full ownership of projects from idea to launch." (required)
- Bullet: "Wrote a Docker image that reproduced the analysis stack for 8 downstream analysts."
- Label: `not_covered`. Wrote one image; no ownership from idea to launch.
- Jev: score 0.20 (not_covered)

**113. `ss_ownership_initiative`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Shows ownership: finds and fixes problems without being asked." (preferred)
- Bullet: "Added dbt tests to the billing models after a schema change reached the reporting layer unnoticed."
- Label: `covered`. Added tests to the billing models after a schema change slipped through.
- Jev: score 0.74 (covered)

**114. `ss_process_real`** (soft_skill, synthetic bullet, label user-confirmed)
- Requirement: "Ability to work within established processes." (required)
- Bullet: "Followed the team's release checklist and change-approval process for 14 production releases."
- Label: `covered`. Names the process followed and 14 releases under it.
- Jev: score 0.97 (covered)

**115. `ss_stakeholders`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Works effectively with cross-functional stakeholders." (required)
- Bullet: "Reviewed annotation disagreements with the research group and revised the guidelines."
- Label: `covered`. Resolved disagreements with the research group and revised the guidelines.
- Jev: score 0.72 (covered)

**116. `ss_team_adjacent`** (soft_skill, profile bullet, label user-confirmed)
- Requirement: "Collaborates across teams to deliver shared goals." (required)
- Bullet: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Label: `not_covered`. A solo build; no other team named.
- Jev: score 0.07 (not_covered)

**117. `e_bs_cs`** (education, profile education, label user-confirmed)
- Requirement: "Bachelor's degree in Computer Science or a related field." (required)
- Education entry: "B.S. Computer Science, Lakeshore Institute of Technology"
- Label: `covered`. B.S. Computer Science, earned.
- Jev: score 0.98 (covered)

**118. `e_bs_quant`** (education, profile education, label user-confirmed)
- Requirement: "Bachelor's degree in a quantitative field such as Statistics, Mathematics or Data Science." (required)
- Education entry: "B.S. Data Science, Rivermount College"
- Label: `covered`. B.S. Data Science is a named field.
- Jev: score 0.98 (covered)

**119. `e_bs_related_field`** (education, profile education, label user-confirmed)
- Requirement: "Bachelor's degree in Computer Science, Engineering or a related technical field." (required)
- Education entry: "B.S. Computer Engineering, Porto Vista State University"
- Label: `covered`. B.S. Computer Engineering is a listed field.
- Jev: score 0.96 (covered)

**120. `e_completed_not_enrolled`** (education, profile education, label user-confirmed)
- Requirement: "Currently enrolled in a degree program." (required)
- Education entry: "B.S. Computer Science, Fairmont University"
- Label: `not_covered`. The entry states a finished degree and no enrollment; calling it finished needs today's date, so it is left uncovered.
- Jev: score 0.21 (not_covered)

**121. `e_enrolled`** (education, profile education, label user-confirmed)
- Requirement: "Currently enrolled in a bachelor's or master's program." (required)
- Education entry: "B.S. Computer Science, Halden State University (Expected May 2027)"
- Label: `covered`. B.S. expected May 2027: enrolled, as the entry states.
- Jev: score 0.95 (covered)

**122. `e_expected_not_earned`** (education, profile education, label user-confirmed)
- Requirement: "Bachelor's degree required." (required)
- Education entry: "B.S. Statistics, Cascade State University (Expected May 2027)"
- Label: `not_covered`. The degree is marked expected: not yet earned. Contestable.
- Jev: score 0.07 (not_covered)

**123. `e_minor_only`** (education, profile education, label user-confirmed)
- Requirement: "Degree in Computer Science." (required)
- Education entry: "B.S. Cognitive Science (Computer Science minor), Saint Clare University"
- Label: `not_covered`. Computer Science is only a minor; the degree is Cognitive Science.
- Jev: score 0.05 (not_covered)

**124. `e_pursuing_cs`** (education, profile education, label user-confirmed)
- Requirement: "Currently pursuing a degree in Computer Science." (required)
- Education entry: "B.S. Computer Science, Ardenne Polytechnic (Expected May 2027)"
- Label: `covered`. B.S. Computer Science, expected: pursuing it.
- Jev: score 0.95 (covered)

**125. `e_pursuing_stats`** (education, profile education, label user-confirmed)
- Requirement: "Pursuing a degree in Statistics, Mathematics or a related field." (preferred)
- Education entry: "B.S. Statistics, Cascade State University (Expected May 2027)"
- Label: `covered`. B.S. Statistics, expected.
- Jev: score 0.90 (covered)

**126. `e_skill_kafka`** (education, profile education, label user-confirmed)
- Requirement: "Experience building Kafka consumers." (required)
- Education entry: "B.S. Computer Science, Vellore Ridge University (Expected May 2027)"
- Label: `not_covered`. A degree is not experience with a tool.
- Jev: score 0.03 (not_covered)

**127. `e_skill_stats`** (education, profile education, label user-confirmed)
- Requirement: "Experience with statistical modeling." (required)
- Education entry: "B.S. Statistics, Cascade State University (Expected May 2027)"
- Label: `not_covered`. A degree in the field is not experience modeling. Contestable.
- Jev: score 0.12 (not_covered)

**128. `e_unrelated`** (education, profile education, label user-confirmed)
- Requirement: "Active Secret security clearance." (preferred)
- Education entry: "B.S. Computer Science, Lakeshore Institute of Technology"
- Label: `not_covered`. A degree line says nothing of a clearance.
- Jev: score 0.02 (not_covered)

**129. `e_wrong_field`** (education, profile education, label user-confirmed)
- Requirement: "Bachelor's degree in Electrical Engineering." (required)
- Education entry: "B.S. Computer Science, Lakeshore Institute of Technology"
- Label: `not_covered`. Computer Science is not Electrical Engineering.
- Jev: score 0.02 (not_covered)

**130. `e_wrong_level_master`** (education, profile education, label user-confirmed)
- Requirement: "Master's degree in Computer Science required." (required)
- Education entry: "B.S. Computer Science, Lakeshore Institute of Technology"
- Label: `not_covered`. A bachelor's, where a master's is asked.
- Jev: score 0.02 (not_covered)

**131. `e_wrong_level_phd`** (education, profile education, label user-confirmed)
- Requirement: "PhD in Machine Learning or a related field." (required)
- Education entry: "B.S. Computer Science, Ardenne Polytechnic (Expected May 2027)"
- Label: `not_covered`. A bachelor's in progress, where a doctorate is asked.
- Jev: score 0.01 (not_covered)

