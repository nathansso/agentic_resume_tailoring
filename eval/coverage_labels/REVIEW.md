# Semantic-coverage labels for review (issue #126)

Each pair is one requirement from a job posting and one resume bullet. `covered`: the bullet shows the candidate meets the requirement, even in other words or at a more specific level. `not_covered`: it does not: it only names a related tool or area, states interest or a plan, or falls short of what the requirement asks (years, leadership, production, scale). These are the PROPOSED labels, awaiting the user's review; to change one, correct `label` in `pairs.json` and re-run `python eval/fit_coverage_threshold.py analyze` to refit. Disagreements with Jev come first.

## Disagreements with Jev (0)

## Agreements (97)

**1. `l_abtest`** (literal, profile bullet)
- Requirement: "Experience designing and analyzing A/B tests." (required)
- Bullet: "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically flat."
- Label: `covered`. Ran an A/B testing readout over 12 promotions.
- Jev: score 0.89 (covered)

**2. `l_airflow`** (literal, profile bullet)
- Requirement: "Experience building data pipelines with Apache Airflow." (required)
- Bullet: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Label: `covered`. Built Airflow DAGs at 40M events a day.
- Jev: score 0.97 (covered)

**3. `l_dbt`** (literal, profile bullet)
- Requirement: "Experience with dbt for data transformation and testing." (required)
- Bullet: "Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting."
- Label: `covered`. Added dbt tests to 60 models.
- Jev: score 0.96 (covered)

**4. `l_docker`** (literal, profile bullet)
- Requirement: "Experience packaging and shipping services with Docker." (preferred)
- Bullet: "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms."
- Label: `covered`. Packaged a service in Docker.
- Jev: score 0.94 (covered)

**5. `l_kafka`** (literal, profile bullet)
- Requirement: "Experience working with Kafka for streaming data." (preferred)
- Bullet: "Shipped a Kafka feature stream feeding 3 production models with sub-minute freshness."
- Label: `covered`. Shipped a Kafka feature stream.
- Jev: score 0.97 (covered)

**6. `l_langchain`** (literal, profile bullet)
- Requirement: "Experience building LLM agents with LangChain." (required)
- Bullet: "Built LangChain agents that call 6 campus APIs and return grounded answers with citations."
- Label: `covered`. Built LangChain agents calling 6 APIs.
- Jev: score 0.97 (covered)

**7. `l_pytorch`** (literal, profile bullet)
- Requirement: "Hands-on experience training models in PyTorch." (required)
- Bullet: "Trained a PyTorch sequence model on 1.1M clinical events, improving readmission recall by 12 points over the baseline."
- Label: `covered`. Trained a PyTorch model on 1.1M events.
- Jev: score 0.98 (covered)

**8. `l_rag`** (literal, profile bullet)
- Requirement: "Experience building retrieval-augmented generation (RAG) systems." (required)
- Bullet: "Built a RAG pipeline over 120k support documents with pgvector, lifting answer accuracy from 61% to 79%."
- Label: `covered`. Built a RAG pipeline over 120k documents.
- Jev: score 0.98 (covered)

**9. `l_react`** (literal, profile bullet)
- Requirement: "Experience building web front ends with React and TypeScript." (required)
- Bullet: "Built a React and TypeScript scheduling tool that replaced a paper process for 12 departments."
- Label: `covered`. Built a React and TypeScript tool.
- Jev: score 0.97 (covered)

**10. `l_sklearn`** (literal, profile bullet)
- Requirement: "Proficiency with scikit-learn for building predictive models." (required)
- Bullet: "Built a customer churn model in scikit-learn over 480k subscription records, raising AUC from 0.71 to 0.83 against the incumbent rule set."
- Label: `covered`. Built a churn model in scikit-learn.
- Jev: score 0.96 (covered)

