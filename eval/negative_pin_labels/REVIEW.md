# Negative-pin labels for review (issue #232)

Each pair is one pin (a topic the user said must never render) and one text a plan might write: a bullet, or an item's company or project name. `mentions`: the text mentions or refers to the pinned topic, by name, in other words, or by a product, employer or project that belongs to it. `does_not_mention`: it does not; a related but different topic is not a mention. These are the labels the user confirmed (2026-09-30), kept for audit; to change one, correct `label` in `pairs.json` and re-run `python eval/fit_negative_pin_threshold.py analyze` to refit. Disagreements with Jev come first.

## Disagreements with Jev (3)

**1. `n_hadoop_spark`** (near_miss, bullet)
- Pin: term "hadoop", statement "Never mention Hadoop"; Jev is asked about: "Hadoop"
- Text: "Parsed 3,100 county result files with pandas and Spark, reconciling 5 conflicting schemas."
- Label: `does_not_mention`. Spark is not Hadoop.
- Jev: score 0.76 (mentions)

**2. `i_employer_field`** (indirect, company)
- Pin: term "my first data engineering job", statement "My first data engineering job, at Corvid Logistics"; Jev is asked about: "My first data engineering job, at Corvid Logistics"
- Text: "Corvid Logistics"
- Label: `mentions`. The statement names the employer, so the company field refers to the pinned job.
- Jev: score 0.42 (does_not_mention)

**3. `i_ms_github`** (indirect, bullet)
- Pin: term "microsoft", statement "Don't mention Microsoft products"; Jev is asked about: "Microsoft products"
- Text: "Added GitHub Actions CI that ran 220 tests on every pull request."
- Label: `mentions`. GitHub is owned by Microsoft.
- Jev: score 0.27 (does_not_mention)

## Agreements (71)

**4. `d_airflow`** (direct, bullet)
- Pin: term "airflow", statement "Never mention Airflow"; Jev is asked about: "Airflow"
- Text: "Migrated 3 lab pipelines from cron scripts to Airflow, with retries and alerting."
- Label: `mentions`. Names Airflow.
- Jev: score 0.99 (mentions)

**5. `d_company_field`** (direct, company)
- Pin: term "ardent chat", statement "Never mention Ardent Chat"; Jev is asked about: "Ardent Chat"
- Text: "Ardent Chat"
- Label: `mentions`. The employer field is the pinned name.
- Jev: score 0.98 (mentions)

**6. `d_docker`** (direct, bullet)
- Pin: term "docker", statement "Leave out Docker"; Jev is asked about: "Docker"
- Text: "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms."
- Label: `mentions`. Names Docker.
- Jev: score 0.99 (mentions)

**7. `d_fastapi`** (direct, bullet)
- Pin: term "fastapi", statement "Please never mention FastAPI"; Jev is asked about: "FastAPI"
- Text: "Serves verification from FastAPI within a 200ms budget per response."
- Label: `mentions`. Names FastAPI.
- Jev: score 0.99 (mentions)

**8. `d_kafka`** (direct, bullet)
- Pin: term "kafka", statement "Never mention Kafka"; Jev is asked about: "Kafka"
- Text: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Label: `mentions`. Names Kafka.
- Jev: score 0.99 (mentions)

**9. `d_mongodb`** (direct, bullet)
- Pin: term "mongodb", statement "Skip anything about MongoDB"; Jev is asked about: "MongoDB"
- Text: "Parses 50k PGN games into MongoDB and reports opening win rates by rating band."
- Label: `mentions`. Names MongoDB.
- Jev: score 0.99 (mentions)

**10. `d_name_field`** (direct, name)
- Pin: term "transit pulse", statement "Never mention Transit Pulse"; Jev is asked about: "Transit Pulse"
- Text: "Transit Pulse"
- Label: `mentions`. The project name field is the pinned name.
- Jev: score 0.97 (mentions)

