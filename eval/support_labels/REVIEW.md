# Support-check labels for review (issue #237)

Each pair is one revised bullet, the cited evidence it must be supported by, and the label the user confirmed for it.`supported`: every claim is stated or directly implied by the evidence. `adds_unsupported`: the bullet claims something the evidence does not state (a larger role, a scope, a tool, an outcome). `contradicts`: the evidence says otherwise. The `original` is the bullet being revised and is context only, never evidence. Numbers are the regex gate's business (#123), so pairs that touch one are tagged `numbers`.

These are the labels the user confirmed (2026-09-29), kept for audit; to change one, correct `label` in `pairs.json` and re-run `python eval/fit_support_threshold.py analyze` to refit. Disagreements with Jev come first.

## Disagreements with Jev (3)

**1. `bd_designed_and_built`** (boundary)
- Evidence: "Built a Postgres reporting schema serving 3 dashboards for the circulation team."
- Original: "Built a Postgres reporting schema serving 3 dashboards for the circulation team."
- Bullet: "Designed and built a Postgres reporting schema serving 3 dashboards for the circulation team."
- Label: `supported`. Building a schema implies designing it: a reasonable implication, not a new claim.
- Jev: `adds_unsupported` (sup 0.23 / adds 0.77 / contra 0.00)

**2. `bd_hundreds_of_runs`** (boundary) `numbers`
- Evidence: "Tracked 60 experiment runs in MLflow and wrote the comparison that selected the shipped checkpoint."
- Original: "Tracked 60 experiment runs in MLflow and wrote the comparison that selected the shipped checkpoint."
- Bullet: "Tracked hundreds of experiment runs in MLflow."
- Label: `contradicts`. 60 runs is not hundreds; the quantifier overstates the number (touches a number).
- Jev: `adds_unsupported` (sup 0.00 / adds 0.61 / contra 0.39)

**3. `contra_r_python`** (contradiction)
- Evidence: "Fit mixed-effects models in R to estimate course-level grade variation across 38 sections."
- Original: none
- Bullet: "Fit mixed-effects models in Python with statsmodels to estimate course-level grade variation across 38 sections."
- Label: `contradicts`. The cite says R; a different stack is a contradiction rather than an addition.
- Jev: `adds_unsupported` (sup 0.00 / adds 0.51 / contra 0.49)

## Agreements (86)

**4. `bd_ci_every_pr`** (boundary)
- Evidence: "Added GitHub Actions CI that ran 220 tests on every pull request."
- Original: none
- Bullet: "Automated testing on every pull request through GitHub Actions CI."
- Label: `supported`. CI that runs tests on each PR is automated testing on each PR.
- Jev: `supported` (sup 0.97 / adds 0.03 / contra 0.00)

**5. `bd_collaboration_review`** (boundary)
- Evidence: "Reviewed pull requests from the rest of the team and kept the CI pipeline green."
- Original: none
- Bullet: "Collaborated with teammates through code review."
- Label: `supported`. Reviewing teammates' pull requests is collaboration through review.
- Jev: `supported` (sup 0.95 / adds 0.05 / contra 0.00)

**6. `bd_cypress_browser_tests`** (boundary)
- Evidence: "Wrote Cypress end-to-end tests covering the 5 highest-traffic user flows."
- Original: "Wrote Cypress end-to-end tests covering the 5 highest-traffic user flows."
- Bullet: "Wrote automated browser tests for the 5 highest-traffic user flows."
- Label: `supported`. Cypress end-to-end tests are automated browser tests.
- Jev: `supported` (sup 0.91 / adds 0.09 / contra 0.00)

**7. `bd_deep_learning_sklearn`** (boundary)
- Evidence: "Built a claims severity model in scikit-learn and compared it against the existing actuarial baseline."
- Original: "Built a claims severity model in scikit-learn and compared it against the existing actuarial baseline."
- Bullet: "Built a deep learning claims severity model and compared it against the actuarial baseline."
- Label: `adds_unsupported`. A scikit-learn model is not evidence of deep learning; a specific method the cite does not support.
- Jev: `adds_unsupported` (sup 0.00 / adds 0.69 / contra 0.31)

**8. `bd_dozens_of_runs`** (boundary) `numbers`
- Evidence: "Tracked 60 experiment runs in MLflow and wrote the comparison that selected the shipped checkpoint."
- Original: none
- Bullet: "Tracked dozens of experiment runs in MLflow."
- Label: `supported`. 60 runs is dozens; a vague quantifier that stays true.
- Jev: `supported` (sup 0.96 / adds 0.04 / contra 0.00)

**9. `bd_hybrid_search_synonym`** (boundary)
- Evidence: "Implemented hybrid BM25 + dense retrieval with reciprocal rank fusion."
- Original: none
- Bullet: "Implemented hybrid search combining keyword and vector retrieval."
- Label: `supported`. BM25 is keyword retrieval and dense retrieval is vector retrieval.
- Jev: `supported` (sup 0.98 / adds 0.02 / contra 0.00)

**10. `bd_monitoring_platform`** (boundary)
- Evidence: "Added drift monitoring in Grafana that caught a 7% input distribution shift within a day."
- Original: none
- Bullet: "Built a Grafana-based model monitoring platform that detects model drift."
- Label: `adds_unsupported`. 'Platform' claims a larger system than adding drift monitoring.
- Jev: `adds_unsupported` (sup 0.15 / adds 0.85 / contra 0.00)