**11. `l_spark`** (literal, profile bullet)
- Requirement: "Experience with Apache Spark for large-scale data processing." (required)
- Bullet: "Built a Spark job that deduplicated 90M ad impression rows nightly, trimming runtime from 50 minutes to 18."
- Label: `covered`. Built a Spark job over 90M rows nightly.
- Jev: score 0.96 (covered)

**12. `l_spring`** (literal, profile bullet)
- Requirement: "Experience building REST APIs with Spring Boot." (required)
- Bullet: "Shipped 14 REST endpoints in Spring Boot backing a customer portal used by 8,000 accounts."
- Label: `covered`. Shipped 14 REST endpoints in Spring Boot.
- Jev: score 0.97 (covered)

**13. `l_terraform`** (literal, profile bullet)
- Requirement: "Infrastructure-as-code experience with Terraform." (preferred)
- Bullet: "Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command."
- Label: `covered`. Wrote a Terraform module for S3 and IAM.
- Jev: score 0.94 (covered)

**14. `s_api_design`** (semantic, profile bullet)
- Requirement: "Experience designing and building APIs." (required)
- Bullet: "Added GraphQL resolvers for 9 entity types behind an existing REST gateway."
- Label: `covered`. Added GraphQL resolvers behind an existing gateway.
- Jev: score 0.89 (covered)

**15. `s_ci`** (semantic, profile bullet)
- Requirement: "Experience with continuous integration and automated testing." (required)
- Bullet: "Added GitHub Actions CI that ran 220 tests on every pull request."
- Label: `covered`. CI running 220 tests on every pull request.
- Jev: score 0.94 (covered)

**16. `s_cost_opt`** (semantic, profile bullet)
- Requirement: "Experience optimizing the cost and performance of cloud data platforms." (preferred)
- Bullet: "Cut warehouse spend 28% by repartitioning the 12 largest Parquet tables and pruning unused columns."
- Label: `covered`. Cut warehouse spend 28% through table layout.
- Jev: score 0.90 (covered)

**17. `s_data_quality`** (semantic, profile bullet)
- Requirement: "Experience implementing data quality checks and validation." (required)
- Bullet: "Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting."
- Label: `covered`. Tests caught 14 schema regressions before reporting.
- Jev: score 0.95 (covered)

**18. `s_dataviz`** (semantic, profile bullet)
- Requirement: "Experience communicating insights through dashboards and data visualization." (preferred)
- Bullet: "Published an interactive Tableau workbook opened 900 times in its first month."
- Label: `covered`. Published an interactive Tableau workbook.
- Jev: score 0.91 (covered)

**19. `s_dist_training`** (semantic, profile bullet)
- Requirement: "Experience with distributed computing for machine learning workloads." (required)
- Bullet: "Built a Ray-backed training pipeline that cut a 9-hour job to 2 hours across 4 GPUs."
- Label: `covered`. Distributed a training job across 4 GPUs.
- Jev: score 0.92 (covered)

**20. `s_etl_scale`** (semantic, profile bullet)
- Requirement: "Experience with data engineering at scale." (required)
- Bullet: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Label: `covered`. Moving 40M events a day into a warehouse is data engineering at scale.
- Jev: score 0.95 (covered)

**21. `s_experiment`** (semantic, profile bullet)
- Requirement: "Experience with statistical hypothesis testing and experimentation." (required)
- Bullet: "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically flat."
- Label: `covered`. Used bootstrap confidence intervals to call promotions flat.
- Jev: score 0.94 (covered)

**22. `s_llm_eval`** (semantic, profile bullet)
- Requirement: "Experience evaluating large language model applications." (required)
- Bullet: "Wrote an evaluation set of 200 graded questions that gated every prompt change."
- Label: `covered`. Built an eval set that gated every prompt change.
- Jev: score 0.82 (covered)

**23. `s_ml_deploy`** (semantic, profile bullet)
- Requirement: "Experience deploying machine learning models to production." (required)
- Bullet: "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms."
- Label: `covered`. Served a model behind an endpoint with a latency target.
- Jev: score 0.92 (covered)