**11. `d_pgvector`** (direct, bullet)
- Pin: term "pgvector", statement "Do not mention pgvector"; Jev is asked about: "pgvector"
- Text: "Built a RAG pipeline over 120k support documents with pgvector, lifting answer accuracy from 61% to 79%."
- Label: `mentions`. Names pgvector.
- Jev: score 0.99 (mentions)

**12. `d_redis`** (direct, bullet)
- Pin: term "redis", statement "Never mention Redis"; Jev is asked about: "Redis"
- Text: "Caches 20k judgments in Redis to keep a full re-run under a minute."
- Label: `mentions`. Names Redis.
- Jev: score 0.99 (mentions)

**13. `d_tableau`** (direct, bullet)
- Pin: term "tableau", statement "Avoid mentioning Tableau"; Jev is asked about: "Tableau"
- Text: "Published an interactive Tableau workbook opened 900 times in its first month."
- Label: `mentions`. Names Tableau.
- Jev: score 0.99 (mentions)

**14. `d_terraform`** (direct, bullet)
- Pin: term "terraform", statement "Never include Terraform"; Jev is asked about: "Terraform"
- Text: "Deployed with Docker and Terraform on AWS, holding 99% uptime over 6 months."
- Label: `mentions`. Names Terraform.
- Jev: score 0.99 (mentions)

**15. `p_ab`** (paraphrase, bullet)
- Pin: term "experimentation", statement "Never mention experimentation"; Jev is asked about: "experimentation"
- Text: "Partnered with 3 product teams on an A/B testing framework now used for every release readout."
- Label: `mentions`. An A/B testing framework is experimentation.
- Jev: score 0.98 (mentions)

**16. `p_agents`** (paraphrase, bullet)
- Pin: term "ai agents", statement "Do not bring up AI agents"; Jev is asked about: "AI agents"
- Text: "Built LangChain agents that call 6 campus APIs and return grounded answers with citations."
- Label: `mentions`. LangChain agents are AI agents.
- Jev: score 0.99 (mentions)

**17. `p_ci`** (paraphrase, bullet)
- Pin: term "continuous integration", statement "Leave out continuous integration"; Jev is asked about: "continuous integration"
- Text: "Added GitHub Actions CI that ran 220 tests on every pull request."
- Label: `mentions`. CI is continuous integration.
- Jev: score 0.99 (mentions)

**18. `p_containers`** (paraphrase, bullet)
- Pin: term "containers", statement "Don't mention containers"; Jev is asked about: "containers"
- Text: "Wrote a Docker image that reproduced the analysis stack for 8 downstream analysts."
- Label: `mentions`. A Docker image is a container.
- Jev: score 0.98 (mentions)

**19. `p_cron`** (paraphrase, bullet)
- Pin: term "scheduled jobs", statement "Don't mention scheduled jobs"; Jev is asked about: "scheduled jobs"
- Text: "Built a Go service that replaced 3 cron jobs, processing 200k records an hour."
- Label: `mentions`. Cron jobs are scheduled jobs.
- Jev: score 0.98 (mentions)

**20. `p_deeplearning`** (paraphrase, bullet)
- Pin: term "deep learning", statement "Never mention deep learning"; Jev is asked about: "deep learning"
- Text: "Trained a PyTorch sequence model on 1.1M clinical events, improving readmission recall by 12 points over the baseline."
- Label: `mentions`. A PyTorch sequence model is deep learning.
- Jev: score 0.97 (mentions)

**21. `p_dwh`** (paraphrase, bullet)
- Pin: term "data warehouse", statement "Don't mention data warehouses"; Jev is asked about: "data warehouses"
- Text: "Cut warehouse spend 28% by repartitioning the 12 largest Parquet tables and pruning unused columns."
- Label: `mentions`. Warehouse spend is data-warehouse work.
- Jev: score 0.86 (mentions)