**11. `bd_original_inflated_bullet_clean`** (boundary)
- Evidence: "Wrote an evaluation set of 200 graded questions that gated every prompt change."
- Original: "Built and owned the evaluation platform for the AI team, gating every prompt change."
- Bullet: "Built the evaluation set that gated every prompt change."
- Label: `supported`. The original overclaimed ('platform', 'owned'); the bullet itself claims only what the cite says.
- Jev: `supported` (sup 0.73 / adds 0.26 / contra 0.01)

**12. `bd_original_not_evidence`** (boundary) `negation`
- Evidence: "Documented the deployment steps so the next intern could run a release unaided."
- Original: "Automated the release process so the next intern could deploy unaided."
- Bullet: "Automated the release process so the next intern could deploy unaided."
- Label: `adds_unsupported`. The original (not evidence) says 'automated'; the cite says only documented.
- Jev: `adds_unsupported` (sup 0.01 / adds 0.85 / contra 0.14)

**13. `bd_pytorch_deep_learning`** (boundary)
- Evidence: "Trained a PyTorch sequence model on 1.1M clinical events, improving readmission recall by 12 points over the baseline."
- Original: none
- Bullet: "Trained a PyTorch deep learning sequence model on 1.1M clinical events, improving readmission recall by 12 points over the baseline."
- Label: `supported`. A PyTorch sequence model is a deep learning model; a generic synonym.
- Jev: `supported` (sup 0.88 / adds 0.12 / contra 0.00)

**14. `bd_seaborn_python`** (boundary)
- Evidence: "Wrote a seaborn plotting toolkit reused by 5 graduate researchers for thesis figures."
- Original: "Wrote a seaborn plotting toolkit reused by 5 graduate researchers for thesis figures."
- Bullet: "Built a Python plotting toolkit adopted by graduate researchers for thesis figures."
- Label: `supported`. Seaborn is a Python library; reused by researchers is adopted by them.
- Jev: `supported` (sup 0.67 / adds 0.32 / contra 0.01)

**15. `bd_shipped_deployed`** (boundary)
- Evidence: "Shipped a retrieval service over 2M documents with pgvector and Redis caching at 120ms p95."
- Original: "Shipped a retrieval service over 2M documents with pgvector and Redis caching at 120ms p95."
- Bullet: "Deployed a retrieval service over 2M documents with pgvector and Redis caching at 120ms p95."
- Label: `supported`. Shipped and deployed are near-synonyms for a service with a latency figure.
- Jev: `supported` (sup 0.94 / adds 0.05 / contra 0.01)

**16. `bd_specific_algorithm`** (boundary)
- Evidence: "Explored policyholder segments with clustering and summarized what distinguished them for the pricing team."
- Original: "Explored policyholder segments with clustering and summarized what distinguished them for the pricing team."
- Bullet: "Segmented policyholders with k-means clustering and summarized what distinguished them for the pricing team."
- Label: `adds_unsupported`. 'Clustering' does not name k-means.
- Jev: `adds_unsupported` (sup 0.04 / adds 0.96 / contra 0.00)

**17. `rw_dependency_free`** (boundary) `negation`
- Evidence: "TypeScript library for building validated forms with no runtime dependency."
- Original: "TypeScript library for building validated forms with no runtime dependency."
- Bullet: "Built a dependency-free TypeScript library for validated forms."
- Label: `adds_unsupported`. Dropping 'runtime' widens the claim: 'dependency-free' also covers dev and build dependencies, which the cite does not.
- Jev: `adds_unsupported` (sup 0.18 / adds 0.82 / contra 0.00)

**18. `contra_api_key`** (contradiction) `negation`
- Evidence: "Runs locally with SQLite and a small embedding model, with no API key required."
- Original: "Runs locally with SQLite and a small embedding model, with no API key required."
- Bullet: "Runs on a hosted vector database and requires an OpenAI API key."
- Label: `contradicts`. The cite says local with no API key required.
- Jev: `contradicts` (sup 0.00 / adds 0.01 / contra 0.99)

**19. `contra_cron_direction`** (contradiction)
- Evidence: "Migrated 3 lab pipelines from cron scripts to Airflow, with retries and alerting."
- Original: none
- Bullet: "Migrated 3 lab pipelines from Airflow back to cron scripts."
- Label: `contradicts`. The cite migrates from cron to Airflow, the reverse.
- Jev: `contradicts` (sup 0.00 / adds 0.00 / contra 1.00)

**20. `contra_flat_significant`** (contradiction)
- Evidence: "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically flat."
- Original: "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically flat."
- Bullet: "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically significant winners."
- Label: `contradicts`. The cite flags them as statistically flat, the opposite result.
- Jev: `contradicts` (sup 0.00 / adds 0.07 / contra 0.93)

**21. `contra_go_rust`** (contradiction)
- Evidence: "Built a Go service that replaced 3 cron jobs, processing 200k records an hour."
- Original: "Built a Go service that replaced 3 cron jobs, processing 200k records an hour."
- Bullet: "Built a Rust service that replaced 3 cron jobs, processing 200k records an hour."
- Label: `contradicts`. The cite says Go.
- Jev: `contradicts` (sup 0.00 / adds 0.13 / contra 0.87)