**24. `s_ml_monitoring`** (semantic, profile bullet)
- Requirement: "Experience monitoring machine learning systems in production." (preferred)
- Bullet: "Added drift monitoring in Grafana that caught a 7% input distribution shift within a day."
- Label: `covered`. Drift monitoring that caught a 7% input shift.
- Jev: score 0.88 (covered)

**25. `s_orchestration`** (semantic, profile bullet)
- Requirement: "Experience with workflow orchestration and job scheduling." (required)
- Bullet: "Migrated 3 lab pipelines from cron scripts to Airflow, with retries and alerting."
- Label: `covered`. Moved cron scripts to a scheduler with retries and alerting.
- Jev: score 0.93 (covered)

**26. `s_teaching`** (semantic, profile bullet)
- Requirement: "Ability to teach and explain technical concepts to others." (preferred)
- Bullet: "Ran weekly lab sections for 30 students on Git, Linux, and testing practice."
- Label: `covered`. Taught weekly lab sections to 30 students.
- Jev: score 0.94 (covered)

**27. `s_timeseries`** (semantic, profile bullet)
- Requirement: "Experience with time series analysis." (preferred)
- Bullet: "Built demand forecasts for 34 clinic sites in Python and statsmodels, reducing weekly schedule error by 19%."
- Label: `covered`. Demand forecasting across 34 sites is time series work.
- Jev: score 0.91 (covered)

**28. `s_warehouse_models`** (semantic, profile bullet)
- Requirement: "Experience designing data warehouse schemas." (required)
- Bullet: "Modeled 8 core marketing tables in dbt with documented tests and freshness checks."
- Label: `covered`. Modeled 8 core tables with tests and freshness checks.
- Jev: score 0.68 (covered)

**29. `s_web_apps`** (semantic, profile bullet)
- Requirement: "Experience building user-facing web applications." (required)
- Bullet: "Built a React and TypeScript scheduling tool that replaced a paper process for 12 departments."
- Label: `covered`. Built a scheduling tool used by 12 departments.
- Jev: score 0.96 (covered)

**30. `p_aws_architecture`** (partial, profile bullet)
- Requirement: "Experience designing highly available multi-region AWS architectures." (required)
- Bullet: "Deployed with Docker and Terraform on AWS, holding 99% uptime over 6 months."
- Label: `not_covered`. Deployed on AWS, but no multi-region or architecture design.
- Jev: score 0.09 (not_covered)

**31. `p_cicd_owner`** (partial, profile bullet)
- Requirement: "Experience owning the CI/CD and release infrastructure for a product team." (required)
- Bullet: "Added GitHub Actions CI that ran 220 tests on every pull request."
- Label: `not_covered`. Added CI; no ownership of release infrastructure shown.
- Jev: score 0.39 (not_covered)

**32. `p_exec_present`** (partial, profile bullet)
- Requirement: "Experience presenting analyses to executive leadership." (preferred)
- Bullet: "Partnered with 3 product teams on an A/B testing framework now used for every release readout."
- Label: `not_covered`. Worked with product teams; no executive audience shown.
- Jev: score 0.18 (not_covered)

**33. `p_kafka_years`** (partial, profile bullet)
- Requirement: "5+ years of professional experience with Kafka." (required)
- Bullet: "Shipped a Kafka feature stream feeding 3 production models with sub-minute freshness."
- Label: `not_covered`. One stream, no years of experience shown.
- Jev: score 0.10 (not_covered)

**34. `p_lead_team`** (partial, profile bullet)
- Requirement: "Experience leading a team of data engineers." (required)
- Bullet: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Label: `not_covered`. Individual build; no leadership shown.
- Jev: score 0.04 (not_covered)

**35. `p_llm_pretrain`** (partial, profile bullet)
- Requirement: "Experience pretraining or fine-tuning large language models with billions of parameters." (required)
- Bullet: "Fine-tuned a Hugging Face classifier routing 12 question types to the right tool."
- Label: `not_covered`. Fine-tuned a small classifier, not a large language model.
- Jev: score 0.08 (not_covered)