**22. `p_edge`** (paraphrase, bullet)
- Pin: term "edge computing", statement "Don't bring up edge computing"; Jev is asked about: "edge computing"
- Text: "Distilled a detector to 8MB for edge devices, retaining 88% of teacher mAP."
- Label: `mentions`. Edge devices are edge computing.
- Jev: score 0.96 (mentions)

**23. `p_frontend`** (paraphrase, bullet)
- Pin: term "frontend development", statement "Never mention frontend development"; Jev is asked about: "frontend development"
- Text: "Built a React and TypeScript scheduling tool that replaced a paper process for 12 departments."
- Label: `mentions`. A React and TypeScript tool is frontend development.
- Jev: score 0.98 (mentions)

**24. `p_pipelines`** (paraphrase, bullet)
- Pin: term "data pipelines", statement "Never mention data pipelines"; Jev is asked about: "data pipelines"
- Text: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Label: `mentions`. DAGs moving events between systems are data pipelines.
- Jev: score 0.98 (mentions)

**25. `p_rag`** (paraphrase, bullet)
- Pin: term "retrieval-augmented generation", statement "Never mention retrieval-augmented generation"; Jev is asked about: "retrieval-augmented generation"
- Text: "Built a RAG pipeline over 120k support documents with pgvector, lifting answer accuracy from 61% to 79%."
- Label: `mentions`. RAG is retrieval-augmented generation.
- Jev: score 0.99 (mentions)

**26. `p_teaching`** (paraphrase, bullet)
- Pin: term "teaching", statement "Never mention teaching experience"; Jev is asked about: "teaching experience"
- Text: "Ran weekly lab sections for 30 students on Git, Linux, and testing practice."
- Label: `mentions`. Running lab sections for students is teaching.
- Jev: score 0.97 (mentions)

**27. `p_testing`** (paraphrase, bullet)
- Pin: term "automated testing", statement "Never mention automated testing"; Jev is asked about: "automated testing"
- Text: "Raised service test coverage from 46% to 78% with JUnit and contract tests."
- Label: `mentions`. JUnit and contract tests are automated testing.
- Jev: score 0.98 (mentions)

**28. `p_tracking`** (paraphrase, bullet)
- Pin: term "experiment tracking", statement "Never mention experiment tracking"; Jev is asked about: "experiment tracking"
- Text: "Tracked 60 experiment runs in MLflow and wrote the comparison that selected the shipped checkpoint."
- Label: `mentions`. Tracking runs in MLflow is experiment tracking.
- Jev: score 0.99 (mentions)

**29. `i_alphabet_bq`** (indirect, bullet)
- Pin: term "alphabet", statement "Never mention Alphabet companies"; Jev is asked about: "Alphabet companies"
- Text: "Ingested 2 years of GTFS feeds into BigQuery and modeled on-time performance across 190 routes."
- Label: `mentions`. BigQuery is an Alphabet product (via Google).
- Jev: score 0.94 (mentions)

**30. `i_amazon_aws`** (indirect, bullet)
- Pin: term "amazon", statement "Never mention Amazon or its products"; Jev is asked about: "Amazon or its products"
- Text: "Deployed the stack on AWS with Docker behind a Next.js frontend."
- Label: `mentions`. AWS is an Amazon product.
- Jev: score 0.99 (mentions)

**31. `i_amazon_s3`** (indirect, bullet)
- Pin: term "amazon web services", statement "Leave out Amazon Web Services"; Jev is asked about: "Amazon Web Services"
- Text: "Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command."
- Label: `mentions`. S3 and IAM are AWS services.
- Jev: score 0.97 (mentions)

**32. `i_election_field`** (indirect, name)
- Pin: term "my election data project", statement "My election data project, Ballot Tally"; Jev is asked about: "My election data project, Ballot Tally"
- Text: "Ballot Tally"
- Label: `mentions`. The statement names the project, so the project name refers to it.
- Jev: score 0.90 (mentions)