**22. `contra_image_text`** (contradiction)
- Evidence: "Fine-tuned 4 image classification backbones on a 40k-image microscopy set, reaching 91% top-1."
- Original: "Fine-tuned 4 image classification backbones on a 40k-image microscopy set, reaching 91% top-1."
- Bullet: "Fine-tuned 4 text classification backbones on a 40k-document corpus, reaching 91% top-1."
- Label: `contradicts`. The cite says image classification on microscopy images.
- Jev: `contradicts` (sup 0.00 / adds 0.40 / contra 0.60)

**23. `contra_java_python`** (contradiction)
- Evidence: "Implemented REST endpoints in Java and Spring Boot for the account management service."
- Original: "Implemented REST endpoints in Java and Spring Boot for the account management service."
- Bullet: "Implemented REST endpoints in Python and Django for the account management service."
- Label: `contradicts`. The cite says Java and Spring Boot.
- Jev: `contradicts` (sup 0.00 / adds 0.01 / contra 0.99)

**24. `contra_kafka_rabbitmq`** (contradiction)
- Evidence: "Shipped a Kafka consumer in Scala that backfilled 3 weeks of missing click events."
- Original: none
- Bullet: "Shipped a RabbitMQ consumer in Scala that backfilled 3 weeks of missing click events."
- Label: `contradicts`. The cite says Kafka, not RabbitMQ.
- Jev: `contradicts` (sup 0.00 / adds 0.08 / contra 0.92)

**25. `contra_mongo_postgres`** (contradiction)
- Evidence: "Parses 50k PGN games into MongoDB and reports opening win rates by rating band."
- Original: none
- Bullet: "Parses 50k PGN games into PostgreSQL and reports opening win rates by rating band."
- Label: `contradicts`. The cite says MongoDB.
- Jev: `contradicts` (sup 0.00 / adds 0.06 / contra 0.94)

**26. `contra_offline_remote`** (contradiction) `negation`
- Evidence: "Caches GTFS schedule data in Parquet and serves lookups without a network call."
- Original: none
- Bullet: "Caches GTFS schedule data in Parquet and serves lookups through a remote API call."
- Label: `contradicts`. The cite says lookups need no network call.
- Jev: `contradicts` (sup 0.00 / adds 0.00 / contra 1.00)

**27. `contra_replay_overwrites`** (contradiction) `negation`
- Evidence: "Loads 500k-row vendor drops into Postgres with content hashing, so a replayed file is a no-op."
- Original: none
- Bullet: "Loads 500k-row vendor drops into Postgres, where replaying a file overwrites earlier rows."
- Label: `contradicts`. The cite says a replayed file is a no-op.
- Jev: `contradicts` (sup 0.03 / adds 0.06 / contra 0.91)

**28. `contra_rewrites_draft`** (contradiction) `negation`
- Evidence: "Built a LangChain assistant that suggests revisions without rewriting a student's draft."
- Original: none
- Bullet: "Built a LangChain assistant that rewrites students' drafts to improve them."
- Label: `contradicts`. The cite says it suggests revisions without rewriting the draft.
- Jev: `contradicts` (sup 0.00 / adds 0.03 / contra 0.97)

**29. `contra_wrote_reviewed`** (contradiction)
- Evidence: "Wrote a Terraform stack provisioning the team's SageMaker training environment."
- Original: "Wrote a Terraform stack provisioning the team's SageMaker training environment."
- Bullet: "Reviewed a SageMaker training environment Terraform stack written by another engineer."
- Label: `contradicts`. The cite says the candidate wrote the stack.
- Jev: `contradicts` (sup 0.01 / adds 0.38 / contra 0.61)

**30. `rw_airflow_maintained`** (faithful_rewording)
- Evidence: "Maintained the Airflow pipelines that load carrier invoices into the Snowflake warehouse."
- Original: "Maintained the Airflow pipelines that load carrier invoices into the Snowflake warehouse."
- Bullet: "Maintained the Airflow pipelines that ingest carrier invoices into the Snowflake data warehouse."
- Label: `supported`. Load/ingest and warehouse/data warehouse are synonyms.
- Jev: `supported` (sup 0.92 / adds 0.08 / contra 0.00)

**31. `rw_autograder`** (faithful_rewording)
- Evidence: "Built a Python autograder that cut assignment turnaround from 4 days to 1."
- Original: none
- Bullet: "Developed a Python autograder that shortened assignment turnaround from 4 days to 1."
- Label: `supported`. Built/developed, cut/shortened; same facts.
- Jev: `supported` (sup 0.99 / adds 0.01 / contra 0.00)

**32. `rw_confusion_matrices`** (faithful_rewording)
- Evidence: "Wrote evaluation tooling that produced per-class confusion matrices for 3 papers under review."
- Original: "Wrote evaluation tooling that produced per-class confusion matrices for 3 papers under review."
- Bullet: "Built evaluation tooling that generated per-class confusion matrices for 3 papers under review."
- Label: `supported`. Wrote/built, produced/generated.
- Jev: `supported` (sup 0.93 / adds 0.07 / contra 0.00)