**36. `p_ml_production`** (partial, profile bullet)
- Requirement: "Experience deploying and operating machine learning models in production." (required)
- Bullet: "Trained demand models in scikit-learn and PyTorch on 300k transactions for 2 campus partners."
- Label: `not_covered`. Training for campus partners; no deployment or operation shown.
- Jev: score 0.09 (not_covered)

**37. `p_petabyte`** (partial, profile bullet)
- Requirement: "Experience operating petabyte-scale data systems." (required)
- Bullet: "Loaded 1.4TB of sensor archives into Postgres and cut a common query from 90 seconds to 4."
- Label: `not_covered`. 1.4TB is three orders of magnitude short.
- Jev: score 0.04 (not_covered)

**38. `p_platform_architect`** (partial, profile bullet)
- Requirement: "Experience architecting data platforms end to end." (required)
- Bullet: "Rewrote a fragile shipment ingest job in Python, adding retries and structured logging."
- Label: `not_covered`. Rewrote one job; no platform architecture.
- Jev: score 0.08 (not_covered)

**39. `p_publications`** (partial, profile bullet)
- Requirement: "Publications at top-tier machine learning conferences." (preferred)
- Bullet: "Implemented 5 replay strategies and measured forgetting across a 10-task sequence."
- Label: `not_covered`. Research experiments, not a publication.
- Jev: score 0.02 (not_covered)

**40. `p_realtime_inference`** (partial, profile bullet)
- Requirement: "Experience building real-time inference services handling millions of requests per second." (required)
- Bullet: "Shipped a Kafka feature stream feeding 3 production models with sub-minute freshness."
- Label: `not_covered`. A feature stream for 3 models; no inference service at that scale.
- Jev: score 0.09 (not_covered)

**41. `p_spark_petabyte`** (partial, profile bullet)
- Requirement: "Experience tuning Spark jobs on petabyte-scale datasets." (required)
- Bullet: "Parsed 3,100 county result files with pandas and Spark, reconciling 5 conflicting schemas."
- Label: `not_covered`. Spark named, but 3,100 files is nowhere near petabyte scale or tuning.
- Jev: score 0.03 (not_covered)

**42. `p_sql_expert`** (partial, profile bullet)
- Requirement: "Expert SQL, including query optimization on multi-terabyte databases." (required)
- Bullet: "Cleaned and joined 9 semesters of enrollment data in SQL and pandas, resolving 4,200 duplicate student records."
- Label: `not_covered`. Used SQL to clean data; no optimization or terabyte scale.
- Jev: score 0.04 (not_covered)

**43. `a_airflow`** (aspiration, synthetic bullet)
- Requirement: "Experience building data pipelines with Airflow." (required)
- Bullet: "Learning Airflow in order to replace the existing cron scripts."
- Label: `not_covered`. Learning, not having built.
- Jev: score 0.03 (not_covered)

**44. `a_aws`** (aspiration, synthetic bullet)
- Requirement: "Experience deploying applications on AWS." (required)
- Bullet: "Planning to earn an AWS certification and move the team's services to the cloud."
- Label: `not_covered`. A plan, nothing deployed.
- Jev: score 0.03 (not_covered)

**45. `a_dbt`** (aspiration, synthetic bullet)
- Requirement: "Experience modeling data with dbt." (required)
- Bullet: "Completed a short course introducing dbt and hope to use it on upcoming projects."
- Label: `not_covered`. A course and a hope; no work with dbt.
- Jev: score 0.03 (not_covered)

**46. `a_deep_learning`** (aspiration, synthetic bullet)
- Requirement: "Experience training deep learning models." (required)
- Bullet: "Excited to get started with deep learning and currently reading about transformer architectures."
- Label: `not_covered`. Reading and excitement only.
- Jev: score 0.02 (not_covered)