**33. `i_google_bq`** (indirect, bullet)
- Pin: term "google cloud", statement "Don't mention Google Cloud"; Jev is asked about: "Google Cloud"
- Text: "Ingested 2 years of GTFS feeds into BigQuery and modeled on-time performance across 190 routes."
- Label: `mentions`. BigQuery is a Google Cloud product.
- Jev: score 0.97 (mentions)

**34. `i_hashicorp_tf`** (indirect, bullet)
- Pin: term "hashicorp", statement "Never mention HashiCorp tooling"; Jev is asked about: "HashiCorp tooling"
- Text: "Deployed with Docker and Terraform on AWS, holding 99% uptime over 6 months."
- Label: `mentions`. Terraform is HashiCorp's.
- Jev: score 0.94 (mentions)

**35. `i_meta_react`** (indirect, bullet)
- Pin: term "meta", statement "Never mention anything made by Meta"; Jev is asked about: "anything made by Meta"
- Text: "Real-time queue used by 400 students across 6 courses, built in React and Node.js."
- Label: `mentions`. React is made by Meta.
- Jev: score 0.57 (mentions)

**36. `i_project_field`** (indirect, name)
- Pin: term "my ingestion side project", statement "My ingestion side project, Slate Ingest"; Jev is asked about: "My ingestion side project, Slate Ingest"
- Text: "Slate Ingest"
- Label: `mentions`. The statement names the project, so the project name refers to it.
- Jev: score 0.95 (mentions)

**37. `n_angular_react`** (near_miss, bullet)
- Pin: term "angular", statement "Never mention Angular"; Jev is asked about: "Angular"
- Text: "Built a React and TypeScript scheduling tool that replaced a paper process for 12 departments."
- Label: `does_not_mention`. React is a different frontend framework.
- Jev: score 0.02 (does_not_mention)

**38. `n_azure_aws`** (near_miss, bullet)
- Pin: term "azure", statement "Never mention Azure"; Jev is asked about: "Azure"
- Text: "Deployed the stack on AWS with Docker behind a Next.js frontend."
- Label: `does_not_mention`. AWS is a different cloud.
- Jev: score 0.02 (does_not_mention)

**39. `n_grpc_rest`** (near_miss, bullet)
- Pin: term "grpc", statement "Never mention gRPC"; Jev is asked about: "gRPC"
- Text: "Added GraphQL resolvers for 9 entity types behind an existing REST gateway."
- Label: `does_not_mention`. GraphQL and REST are different API styles.
- Jev: score 0.02 (does_not_mention)

**40. `n_healthcare_students`** (near_miss, bullet)
- Pin: term "healthcare analytics", statement "Never mention healthcare analytics"; Jev is asked about: "healthcare analytics"
- Text: "Trained a gradient-boosted retention model on 22,000 student records, beating the prior heuristic by 14 points of recall."
- Label: `does_not_mention`. A retention model on student records is not healthcare.
- Jev: score 0.02 (does_not_mention)

**41. `n_kafka_airflow`** (near_miss, bullet)
- Pin: term "kafka", statement "Never mention Kafka"; Jev is asked about: "Kafka"
- Text: "Migrated 3 lab pipelines from cron scripts to Airflow, with retries and alerting."
- Label: `does_not_mention`. Airflow is an orchestrator, not a message broker.
- Jev: score 0.01 (does_not_mention)

**42. `n_llm_finetune`** (near_miss, bullet)
- Pin: term "llm fine-tuning", statement "Never mention fine-tuning language models"; Jev is asked about: "fine-tuning language models"
- Text: "Fine-tuned 4 image classification backbones on a 40k-image microscopy set, reaching 91% top-1."
- Label: `does_not_mention`. Fine-tuning, but of image models, not language models.
- Jev: score 0.12 (does_not_mention)

**43. `n_mongo_postgres`** (near_miss, bullet)
- Pin: term "mongodb", statement "Never mention MongoDB"; Jev is asked about: "MongoDB"
- Text: "Next.js and Postgres app that settles group expenses in the fewest transfers."
- Label: `does_not_mention`. Postgres is a different database.
- Jev: score 0.02 (does_not_mention)