**33. `rw_dedupe_negation`** (faithful_rewording) `negation`
- Evidence: "Handles duplicate transactions by hashing the source row rather than trusting file order."
- Original: none
- Bullet: "Deduplicates transactions by hashing the source row instead of relying on file order."
- Label: `supported`. 'Rather than trusting file order' restated as 'instead of relying on file order'.
- Jev: `supported` (sup 0.97 / adds 0.03 / contra 0.00)

**34. `rw_draft_untouched`** (faithful_rewording) `negation`
- Evidence: "Built a LangChain assistant that suggests revisions without rewriting a student's draft."
- Original: none
- Bullet: "Built a LangChain assistant that gives revision suggestions while leaving the student's draft untouched."
- Label: `supported`. 'Without rewriting the draft' restated as 'leaving the draft untouched'.
- Jev: `supported` (sup 1.00 / adds 0.00 / contra 0.00)

**35. `rw_fastapi_endpoint`** (faithful_rewording)
- Evidence: "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms."
- Original: "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms."
- Bullet: "Deployed the model as a FastAPI endpoint in a Docker container, keeping p95 latency below 80ms."
- Label: `supported`. Packaged behind an endpoint in Docker is deploying it as one; under/below.
- Jev: `supported` (sup 0.92 / adds 0.08 / contra 0.00)

**36. `rw_gtfs_years`** (faithful_rewording)
- Evidence: "Ingested 2 years of GTFS feeds into BigQuery and modeled on-time performance across 190 routes."
- Original: none
- Bullet: "Loaded two years of GTFS feeds into BigQuery and modeled on-time performance for 190 routes."
- Label: `supported`. 2 years written as 'two years'; otherwise a paraphrase.
- Jev: `supported` (sup 0.93 / adds 0.06 / contra 0.01)

**37. `rw_mlflow_runs`** (faithful_rewording)
- Evidence: "Tracked 60 experiment runs in MLflow and wrote the comparison that selected the shipped checkpoint."
- Original: none
- Bullet: "Logged 60 experiment runs in MLflow and wrote the comparison used to choose the shipped checkpoint."
- Label: `supported`. Tracked/logged, selected/used to choose.
- Jev: `supported` (sup 0.96 / adds 0.03 / contra 0.01)

**38. `rw_offline_lookup`** (faithful_rewording) `negation`
- Evidence: "Caches GTFS schedule data in Parquet and serves lookups without a network call."
- Original: "Caches GTFS schedule data in Parquet and serves lookups without a network call."
- Bullet: "Serves transit schedule lookups offline from a Parquet cache."
- Label: `supported`. 'Without a network call' is 'offline'; GTFS is transit schedule data.
- Jev: `supported` (sup 0.88 / adds 0.12 / contra 0.00)

**39. `rw_pandas_features`** (faithful_rewording)
- Evidence: "Wrote a reusable pandas feature pipeline for lag and rolling-window terms."
- Original: none
- Bullet: "Created a reusable pandas feature pipeline covering lag and rolling-window terms."
- Label: `supported`. Wrote/created; for/covering.
- Jev: `supported` (sup 0.99 / adds 0.01 / contra 0.00)

**40. `rw_pytorch_ranking`** (faithful_rewording)
- Evidence: "Trained ranking models in PyTorch and compared them against the existing heuristic ordering."
- Original: "Trained ranking models in PyTorch and compared them against the existing heuristic ordering."
- Bullet: "Trained PyTorch ranking models and evaluated them against the current heuristic ordering."
- Label: `supported`. Compared against/evaluated against; existing/current.
- Jev: `supported` (sup 0.94 / adds 0.06 / contra 0.00)

**41. `rw_rag_expand`** (faithful_rewording)
- Evidence: "Built a RAG pipeline over 120k support documents with pgvector, lifting answer accuracy from 61% to 79%."
- Original: "Built a RAG pipeline over 120k support documents with pgvector, lifting answer accuracy from 61% to 79%."
- Bullet: "Built a retrieval-augmented generation pipeline over 120k support documents using pgvector, raising answer accuracy from 61% to 79%."
- Label: `supported`. RAG spelled out; every other claim and number is unchanged.
- Jev: `supported` (sup 0.99 / adds 0.01 / contra 0.00)

**42. `rw_react_dashboard`** (faithful_rewording)
- Evidence: "Built an internal React dashboard that replaced a spreadsheet workflow for the help desk."
- Original: "Built an internal React dashboard that replaced a spreadsheet workflow for the help desk."
- Bullet: "Created an internal React dashboard for the help desk in place of a spreadsheet-based workflow."
- Label: `supported`. Same claim, reordered.
- Jev: `supported` (sup 0.99 / adds 0.01 / contra 0.00)

**43. `rw_replay_noop`** (faithful_rewording) `negation`
- Evidence: "Loads 500k-row vendor drops into Postgres with content hashing, so a replayed file is a no-op."
- Original: none
- Bullet: "Loads 500k-row vendor files into Postgres with content hashing, so replaying a file changes nothing."
- Label: `supported`. 'A replayed file is a no-op' restated as 'replaying changes nothing'.
- Jev: `supported` (sup 0.89 / adds 0.10 / contra 0.01)