**47. `a_go`** (aspiration, synthetic bullet)
- Requirement: "Experience programming in Go." (required)
- Bullet: "Wants to learn Go and has started working through the language tour."
- Label: `not_covered`. Started a tutorial.
- Jev: score 0.03 (not_covered)

**48. `a_k8s`** (aspiration, synthetic bullet)
- Requirement: "Experience with Kubernetes." (required)
- Bullet: "Eager to learn Kubernetes and apply it to the team's training jobs."
- Label: `not_covered`. Eager to learn is not experience.
- Jev: score 0.02 (not_covered)

**49. `a_rag`** (aspiration, synthetic bullet)
- Requirement: "Experience building retrieval-augmented generation systems." (required)
- Bullet: "Eager to learn retrieval-augmented generation and build a prototype soon."
- Label: `not_covered`. Eager to learn; no prototype yet.
- Jev: score 0.02 (not_covered)

**50. `a_react`** (aspiration, synthetic bullet)
- Requirement: "Experience building React applications." (required)
- Bullet: "Looking forward to building user interfaces in React as part of the roadmap."
- Label: `not_covered`. Looking forward to, not done.
- Jev: score 0.02 (not_covered)

**51. `a_rust`** (aspiration, synthetic bullet)
- Requirement: "Professional experience programming in Rust." (required)
- Bullet: "Interested in systems programming and planning to pick up Rust this year."
- Label: `not_covered`. A plan to learn.
- Jev: score 0.02 (not_covered)

**52. `a_spark`** (aspiration, synthetic bullet)
- Requirement: "Hands-on experience with Apache Spark." (required)
- Bullet: "Exploring Apache Spark through online tutorials to prepare for larger datasets."
- Label: `not_covered`. Tutorials are exploration, not experience.
- Jev: score 0.05 (not_covered)

**53. `a_terraform`** (aspiration, synthetic bullet)
- Requirement: "Experience with infrastructure as code using Terraform." (required)
- Bullet: "Keen to adopt Terraform for the group's infrastructure once the migration settles."
- Label: `not_covered`. An intention, nothing built.
- Jev: score 0.03 (not_covered)

**54. `n_angular`** (near_miss, profile bullet)
- Requirement: "Experience building single-page applications in Angular." (required)
- Bullet: "Built a React and TypeScript scheduling tool that replaced a paper process for 12 departments."
- Label: `not_covered`. React is a different framework.
- Jev: score 0.02 (not_covered)

**55. `n_aws_data`** (near_miss, profile bullet)
- Requirement: "Experience with AWS data services such as Redshift and Glue." (required)
- Bullet: "Ingested 2 years of GTFS feeds into BigQuery and modeled on-time performance across 190 routes."
- Label: `not_covered`. BigQuery is Google Cloud.
- Jev: score 0.03 (not_covered)

**56. `n_causal`** (near_miss, profile bullet)
- Requirement: "Experience with causal inference methods such as difference-in-differences and instrumental variables." (preferred)
- Bullet: "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically flat."
- Label: `not_covered`. Experimentation is related to causal inference, not those methods.
- Jev: score 0.07 (not_covered)

**57. `n_dagster`** (near_miss, profile bullet)
- Requirement: "Experience with Dagster or Prefect." (preferred)
- Bullet: "Migrated 3 lab pipelines from cron scripts to Airflow, with retries and alerting."
- Label: `not_covered`. Airflow is a sibling orchestrator, not those.
- Jev: score 0.05 (not_covered)

**58. `n_flink`** (near_miss, profile bullet)
- Requirement: "Experience with Apache Flink for stream processing." (required)
- Bullet: "Built a Spark job that deduplicated 90M ad impression rows nightly, trimming runtime from 50 minutes to 18."
- Label: `not_covered`. Spark batch is related, not Flink stream processing.
- Jev: score 0.02 (not_covered)