**44. `n_other_employer`** (near_miss, company)
- Pin: term "ardent chat", statement "Never mention Ardent Chat"; Jev is asked about: "Ardent Chat"
- Text: "Saint Clare University Digital Learning"
- Label: `does_not_mention`. A different employer.
- Jev: score 0.02 (does_not_mention)

**45. `n_redis_sqlite`** (near_miss, bullet)
- Pin: term "redis", statement "Never mention Redis"; Jev is asked about: "Redis"
- Text: "Stores runs in SQLite and exports a side-by-side comparison report."
- Label: `does_not_mention`. SQLite is a different store.
- Jev: score 0.02 (does_not_mention)

**46. `n_research_grading`** (near_miss, bullet)
- Pin: term "research experience", statement "Never mention research experience"; Jev is asked about: "research experience"
- Text: "Graded and gave written feedback on 120 data-structures assignments a term."
- Label: `does_not_mention`. Grading coursework is not research.
- Jev: score 0.02 (does_not_mention)

**47. `n_rl_replay`** (near_miss, bullet)
- Pin: term "reinforcement learning", statement "Never mention reinforcement learning"; Jev is asked about: "reinforcement learning"
- Text: "Implemented 5 replay strategies and measured forgetting across a 10-task sequence."
- Label: `does_not_mention`. Continual learning replay is not reinforcement learning.
- Jev: score 0.13 (does_not_mention)

**48. `n_sibling_project`** (near_miss, name)
- Pin: term "prompt ledger", statement "Never mention Prompt Ledger"; Jev is asked about: "Prompt Ledger"
- Text: "Cite Guard"
- Label: `does_not_mention`. A different project by the same author.
- Jev: score 0.07 (does_not_mention)

**49. `n_snowflake_bq`** (near_miss, bullet)
- Pin: term "snowflake", statement "Never mention Snowflake"; Jev is asked about: "Snowflake"
- Text: "Ingested 2 years of GTFS feeds into BigQuery and modeled on-time performance across 190 routes."
- Label: `does_not_mention`. BigQuery is a different warehouse.
- Jev: score 0.02 (does_not_mention)

**50. `n_tf_pytorch`** (near_miss, bullet)
- Pin: term "tensorflow", statement "Never mention TensorFlow"; Jev is asked about: "TensorFlow"
- Text: "Trained a PyTorch sequence model on 1.1M clinical events, improving readmission recall by 12 points over the baseline."
- Label: `does_not_mention`. PyTorch is a rival framework, not TensorFlow.
- Jev: score 0.02 (does_not_mention)

**51. `n_unit_e2e`** (near_miss, bullet)
- Pin: term "unit testing", statement "Never mention unit testing"; Jev is asked about: "unit testing"
- Text: "Wrote Cypress end-to-end tests covering the 5 highest-traffic user flows."
- Label: `does_not_mention`. End-to-end tests are not unit tests.
- Jev: score 0.04 (does_not_mention)

**52. `u_blockchain`** (unrelated, bullet)
- Pin: term "blockchain", statement "Never mention blockchain"; Jev is asked about: "blockchain"
- Text: "Cut warehouse spend 28% by repartitioning the 12 largest Parquet tables and pruning unused columns."
- Label: `does_not_mention`. Nothing to do with it.
- Jev: score 0.01 (does_not_mention)

**53. `u_cuda`** (unrelated, bullet)
- Pin: term "cuda", statement "Never mention CUDA"; Jev is asked about: "CUDA"
- Text: "Shipped 14 REST endpoints in Spring Boot backing a customer portal used by 8,000 accounts."
- Label: `does_not_mention`. Nothing to do with it.
- Jev: score 0.01 (does_not_mention)

**54. `u_employer_field`** (unrelated, company)
- Pin: term "goldman sachs", statement "Never mention Goldman Sachs"; Jev is asked about: "Goldman Sachs"
- Text: "Halcyon Health Analytics"
- Label: `does_not_mention`. A different employer.
- Jev: score 0.01 (does_not_mention)