**44. `rw_token_spend`** (faithful_rewording)
- Evidence: "Cut token spend 40% by adding a prompt cache and trimming 3 redundant context blocks."
- Original: none
- Bullet: "Reduced token spend by 40% through a prompt cache and by removing 3 redundant context blocks."
- Label: `supported`. Cut/trimmed to reduced/removing; same facts.
- Jev: `supported` (sup 0.94 / adds 0.06 / contra 0.00)

**45. `weave_airflow_kafka`** (grounded_weave)
- Evidence: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Original: "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery."
- Bullet: "Built data pipelines with Airflow and Kafka, loading shipment events into Snowflake at 99.9% delivery."
- Label: `supported`. Airflow, Kafka and Snowflake are all in the cite; 'data pipelines' is the generic term for DAGs.
- Jev: `supported` (sup 0.92 / adds 0.08 / contra 0.00)

**46. `weave_dbt_snowflake`** (grounded_weave)
- Evidence: "Shipped a dbt model layer over Snowflake that consolidated 6 reporting pipelines into 1 tested source."
- Original: "Shipped a dbt model layer over Snowflake that consolidated 6 reporting pipelines into 1 tested source."
- Bullet: "Built a dbt model layer on Snowflake, consolidating reporting pipelines into one tested source."
- Label: `supported`. dbt, Snowflake and the consolidation are all stated.
- Jev: `supported` (sup 0.95 / adds 0.05 / contra 0.00)

**47. `weave_fastapi_postgres_qa`** (grounded_weave)
- Evidence: "Answers questions over 800 hours of transcripts with timestamped citations." / "Runs on FastAPI and Postgres with a React player integration."
- Original: none
- Bullet: "Built a FastAPI and Postgres Q&A service over 800 hours of lecture transcripts with timestamped citations."
- Label: `supported`. FastAPI and Postgres come from the project's second cited bullet; the rest from the first.
- Jev: `supported` (sup 0.87 / adds 0.13 / contra 0.00)

**48. `weave_junit_contract`** (grounded_weave)
- Evidence: "Shipped 14 REST endpoints in Spring Boot backing a customer portal used by 8,000 accounts." / "Raised service test coverage from 46% to 78% with JUnit and contract tests."
- Original: none
- Bullet: "Shipped Spring Boot REST endpoints and raised service test coverage from 46% to 78% with JUnit and contract tests."
- Label: `supported`. Two bullets of the same role; Spring Boot, REST, JUnit and the coverage are all cited.
- Jev: `supported` (sup 0.99 / adds 0.01 / contra 0.00)

**49. `weave_kafka_grafana`** (grounded_weave)
- Evidence: "Shipped a Kafka feature stream feeding 3 production models with sub-minute freshness." / "Added drift monitoring in Grafana that caught a 7% input distribution shift within a day."
- Original: none
- Bullet: "Shipped a Kafka feature stream feeding 3 production models and added Grafana drift monitoring."
- Label: `supported`. Both bullets of the role are cited and the bullet just joins them.
- Jev: `supported` (sup 0.99 / adds 0.01 / contra 0.00)

**50. `weave_mlflow_pytorch`** (grounded_weave)
- Evidence: "Ran fine-tuning experiments with scikit-learn and PyTorch and logged the results in MLflow."
- Original: "Ran fine-tuning experiments with scikit-learn and PyTorch and logged the results in MLflow."
- Bullet: "Ran PyTorch fine-tuning experiments and tracked the results in MLflow."
- Label: `supported`. PyTorch and MLflow are both cited.
- Jev: `supported` (sup 1.00 / adds 0.00 / contra 0.00)

**51. `weave_pgvector_search`** (grounded_weave)
- Evidence: "Shipped a retrieval service over 2M documents with pgvector and Redis caching at 120ms p95."
- Original: none
- Bullet: "Shipped a vector-search retrieval service on pgvector with Redis caching over 2M documents."
- Label: `supported`. pgvector implies vector search; Redis and 2M documents are in the cite.
- Jev: `supported` (sup 0.98 / adds 0.02 / contra 0.00)

**52. `weave_sklearn_pytorch`** (grounded_weave)
- Evidence: "Trained demand models in scikit-learn and PyTorch on 300k transactions for 2 campus partners."
- Original: "Trained demand models in scikit-learn and PyTorch on 300k transactions for 2 campus partners."
- Bullet: "Trained demand models with scikit-learn and PyTorch on 300k transactions for two campus partners."
- Label: `supported`. Both libraries and the data are in the cite; 2 written as 'two'.
- Jev: `supported` (sup 0.98 / adds 0.02 / contra 0.00)

**53. `weave_statsmodels`** (grounded_weave)
- Evidence: "Built demand forecasts for 34 clinic sites in Python and statsmodels, reducing weekly schedule error by 19%."
- Original: none
- Bullet: "Built demand forecasts in Python with statsmodels for 34 clinic sites, cutting weekly schedule error by 19%."
- Label: `supported`. Python and statsmodels are cited; reducing/cutting.
- Jev: `supported` (sup 1.00 / adds 0.00 / contra 0.00)

**54. `weave_terraform_iac`** (grounded_weave)
- Evidence: "Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command."
- Original: none
- Bullet: "Provisioned S3 buckets and IAM roles as code with a Terraform module."
- Label: `supported`. 'As code' is what a Terraform module does; S3, IAM and Terraform are cited.
- Jev: `supported` (sup 0.98 / adds 0.02 / contra 0.00)