**59. `n_grpc`** (near_miss, profile bullet)
- Requirement: "Experience building gRPC services." (preferred)
- Bullet: "Shipped 14 REST endpoints in Spring Boot backing a customer portal used by 8,000 accounts."
- Label: `not_covered`. REST endpoints are not gRPC.
- Jev: score 0.02 (not_covered)

**60. `n_inference_opt`** (near_miss, profile bullet)
- Requirement: "Experience with model serving and inference optimization." (preferred)
- Bullet: "Cut training time 35% by moving augmentation into a prefetching data loader."
- Label: `not_covered`. Training speed-up, not serving or inference.
- Jev: score 0.05 (not_covered)

**61. `n_java`** (near_miss, profile bullet)
- Requirement: "Experience developing backend services in Java or Kotlin." (required)
- Bullet: "Built a Go service that replaced 3 cron jobs, processing 200k records an hour."
- Label: `not_covered`. Go is a different language.
- Jev: score 0.05 (not_covered)

**62. `n_kubernetes`** (near_miss, profile bullet)
- Requirement: "Experience running workloads on Kubernetes." (required)
- Bullet: "Containerized the ingest job with Docker so it runs identically on 2 campus servers."
- Label: `not_covered`. Docker is not Kubernetes.
- Jev: score 0.05 (not_covered)

**63. `n_mongodb`** (near_miss, profile bullet)
- Requirement: "Experience with MongoDB and other NoSQL databases." (preferred)
- Bullet: "Loaded 1.4TB of sensor archives into Postgres and cut a common query from 90 seconds to 4."
- Label: `not_covered`. Postgres is relational, not a NoSQL store.
- Jev: score 0.02 (not_covered)

**64. `n_nlp`** (near_miss, profile bullet)
- Requirement: "Experience with natural language processing." (required)
- Bullet: "Fine-tuned 4 image classification backbones on a 40k-image microscopy set, reaching 91% top-1."
- Label: `not_covered`. Vision models, not language.
- Jev: score 0.02 (not_covered)

**65. `n_powerbi`** (near_miss, profile bullet)
- Requirement: "Experience building Power BI reports." (preferred)
- Bullet: "Published an interactive Tableau workbook opened 900 times in its first month."
- Label: `not_covered`. Tableau is a sibling tool, not Power BI.
- Jev: score 0.05 (not_covered)

**66. `n_recsys`** (near_miss, profile bullet)
- Requirement: "Experience building recommendation systems." (required)
- Bullet: "Built a customer churn model in scikit-learn over 480k subscription records, raising AUC from 0.71 to 0.83 against the incumbent rule set."
- Label: `not_covered`. A churn classifier is not a recommender.
- Jev: score 0.03 (not_covered)

**67. `n_redshift`** (near_miss, profile bullet)
- Requirement: "Experience with Amazon Redshift." (required)
- Bullet: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Label: `not_covered`. Snowflake is a different warehouse.
- Jev: score 0.02 (not_covered)

**68. `n_tensorflow`** (near_miss, profile bullet)
- Requirement: "Experience building models with TensorFlow." (required)
- Bullet: "Trained a PyTorch sequence model on 1.1M clinical events, improving readmission recall by 12 points over the baseline."
- Label: `not_covered`. PyTorch is a sibling framework, not TensorFlow.
- Jev: score 0.02 (not_covered)

**69. `u_accounting`** (unrelated, profile bullet)
- Requirement: "CPA certification and experience with month-end financial close." (required)
- Bullet: "Built a Python autograder that cut assignment turnaround from 4 days to 1."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**70. `u_clearance`** (unrelated, profile bullet)
- Requirement: "Active Secret security clearance." (preferred)
- Bullet: "Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command."
- Label: `not_covered`. Unrelated requirement.
- Jev: score 0.01 (not_covered)