**55. `u_figma`** (unrelated, bullet)
- Pin: term "figma", statement "Never mention Figma"; Jev is asked about: "Figma"
- Text: "Ran weekly lab sections for 30 students on Git, Linux, and testing practice."
- Label: `does_not_mention`. Nothing to do with it.
- Jev: score 0.01 (does_not_mention)

**56. `u_gamedev`** (unrelated, bullet)
- Pin: term "game development", statement "Never mention game development"; Jev is asked about: "game development"
- Text: "Migrated 3 lab pipelines from cron scripts to Airflow, with retries and alerting."
- Label: `does_not_mention`. Nothing to do with it.
- Jev: score 0.01 (does_not_mention)

**57. `u_ios`** (unrelated, bullet)
- Pin: term "ios development", statement "Never mention iOS development"; Jev is asked about: "iOS development"
- Text: "Cut a checkout page's p95 render from 1.8s to 600ms by batching 5 sequential API calls."
- Label: `does_not_mention`. Nothing to do with it.
- Jev: score 0.01 (does_not_mention)

**58. `u_k8s`** (unrelated, bullet)
- Pin: term "kubernetes", statement "Never mention Kubernetes"; Jev is asked about: "Kubernetes"
- Text: "Wrote an evaluation set of 200 graded questions that gated every prompt change."
- Label: `does_not_mention`. Nothing to do with it.
- Jev: score 0.01 (does_not_mention)

**59. `u_military`** (unrelated, bullet)
- Pin: term "military service", statement "Never mention military service"; Jev is asked about: "military service"
- Text: "Wrote evaluation tooling that produced per-class confusion matrices for 3 papers under review."
- Label: `does_not_mention`. Nothing to do with it.
- Jev: score 0.01 (does_not_mention)

**60. `u_name_field`** (unrelated, name)
- Pin: term "stripe", statement "Never mention Stripe"; Jev is asked about: "Stripe"
- Text: "Path Finder"
- Label: `does_not_mention`. Nothing to do with it.
- Jev: score 0.05 (does_not_mention)

**61. `u_rust`** (unrelated, bullet)
- Pin: term "rust", statement "Never mention Rust"; Jev is asked about: "Rust"
- Text: "Graded and gave written feedback on 120 data-structures assignments a term."
- Label: `does_not_mention`. Nothing to do with it.
- Jev: score 0.02 (does_not_mention)

**62. `u_salesforce`** (unrelated, bullet)
- Pin: term "salesforce", statement "Never mention Salesforce"; Jev is asked about: "Salesforce"
- Text: "Loaded 1.4TB of sensor archives into Postgres and cut a common query from 90 seconds to 4."
- Label: `does_not_mention`. Nothing to do with it.
- Jev: score 0.01 (does_not_mention)

**63. `g_backend_frontend`** (negation, bullet)
- Pin: term "backend work", statement "Never mention backend work; I'm going for frontend roles, not backend ones"; Jev is asked about: "backend work"
- Text: "Built a React and TypeScript scheduling tool that replaced a paper process for 12 departments."
- Label: `does_not_mention`. The bullet is frontend work, which the statement is asking for.
- Jev: score 0.07 (does_not_mention)

**64. `g_cloud_storage`** (negation, bullet)
- Pin: term "cloud storage", statement "Skip cloud storage, it's not relevant to data engineering roles"; Jev is asked about: "cloud storage"
- Text: "Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command."
- Label: `mentions`. S3 buckets are cloud storage.
- Jev: score 0.98 (mentions)

**65. `g_deep_classical`** (negation, bullet)
- Pin: term "deep learning", statement "Do not describe this as deep learning, it was classical ML"; Jev is asked about: "deep learning"
- Text: "Trained a PyTorch sequence model on 1.1M clinical events, improving readmission recall by 12 points over the baseline."
- Label: `mentions`. A PyTorch sequence model is deep learning, whatever the statement says.
- Jev: score 0.97 (mentions)