**55. `scope_deployed_replaced`** (invented_scope_outcome)
- Evidence: "Built a claims severity model in scikit-learn and compared it against the existing actuarial baseline."
- Original: "Built a claims severity model in scikit-learn and compared it against the existing actuarial baseline."
- Bullet: "Built and deployed a claims severity model in scikit-learn that replaced the actuarial baseline."
- Label: `adds_unsupported`. The evidence compared against the baseline; deployment and replacing it are not stated.
- Jev: `adds_unsupported` (sup 0.00 / adds 0.99 / contra 0.01)

**56. `scope_edge_fleet`** (invented_scope_outcome)
- Evidence: "Distilled a detector to 8MB for edge devices, retaining 88% of teacher mAP."
- Original: "Distilled a detector to 8MB for edge devices, retaining 88% of teacher mAP."
- Bullet: "Distilled a detector to 8MB and deployed it across a fleet of edge devices in production."
- Label: `adds_unsupported`. The evidence says sized for edge devices; a production fleet is not stated.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**57. `scope_end_to_end_deploy`** (invented_scope_outcome)
- Evidence: "Built LangChain agents that call 6 campus APIs and return grounded answers with citations."
- Original: "Built LangChain agents that call 6 campus APIs and return grounded answers with citations."
- Bullet: "Built and deployed LangChain agents end to end, from API integration to hosting, that answer student questions with citations."
- Label: `adds_unsupported`. Deployment and 'end to end' are not stated; the evidence says built.
- Jev: `adds_unsupported` (sup 0.01 / adds 0.99 / contra 0.00)