**71. `u_contracts`** (unrelated, profile bullet)
- Requirement: "Experience drafting and negotiating commercial contracts." (required)
- Bullet: "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically flat."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**72. `u_design`** (unrelated, profile bullet)
- Requirement: "A portfolio of graphic and brand design work." (preferred)
- Bullet: "Shipped a Kafka feature stream feeding 3 production models with sub-minute freshness."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**73. `u_firmware`** (unrelated, profile bullet)
- Requirement: "Experience with PCB layout and embedded firmware." (required)
- Bullet: "Automated a weekly cohort report in pandas and matplotlib, cutting a 6-hour manual Excel process to 15 minutes."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**74. `u_machining`** (unrelated, profile bullet)
- Requirement: "Experience with CNC machining and precision manufacturing." (required)
- Bullet: "Wrote an evaluation set of 200 graded questions that gated every prompt change."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.02 (not_covered)

**75. `u_nursing`** (unrelated, profile bullet)
- Requirement: "Clinical nursing experience in an acute-care setting." (required)
- Bullet: "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**76. `u_recruiting`** (unrelated, profile bullet)
- Requirement: "Experience in talent acquisition and recruiting." (preferred)
- Bullet: "Built an Airflow retraining schedule with automated evaluation gates."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**77. `u_sales`** (unrelated, profile bullet)
- Requirement: "Experience managing enterprise sales accounts." (required)
- Bullet: "Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**78. `u_social`** (unrelated, profile bullet)
- Requirement: "Experience running paid social media campaigns." (preferred)
- Bullet: "Wrote a Node.js sync service moving 30k roster records nightly into PostgreSQL."
- Label: `not_covered`. Unrelated field.
- Jev: score 0.01 (not_covered)

**79. `u_travel`** (unrelated, profile bullet)
- Requirement: "A valid driver's license and willingness to travel 25% of the time." (required)
- Bullet: "Shipped a Kafka consumer in Scala that backfilled 3 weeks of missing click events."
- Label: `not_covered`. Unrelated requirement.
- Jev: score 0.01 (not_covered)

**80. `b_app_security`** (boundary, profile bullet)
- Requirement: "Experience with application security and authentication." (preferred)
- Bullet: "Wrote a Node.js sync service moving 30k roster records nightly into PostgreSQL."
- Label: `not_covered`. Nothing about security in a sync service.
- Jev: score 0.03 (not_covered)

**81. `b_big_data`** (boundary, profile bullet)
- Requirement: "Experience working with big data technologies such as Hadoop or Spark." (required)
- Bullet: "Built a Spark job that deduplicated 90M ad impression rows nightly, trimming runtime from 50 minutes to 18."
- Label: `covered`. A Spark job over 90M rows.
- Jev: score 0.96 (covered)

**82. `b_cloud_infra`** (boundary, profile bullet)
- Requirement: "Experience provisioning and managing cloud infrastructure." (required)
- Bullet: "Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command."
- Label: `covered`. Provisioned S3 buckets and IAM roles as code.
- Jev: score 0.93 (covered)

**83. `b_communication`** (boundary, profile bullet)
- Requirement: "Strong written communication skills." (required)
- Bullet: "Posts a daily digest that replaced a manual review of 3 upstream feeds."
- Label: `not_covered`. Automation of a review, not evidence of writing or communicating.
- Jev: score 0.27 (not_covered)

**84. `b_containers`** (boundary, profile bullet)
- Requirement: "Experience with containerization." (preferred)
- Bullet: "Wrote a Docker image that reproduced the analysis stack for 8 downstream analysts."
- Label: `covered`. Wrote a Docker image for 8 analysts.
- Jev: score 0.95 (covered)

**85. `b_cross_functional`** (boundary, profile bullet)
- Requirement: "Experience collaborating with cross-functional teams." (required)
- Bullet: "Partnered with 3 product teams on an A/B testing framework now used for every release readout."
- Label: `covered`. Worked with 3 product teams on a shared framework.
- Jev: score 0.93 (covered)

**86. `b_dev_tools`** (boundary, profile bullet)
- Requirement: "Experience building internal developer tools." (preferred)
- Bullet: "Built a Python autograder that cut assignment turnaround from 4 days to 1."
- Label: `covered`. Built a tool the teaching staff used daily.
- Jev: score 0.84 (covered)