**66. `g_fabricated`** (negation, bullet)
- Pin: term "fabricated answers", statement "Don't mention fabricated answers, focus on retrieval quality instead"; Jev is asked about: "fabricated answers"
- Text: "Reduced hallucination reports 30% by adding grounded citation rendering to the response path."
- Label: `mentions`. Hallucination reports are about fabricated answers.
- Jev: score 0.95 (mentions)

**67. `g_finetune_retrieval`** (negation, bullet)
- Pin: term "fine-tuning", statement "Nothing about fine-tuning; my work is retrieval, not training"; Jev is asked about: "fine-tuning"
- Text: "Built a RAG pipeline over 120k support documents with pgvector, lifting answer accuracy from 61% to 79%."
- Label: `does_not_mention`. The bullet is retrieval work, which the statement says it is not about.
- Jev: score 0.03 (does_not_mention)

**68. `g_java_go`** (negation, bullet)
- Pin: term "java", statement "Never mention Java, I want to come across as a Go developer, not a Java one"; Jev is asked about: "java"
- Text: "Built a Go service that replaced 3 cron jobs, processing 200k records an hour."
- Label: `does_not_mention`. The statement names Go, which the bullet uses; the pinned topic is Java.
- Jev: score 0.02 (does_not_mention)

**69. `g_java_spring`** (negation, bullet)
- Pin: term "java", statement "Don't say I know Java, I'd rather be seen as a JavaScript developer"; Jev is asked about: "java"
- Text: "Shipped 14 REST endpoints in Spring Boot backing a customer portal used by 8,000 accounts."
- Label: `mentions`. Spring Boot is a Java framework.
- Jev: score 0.91 (mentions)

**70. `g_kafka_less`** (negation, bullet)
- Pin: term "kafka", statement "Don't talk about Kafka, I used it far less than Airflow"; Jev is asked about: "kafka"
- Text: "Migrated 3 lab pipelines from cron scripts to Airflow, with retries and alerting."
- Label: `does_not_mention`. The statement names Airflow as the thing used more; the pinned topic is Kafka.
- Jev: score 0.01 (does_not_mention)

**71. `g_ml_analytics`** (negation, bullet)
- Pin: term "machine learning", statement "I don't want machine learning on this one, keep it analytics only"; Jev is asked about: "machine learning"
- Text: "Trained a gradient-boosted retention model on 22,000 student records, beating the prior heuristic by 14 points of recall."
- Label: `mentions`. A gradient-boosted model is machine learning.
- Jev: score 0.98 (mentions)

**72. `g_qa`** (negation, bullet)
- Pin: term "quality assurance", statement "Don't mention quality assurance, I'm applying as a developer rather than a tester"; Jev is asked about: "quality assurance"
- Text: "Wrote Cypress end-to-end tests covering the 5 highest-traffic user flows."
- Label: `mentions`. End-to-end tests are quality-assurance work.
- Jev: score 0.97 (mentions)

**73. `g_tf_only_pytorch`** (negation, bullet)
- Pin: term "tensorflow", statement "Never mention TensorFlow; I only worked in PyTorch"; Jev is asked about: "tensorflow"
- Text: "Trained a PyTorch sequence model on 1.1M clinical events, improving readmission recall by 12 points over the baseline."
- Label: `does_not_mention`. The statement names PyTorch, which the bullet uses, but the pinned topic is TensorFlow.
- Jev: score 0.02 (does_not_mention)

**74. `g_user_studies`** (negation, bullet)
- Pin: term "user studies", statement "No user studies, I did benchmarks not surveys"; Jev is asked about: "user studies"
- Text: "Ran a 3-model comparison on a 400-question benchmark and published the scoring rubric."
- Label: `does_not_mention`. A benchmark comparison is not a user study.
- Jev: score 0.05 (does_not_mention)