**58. `scope_high_availability`** (invented_scope_outcome)
- Evidence: "Ships a Docker Compose stack that stands the whole pipeline up in under 2 minutes."
- Original: none
- Bullet: "Ships a Docker Compose stack that deploys the pipeline with high availability and zero downtime."
- Label: `adds_unsupported`. High availability and zero downtime are not stated; the evidence says fast startup.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**59. `scope_hourly_refresh`** (invented_scope_outcome) `negation`
- Evidence: "Caches GTFS schedule data in Parquet and serves lookups without a network call."
- Original: none
- Bullet: "Caches GTFS schedule data in Parquet, refreshed hourly, and serves lookups without a network call."
- Label: `adds_unsupported`. The offline lookups are cited; the hourly refresh is invented.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**60. `scope_invented_impact`** (invented_scope_outcome)
- Evidence: "Rewrote a fragile shipment ingest job in Python, adding retries and structured logging."
- Original: none
- Bullet: "Rewrote a fragile shipment ingest job in Python, eliminating pipeline failures and saving significant engineering time."
- Label: `adds_unsupported`. Retries and logging do not establish eliminated failures or saved time.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**61. `scope_invented_users`** (invented_scope_outcome) `numbers`
- Evidence: "Built an internal React dashboard that replaced a spreadsheet workflow for the help desk."
- Original: "Built an internal React dashboard that replaced a spreadsheet workflow for the help desk."
- Bullet: "Built a React dashboard adopted by 200 help desk staff, replacing spreadsheets."
- Label: `adds_unsupported`. An adoption figure of 200 users is invented (touches a number, so #123's gate also fires).
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**62. `scope_no_internet`** (invented_scope_outcome) `negation`
- Evidence: "Runs locally with SQLite and a small embedding model, with no API key required."
- Original: none
- Bullet: "Runs locally with SQLite and a small embedding model, with no API key or internet connection required."
- Label: `adds_unsupported`. 'No API key' is cited; 'no internet' is an additional claim.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**63. `scope_outperforms`** (invented_scope_outcome)
- Evidence: "Classifies scanned lecture notes by course with a small convolutional network."
- Original: none
- Bullet: "Classifies scanned lecture notes by course with a convolutional network, outperforming commercial OCR tools."
- Label: `adds_unsupported`. An outperformance claim is invented.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**64. `scope_plagiarism_feature`** (invented_scope_outcome) `negation`
- Evidence: "Built a LangChain assistant that suggests revisions without rewriting a student's draft."
- Original: "Built a LangChain assistant that suggests revisions without rewriting a student's draft."
- Bullet: "Built a LangChain assistant that suggests revisions without rewriting a student's draft and flags plagiarism."
- Label: `adds_unsupported`. The non-rewriting claim is cited; plagiarism flagging is invented.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**65. `scope_production_org`** (invented_scope_outcome)
- Evidence: "Built an evaluation harness that reported offline metrics for every candidate checkpoint."
- Original: none
- Bullet: "Built a production evaluation harness used across the ML organization."
- Label: `adds_unsupported`. 'Production' and 'used across the organization' are not stated.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**66. `scope_team_size`** (invented_scope_outcome)
- Evidence: "Containerized the ingest job with Docker so it runs identically on 2 campus servers."
- Original: none
- Bullet: "Containerized the ingest job with Docker for a team of six engineers."
- Label: `adds_unsupported`. A team size is invented.
- Jev: `adds_unsupported` (sup 0.02 / adds 0.98 / contra 0.00)

**67. `scope_whole_corpus`** (invented_scope_outcome)
- Evidence: "Built a retrieval pipeline over the company handbook and wired it into an internal chat tool."
- Original: "Built a retrieval pipeline over the company handbook and wired it into an internal chat tool."
- Bullet: "Built a retrieval pipeline over the company's entire document corpus, serving the whole organization through an internal chat tool."
- Label: `adds_unsupported`. The evidence says the handbook and an internal chat tool; corpus-wide and organization-wide are larger.
- Jev: `adds_unsupported` (sup 0.00 / adds 0.89 / contra 0.11)

**68. `infl_built_led_migration`** (role_inflation)
- Evidence: "Built a Go service that replaced 3 cron jobs, processing 200k records an hour."
- Original: "Built a Go service that replaced 3 cron jobs, processing 200k records an hour."
- Bullet: "Led a migration of legacy cron jobs to Go services."
- Label: `adds_unsupported`. Built one Go service replacing cron jobs; 'led a migration' is a larger role and scope.
- Jev: `adds_unsupported` (sup 0.13 / adds 0.87 / contra 0.00)

**69. `infl_compared_architected`** (role_inflation)
- Evidence: "Compared several chunking strategies and recorded which produced grounded answers."
- Original: none
- Bullet: "Architected the chunking strategy for the company's retrieval pipeline."
- Label: `adds_unsupported`. The evidence compared strategies; it does not say the candidate architected one.
- Jev: `adds_unsupported` (sup 0.03 / adds 0.97 / contra 0.00)

**70. `infl_documented_owned`** (role_inflation)
- Evidence: "Documented the feature preparation steps so the team could rerun the study after I left."
- Original: "Documented the feature preparation steps so the team could rerun the study after I left."
- Bullet: "Owned feature preparation for the team's ranking study."
- Label: `adds_unsupported`. Documenting the steps is not owning the work.
- Jev: `adds_unsupported` (sup 0.19 / adds 0.81 / contra 0.00)

**71. `infl_implemented_owner`** (role_inflation)
- Evidence: "Implemented REST endpoints in Java and Spring Boot for the account management service." / "Fixed defects in the billing module and added JUnit coverage around the affected paths." / "Reviewed pull requests from the rest of the team and kept the CI pipeline green."
- Original: none
- Bullet: "Served as technical owner of the account management service."
- Label: `adds_unsupported`. Implementing endpoints, fixing defects and reviewing PRs is contribution, not ownership.
- Jev: `adds_unsupported` (sup 0.03 / adds 0.97 / contra 0.00)

**72. `infl_maintained_architected`** (role_inflation)
- Evidence: "Maintained the FastAPI service and the deployment notes the center's coordinators follow."
- Original: none
- Bullet: "Architected the FastAPI service the center's coordinators rely on."
- Label: `adds_unsupported`. 'Maintained' is not architecting.
- Jev: `adds_unsupported` (sup 0.00 / adds 0.99 / contra 0.01)

**73. `infl_maintained_owned`** (role_inflation)
- Evidence: "Maintained the Airflow pipelines that load carrier invoices into the Snowflake warehouse."
- Original: "Maintained the Airflow pipelines that load carrier invoices into the Snowflake warehouse."
- Bullet: "Owned the Airflow pipelines that load carrier invoices into the Snowflake warehouse."
- Label: `adds_unsupported`. 'Maintained' claims upkeep, not ownership.
- Jev: `adds_unsupported` (sup 0.04 / adds 0.95 / contra 0.01)

**74. `infl_partnered_led`** (role_inflation)
- Evidence: "Partnered with 3 product teams on an A/B testing framework now used for every release readout."
- Original: "Partnered with 3 product teams on an A/B testing framework now used for every release readout."
- Bullet: "Led the A/B testing framework now used for every release readout."
- Label: `adds_unsupported`. 'Partnered with 3 product teams' is collaboration, not leadership.
- Jev: `adds_unsupported` (sup 0.01 / adds 0.96 / contra 0.03)

**75. `infl_ran_directed`** (role_inflation)
- Evidence: "Ran weekly lab sections for 30 students on Git, Linux, and testing practice."
- Original: "Ran weekly lab sections for 30 students on Git, Linux, and testing practice."
- Bullet: "Directed the department's weekly lab program on Git, Linux, and testing practice."
- Label: `adds_unsupported`. 'Ran weekly lab sections' is teaching them, not directing a program.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**76. `infl_reviewed_led`** (role_inflation)
- Evidence: "Reviewed pull requests from the rest of the team and kept the CI pipeline green."
- Original: none
- Bullet: "Led code review for the engineering team and owned the CI pipeline."
- Label: `adds_unsupported`. Reviewing PRs and keeping CI green is participation, not leading review or owning CI.
- Jev: `adds_unsupported` (sup 0.11 / adds 0.88 / contra 0.01)

**77. `infl_tests_authored`** (role_inflation)
- Evidence: "Ran hypothesis tests comparing treatment and control groups and drafted the results section."
- Original: "Ran hypothesis tests comparing treatment and control groups and drafted the results section."
- Bullet: "Led the statistical analysis for the study and authored its results."
- Label: `adds_unsupported`. Ran tests and drafted a section; did not lead the analysis or author the results.
- Jev: `adds_unsupported` (sup 0.12 / adds 0.88 / contra 0.00)

**78. `infl_wrote_designed`** (role_inflation)
- Evidence: "Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command."
- Original: none
- Bullet: "Designed the research group's cloud infrastructure architecture in Terraform."
- Label: `adds_unsupported`. Writing a module is not designing the group's architecture.
- Jev: `adds_unsupported` (sup 0.23 / adds 0.77 / contra 0.00)

**79. `tool_airflow_added`** (ungrounded_tool)
- Evidence: "Automated catalog exports with Python and Bash, replacing a 5-step manual process."
- Original: none
- Bullet: "Automated catalog exports with Python, Bash and Airflow, replacing a 5-step manual process."
- Label: `adds_unsupported`. Airflow is not in the cite.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**80. `tool_airflow_elsewhere`** (ungrounded_tool)
- Evidence: "Replaced a spreadsheet handoff with a scheduled export written in Python."
- Original: none
- Bullet: "Replaced a spreadsheet handoff with a scheduled Airflow export written in Python."
- Label: `adds_unsupported`. Airflow appears in another role of the same profile, but not in the cited bullet.
- Jev: `adds_unsupported` (sup 0.01 / adds 0.99 / contra 0.00)

**81. `tool_dbt_added`** (ungrounded_tool)
- Evidence: "Parsed 3,100 county result files with pandas and Spark, reconciling 5 conflicting schemas."
- Original: none
- Bullet: "Parsed 3,100 county result files with pandas, Spark and dbt, reconciling 5 conflicting schemas."
- Label: `adds_unsupported`. dbt is not in the cite.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**82. `tool_kubernetes`** (ungrounded_tool)
- Evidence: "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms."
- Original: "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms."
- Bullet: "Packaged the model behind a FastAPI endpoint on Kubernetes, holding p95 latency under 80ms."
- Label: `adds_unsupported`. Kubernetes is not in the cite (Docker is).
- Jev: `adds_unsupported` (sup 0.00 / adds 0.98 / contra 0.02)

**83. `tool_langchain_added`** (ungrounded_tool)
- Evidence: "Answers questions over 800 hours of transcripts with timestamped citations."
- Original: "Answers questions over 800 hours of transcripts with timestamped citations."
- Bullet: "Built a LangChain application that answers questions over 800 hours of transcripts with timestamped citations."
- Label: `adds_unsupported`. LangChain is not in the cited project bullet (it is in another item).
- Jev: `adds_unsupported` (sup 0.08 / adds 0.92 / contra 0.00)

**84. `tool_redis_added`** (ungrounded_tool)
- Evidence: "Next.js and Postgres app that settles group expenses in the fewest transfers."
- Original: "Next.js and Postgres app that settles group expenses in the fewest transfers."
- Bullet: "Built a Next.js, Postgres and Redis app that settles group expenses in the fewest transfers."
- Label: `adds_unsupported`. Redis is not in the cite.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**85. `tool_redis_cache`** (ungrounded_tool)
- Evidence: "Cut token spend 40% by adding a prompt cache and trimming 3 redundant context blocks."
- Original: none
- Bullet: "Cut token spend 40% by adding a Redis prompt cache and trimming 3 redundant context blocks."
- Label: `adds_unsupported`. The cache technology (Redis) is not named in the cite.
- Jev: `adds_unsupported` (sup 0.03 / adds 0.97 / contra 0.00)

**86. `tool_tensorrt`** (ungrounded_tool)
- Evidence: "Exported to ONNX and benchmarked 3 runtimes on a Raspberry Pi."
- Original: "Exported to ONNX and benchmarked 3 runtimes on a Raspberry Pi."
- Bullet: "Exported to ONNX and TensorRT and benchmarked 3 runtimes on a Raspberry Pi."
- Label: `adds_unsupported`. TensorRT is not in the cite.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**87. `tool_terraform_added`** (ungrounded_tool)
- Evidence: "Containerized the ingest job with Docker so it runs identically on 2 campus servers."
- Original: "Containerized the ingest job with Docker so it runs identically on 2 campus servers."
- Bullet: "Containerized the ingest job with Docker and Terraform so it runs identically on 2 campus servers."
- Label: `adds_unsupported`. Terraform is not in the cite; it belongs to a different project.
- Jev: `adds_unsupported` (sup 0.00 / adds 1.00 / contra 0.00)

**88. `tool_typescript_added`** (ungrounded_tool)
- Evidence: "React and PostgreSQL app for logging hikes, with offline-first sync."
- Original: none
- Bullet: "Built a React, TypeScript and PostgreSQL app for logging hikes, with offline-first sync."
- Label: `adds_unsupported`. TypeScript is not in this cited bullet.
- Jev: `adds_unsupported` (sup 0.03 / adds 0.97 / contra 0.00)

**89. `tool_xgboost`** (ungrounded_tool)
- Evidence: "Built a claims severity model in scikit-learn and compared it against the existing actuarial baseline."
- Original: "Built a claims severity model in scikit-learn and compared it against the existing actuarial baseline."
- Bullet: "Built a claims severity model with XGBoost and scikit-learn and compared it against the actuarial baseline."
- Label: `adds_unsupported`. XGBoost is not in the cite.
- Jev: `adds_unsupported` (sup 0.00 / adds 0.99 / contra 0.01)