**87. `b_low_latency`** (boundary, profile bullet)
- Requirement: "Experience building low-latency, high-throughput services." (required)
- Bullet: "Shipped a retrieval service over 2M documents with pgvector and Redis caching at 120ms p95."
- Label: `covered`. 120ms p95 over 2M documents; throughput is not stated.
- Jev: score 0.88 (covered)

**88. `b_mlops`** (boundary, profile bullet)
- Requirement: "Experience with MLOps practices and experiment tracking." (preferred)
- Bullet: "Tracked 60 experiment runs in MLflow and wrote the comparison that selected the shipped checkpoint."
- Label: `covered`. MLflow run tracking and checkpoint selection.
- Jev: score 0.89 (covered)

**89. `b_model_eval`** (boundary, profile bullet)
- Requirement: "Experience with model evaluation and error analysis." (required)
- Bullet: "Wrote evaluation tooling that produced per-class confusion matrices for 3 papers under review."
- Label: `covered`. Per-class confusion matrices for 3 papers.
- Jev: score 0.92 (covered)

**90. `b_operate_k8s`** (boundary, profile bullet)
- Requirement: "Experience operating production Kubernetes clusters." (required)
- Bullet: "Runs on Kubernetes from a Docker image pinned to a reproducible CUDA build."
- Label: `not_covered`. Runs on Kubernetes; no operation of clusters shown.
- Jev: score 0.17 (not_covered)

**91. `b_pipeline_ownership`** (boundary, profile bullet)
- Requirement: "Experience owning production data pipelines." (required)
- Bullet: "Maintained the Airflow pipelines that load carrier invoices into the Snowflake warehouse."
- Label: `covered`. Maintained the pipelines a warehouse depends on.
- Jev: score 0.77 (covered)

**92. `b_python_skill`** (boundary, profile bullet)
- Requirement: "Strong Python programming skills." (required)
- Bullet: "Automated a weekly cohort report in pandas and matplotlib, cutting a 6-hour manual Excel process to 15 minutes."
- Label: `covered`. pandas and matplotlib are Python, though the bullet never says so.
- Jev: score 0.75 (covered)

**93. `b_reproduce_research`** (boundary, profile bullet)
- Requirement: "Ability to read and reproduce machine learning research papers." (preferred)
- Bullet: "Reproduced 2 published baselines within 1 point of their reported numbers."
- Label: `covered`. Reproduced 2 published baselines within 1 point.
- Jev: score 0.94 (covered)

**94. `b_sql_tuning`** (boundary, profile bullet)
- Requirement: "Experience with SQL query optimization." (preferred)
- Bullet: "Loaded 1.4TB of sensor archives into Postgres and cut a common query from 90 seconds to 4."
- Label: `covered`. Cut a common query from 90 seconds to 4.
- Jev: score 0.91 (covered)

**95. `b_streaming`** (boundary, profile bullet)
- Requirement: "Experience with real-time data streaming." (required)
- Bullet: "Shipped a Kafka consumer in Scala that backfilled 3 weeks of missing click events."
- Label: `covered`. A Kafka consumer is stream processing, though this one backfilled.
- Jev: score 0.84 (covered)

**96. `b_tdd`** (boundary, profile bullet)
- Requirement: "Experience practicing test-driven development." (preferred)
- Bullet: "Raised service test coverage from 46% to 78% with JUnit and contract tests."
- Label: `not_covered`. Raised test coverage; nothing shows tests written first.
- Jev: score 0.18 (not_covered)

**97. `b_team_lead`** (boundary, profile bullet)
- Requirement: "Experience leading and managing a team." (preferred)
- Bullet: "Ran weekly lab sections for 30 students on Git, Linux, and testing practice."
- Label: `not_covered`. Taught lab sections; did not lead a team.
- Jev: score 0.27 (not_covered)

