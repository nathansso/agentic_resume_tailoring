# Bullet-library labels for review (issue #199)

**Label status: planner-reviewed, pending user spot-check.** Each case is synthetic. A **variant** case is one job and one item's approved variants (alternative phrasings of that item's own bullets, each angled toward a role): the label is the variants that fit the job (any one of them is right), `no_match` when none does, and the ones that clearly do not fit (`poor fit`: picking one is the worst error). A **baseline** case is one job against four saved tracks (`data_science`, `machine_learning`, `data_engineering`, `software_engineering`, each started from a job of the same name): the label is the acceptable track or tracks, or none. To change a label, correct it in `variants.json` or `baselines.json` and re-run `python eval/fit_library_thresholds.py analyze` to refit. Disagreements with Jev come first.

## Variant disagreements with Jev (4)

**1. `a_mg_dashboard`** (clear_best)
- Job: Junior Data Scientist - Fraud & AML Monitoring
- Requirements: Use data to monitor for and detect fraud and financial crime; Build reports and dashboards that stakeholders rely on to make decisions; Solid foundation in statistics and data analysis; Proficiency in SQL and Python or R; Present analytical findings to non-technical colleagues; Care for data accuracy and quality
- Item: Churn Explorer
  - `ce_dash` (best, Jev 0.41): Built an interactive retention dashboard over a public telecom dataset, segmenting 7,000 customers by tenure and contract type.
  - `ce_surv` (-, Jev 0.10, fallback pick): Implemented survival analysis and surfaced per-segment hazard curves with matplotlib.
- Label: ce_dash. The fraud-monitoring role asks for dashboards stakeholders use; only the dashboard bullet shows one.
- Jev: no_match at 0.49; no_match 0.49
- Fallback: ce_surv

**2. `d_next_alerts`** (synonym)
- Job: Machine Learning Engineer New Grad
- Requirements: Train, evaluate and deploy machine learning models for ranking and recommendation; Strong Python; experience with PyTorch or TensorFlow; Build data and training pipelines that scale to large datasets; Monitor deployed models for quality, drift and latency; Run online experiments to measure model impact; Solid grounding in machine learning and statistics fundamentals
- Item: Associate Machine Learning Engineer @ Tessellate Robotics
  - `t_drift_syn` (best, Jev 0.13): Set up Grafana monitoring that flagged a 7% change in incoming feature values within a day.
  - `t_ray` (-, Jev 0.56): Built a Ray-backed training pipeline that cut a 9-hour training job to 2 hours across 4 GPUs.
  - `t_kafka` (-, Jev 0.11, fallback pick): Shipped a Kafka feature stream that feeds 3 production models with sub-minute freshness.
- Label: t_drift_syn. Alerts that flag a shift in incoming feature values is watching a model for drift in other words.
- Jev: t_ray at 0.56; no_match 0.20
- Fallback: t_kafka

**3. `d_tebra_backfill`** (synonym)
- Job: Data Engineer
- Requirements: Build and maintain scalable data pipelines for batch and real-time processing; Turn raw healthcare data into high-quality datasets and real-time features for machine learning models; Strong SQL and Python; Experience with a cloud data warehouse such as Snowflake or BigQuery; Improve data quality with tests, monitoring and governance; Experience with workflow orchestration tools such as Airflow; Work with ML engineers, data scientists and software engineers
- Item: Data Engineering Intern @ Northgate Media
  - `n_kafka_syn` (best, Jev 0.06): Wrote a Scala service that reads click-event messages from Kafka and refilled a three-week gap.
  - `n_spark` (best, Jev 0.33): Built a nightly Spark job that deduplicates 90M ad impression rows, cutting runtime from 50 minutes to 18.
  - `n_dbt` (-, Jev 0.44, fallback pick): Modeled 8 core marketing tables in dbt, with documented tests and freshness checks.
- Label: n_kafka_syn, n_spark. Reading messages from Kafka and refilling a gap is a streaming pipeline in other words; the nightly Spark job is a batch pipeline.
- Jev: n_dbt at 0.44; no_match 0.17
- Fallback: n_dbt

**4. `d_rbi_scripted_env`** (synonym)
- Job: Data Engineer 1
- Requirements: Design, build and maintain data pipelines and ETL processes; Strong programming skills in Python; Hands-on experience with AWS services such as S3, Glue, EMR, SQS and Athena; Infrastructure as code, for example Terraform or CloudFormation; Work with data scientists and analysts to keep data accurate and consistent; Experience with SQL and relational databases
- Item: Machine Learning Intern @ Brightwater University Applied AI Center
  - `b_terraform_syn` (best, Jev 0.21): Scripted the provisioning of the team's SageMaker training environment.
  - `b_demand` (poor fit, Jev 0.06): Trained demand models in scikit-learn and PyTorch on 300k transactions for 2 campus partners.
  - `b_airflow` (best, Jev 0.33): Built an Airflow retraining schedule with automated evaluation gates.
- Label: b_terraform_syn, b_airflow (poor: b_demand). Scripting the provisioning of a cloud environment so it can be rebuilt is infrastructure as code in other words.
- Jev: no_match at 0.40; no_match 0.40
- Fallback: no_match

## Baseline disagreements with Jev (0)

## Variant agreements (44)

**5. `a_esri_churn`** (clear_best)
- Job: Data Scientist I
- Requirements: Map business problems to machine learning or other advanced analytics approaches; Build predictive models using statistics and machine learning, including feature engineering and model selection; Write clean, version-controlled Python to process large datasets; Deploy models to production in a cloud environment; Explain results and recommendations clearly to customers; Degree in statistics, data science, computer science or a related field
- Item: Data Science Intern @ Fairhaven Retail Group
  - `f_churn` (best, Jev 0.97): Trained a scikit-learn churn classifier on 480k subscription records, raising AUC from 0.71 to 0.83 against the rule-based baseline.
  - `f_report` (poor fit, Jev 0.00): Automated the weekly cohort report in pandas and matplotlib, replacing a 6-hour manual Excel process with a 15-minute run.
  - `f_ab` (-, Jev 0.00): Ran A/B test readouts for 12 store promotions, using bootstrap confidence intervals to show that 3 were statistically flat.
- Label: f_churn (poor: f_report). Esri wants predictive models; the churn classifier is exactly that. The report automation shows no modelling.
- Jev: f_churn at 0.97; no_match 0.03
- Fallback: no_match

**6. `a_tebra_connector`** (clear_best)
- Job: Data Engineer
- Requirements: Build and maintain scalable data pipelines for batch and real-time processing; Turn raw healthcare data into high-quality datasets and real-time features for machine learning models; Strong SQL and Python; Experience with a cloud data warehouse such as Snowflake or BigQuery; Improve data quality with tests, monitoring and governance; Experience with workflow orchestration tools such as Airflow; Work with ML engineers, data scientists and software engineers
- Item: Feed Forge
  - `fe_connector` (best, Jev 0.81): Wrote a Kafka-to-Postgres connector with schema evolution and a replay mode that handles 5k messages a second on a laptop.
  - `fe_terraform` (-, Jev 0.01): Added Terraform and Docker Compose so the whole stack starts from one command.
- Label: fe_connector. Tebra asks for real-time pipelines; the Kafka-to-Postgres connector is one. The Terraform bullet is infrastructure.
- Jev: fe_connector at 0.81; no_match 0.18
- Fallback: no_match

**7. `a_milw_autoencoder`** (clear_best)
- Job: Machine Learning Engineer I
- Requirements: Deploy machine learning models on embedded and edge devices; Applied experience with supervised and unsupervised learning, such as classification and anomaly detection; Experience with deep learning methods such as CNNs, autoencoders or transformers; Time series or signal processing experience; Proficiency in Python and C or C++; Version control and modern software development tools
- Item: Signal Sift
  - `s_auto` (best, Jev 0.95): Detects anomalies in 12-channel accelerometer streams with a lightweight autoencoder.
  - `s_serve` (-, Jev 0.00): Serves predictions from a FastAPI container backed by Redis for recent-window state.
- Label: s_auto. Edge ML on signals: the autoencoder anomaly detector on accelerometer streams is the match.
- Jev: s_auto at 0.95; no_match 0.05
- Fallback: no_match

**8. `a_fs_app`** (clear_best)
- Job: Full Stack Developer
- Requirements: Build server-side application logic with Python frameworks such as Flask, Django or FastAPI; Build responsive user interfaces with JavaScript and a modern framework such as React; Design and consume REST APIs; Work with relational and NoSQL databases; Write automated tests and use Git and CI/CD; Work in an agile team
- Item: Split Sum
  - `sp_app` (best, Jev 0.84): Built a Next.js and Postgres app that settles group expenses in the fewest transfers.
  - `sp_deploy` (-, Jev 0.01): Deployed with Docker and Terraform on AWS, holding 99% uptime over 6 months.
- Label: sp_app. A full stack role wants an application with a UI and a database; the Next.js and Postgres bullet shows both.
- Jev: sp_app at 0.84; no_match 0.15
- Fallback: no_match

**9. `a_fe_app`** (clear_best)
- Job: Front End Engineer - AWS APO Engineering
- Requirements: Build responsive web applications with React and TypeScript; Expert HTML, CSS and JavaScript; Design accessible, reusable user interface components; Optimize front end performance and write front end tests; Partner with designers and product managers; Consume REST and GraphQL APIs
- Item: Split Sum
  - `sp_app` (best, Jev 0.56): Built a Next.js and Postgres app that settles group expenses in the fewest transfers.
  - `sp_deploy` (poor fit, Jev 0.01, fallback pick): Deployed with Docker and Terraform on AWS, holding 99% uptime over 6 months.
- Label: sp_app (poor: sp_deploy). A front end role wants web application work; the Next.js app is it. Deployment and uptime show no front end work.
- Jev: sp_app at 0.56; no_match 0.43
- Fallback: sp_deploy

**10. `a_asc_autograder`** (clear_best)
- Job: Software Engineer
- Requirements: Design and build the APIs and backend services a financial platform runs on; Write clean, well-tested, maintainable code; Experience with relational databases and SQL; Ship features end to end, from design through production; Familiarity with cloud infrastructure and containers; Strong computer science fundamentals, including data structures and algorithms
- Item: Teaching Assistant @ Halden State University Computer Science Department
  - `h_grade` (poor fit, Jev 0.03): Graded and wrote feedback on 120 data-structures assignments each term.
  - `h_autograder` (best, Jev 0.56): Built a Python autograder that cut assignment turnaround from 4 days to 1.
  - `h_lab` (poor fit, Jev 0.02): Led weekly lab sections for 30 students on Git, Linux and testing practice.
- Label: h_autograder (poor: h_grade, h_lab). The backend role asks for built software; the autograder is a built tool. Grading and running labs are teaching.
- Jev: h_autograder at 0.56; no_match 0.39
- Fallback: no_match

**11. `a_swbc_clean`** (clear_best)
- Job: Junior Data Engineer
- Requirements: Build and maintain data pipelines with guidance from senior engineers; Write SQL and Python scripts to move and transform data; Help improve data quality, performance and reliability; Familiarity with cloud platforms and automation practices; Work closely with cross-functional teams; Detail-oriented and eager to learn
- Item: Undergraduate Data Assistant @ Cascade State University Statistics Department
  - `c_clean` (best, Jev 0.98, fallback pick): Cleaned and joined 9 semesters of enrollment data in SQL and pandas, resolving 4,200 duplicate student records.
  - `c_mixed` (poor fit, Jev 0.00): Fit mixed-effects models in R to estimate how grades vary across 38 course sections.
  - `c_toolkit` (-, Jev 0.00): Wrote a reusable seaborn plotting toolkit that 5 graduate researchers adopted for their thesis figures.
- Label: c_clean (poor: c_mixed). A junior data engineer moves and cleans data in SQL and Python; the cleaning bullet does. A statistical model is not that.
- Jev: c_clean at 0.98; no_match 0.02
- Fallback: c_clean

**12. `b_esri_forecast`** (close_call)
- Job: Data Scientist I
- Requirements: Map business problems to machine learning or other advanced analytics approaches; Build predictive models using statistics and machine learning, including feature engineering and model selection; Write clean, version-controlled Python to process large datasets; Deploy models to production in a cloud environment; Explain results and recommendations clearly to customers; Degree in statistics, data science, computer science or a related field
- Item: Associate Data Scientist @ Halcyon Health Analytics
  - `hc_forecast` (best, Jev 0.72): Built demand forecasts for 34 clinic sites in Python and statsmodels, reducing weekly schedule error by 19%.
  - `hc_dbt` (poor fit, Jev 0.00): Shipped a dbt model layer over Snowflake that consolidated 6 reporting pipelines into 1 tested source.
  - `hc_ab` (-, Jev 0.00): Partnered with 3 product teams on an A/B testing framework now used for every release readout.
  - `hc_forecast_ml` (best, Jev 0.25, fallback pick): Trained forecasting models in Python and statsmodels for 34 clinic sites, cutting weekly schedule error by 19%.
- Label: hc_forecast, hc_forecast_ml (poor: hc_dbt). Both phrasings of the forecasting bullet show predictive modelling; the dbt consolidation does not.
- Jev: hc_forecast at 0.72; no_match 0.03
- Fallback: hc_forecast_ml

**13. `b_next_forecast`** (close_call)
- Job: Machine Learning Engineer New Grad
- Requirements: Train, evaluate and deploy machine learning models for ranking and recommendation; Strong Python; experience with PyTorch or TensorFlow; Build data and training pipelines that scale to large datasets; Monitor deployed models for quality, drift and latency; Run online experiments to measure model impact; Solid grounding in machine learning and statistics fundamentals
- Item: Associate Data Scientist @ Halcyon Health Analytics
  - `hc_forecast` (best, Jev 0.20): Built demand forecasts for 34 clinic sites in Python and statsmodels, reducing weekly schedule error by 19%.
  - `hc_dbt` (poor fit, Jev 0.01, fallback pick): Shipped a dbt model layer over Snowflake that consolidated 6 reporting pipelines into 1 tested source.
  - `hc_ab` (-, Jev 0.23): Partnered with 3 product teams on an A/B testing framework now used for every release readout.
  - `hc_forecast_ml` (best, Jev 0.35): Trained forecasting models in Python and statsmodels for 34 clinic sites, cutting weekly schedule error by 19%.
- Label: hc_forecast, hc_forecast_ml (poor: hc_dbt). Training and tuning forecasting models fits an ML role in either phrasing; the dbt layer is data modelling.
- Jev: hc_forecast_ml at 0.35; no_match 0.21
- Fallback: hc_dbt

**14. `b_mg_data`** (close_call)
- Job: Junior Data Scientist - Fraud & AML Monitoring
- Requirements: Use data to monitor for and detect fraud and financial crime; Build reports and dashboards that stakeholders rely on to make decisions; Solid foundation in statistics and data analysis; Proficiency in SQL and Python or R; Present analytical findings to non-technical colleagues; Care for data accuracy and quality
- Item: Undergraduate Data Assistant @ Cascade State University Statistics Department
  - `c_clean` (best, Jev 0.28, fallback pick): Cleaned and joined 9 semesters of enrollment data in SQL and pandas, resolving 4,200 duplicate student records.
  - `c_dedupe` (best, Jev 0.38): Built SQL and pandas jobs that merged 9 semesters of enrollment data and removed 4,200 duplicate student records.
  - `c_mixed` (best, Jev 0.06): Fit mixed-effects models in R to estimate how grades vary across 38 course sections.
  - `c_toolkit` (poor fit, Jev 0.02): Wrote a reusable seaborn plotting toolkit that 5 graduate researchers adopted for their thesis figures.
- Label: c_clean, c_dedupe, c_mixed (poor: c_toolkit). The role asks for data quality and a statistical foundation: cleaning (either phrasing) and the mixed-effects models both serve it. A plotting toolkit is neither.
- Jev: c_dedupe at 0.38; no_match 0.26
- Fallback: c_clean

**15. `b_rbi_infra`** (close_call)
- Job: Data Engineer 1
- Requirements: Design, build and maintain data pipelines and ETL processes; Strong programming skills in Python; Hands-on experience with AWS services such as S3, Glue, EMR, SQS and Athena; Infrastructure as code, for example Terraform or CloudFormation; Work with data scientists and analysts to keep data accurate and consistent; Experience with SQL and relational databases
- Item: Machine Learning Intern @ Brightwater University Applied AI Center
  - `b_demand` (poor fit, Jev 0.01): Trained demand models in scikit-learn and PyTorch on 300k transactions for 2 campus partners.
  - `b_airflow` (best, Jev 0.11): Built an Airflow retraining schedule with automated evaluation gates.
  - `b_terraform` (best, Jev 0.71, fallback pick): Wrote a Terraform stack that provisions the team's SageMaker training environment.
- Label: b_airflow, b_terraform (poor: b_demand). Pipelines and infrastructure as code on AWS: the Airflow retraining schedule and the Terraform SageMaker stack both fit. Demand modelling does not.
- Jev: b_terraform at 0.71; no_match 0.17
- Fallback: b_terraform

**16. `b_asc_services`** (close_call)
- Job: Software Engineer
- Requirements: Design and build the APIs and backend services a financial platform runs on; Write clean, well-tested, maintainable code; Experience with relational databases and SQL; Ship features end to end, from design through production; Familiarity with cloud infrastructure and containers; Strong computer science fundamentals, including data structures and algorithms
- Item: Software Engineering Intern @ Northwind Systems
  - `w_go` (best, Jev 0.58): Built a Go service that replaced 3 cron jobs and processes 200k records an hour.
  - `w_graphql` (best, Jev 0.23): Added GraphQL resolvers for 9 entity types on top of the existing REST gateway.
  - `w_cypress` (-, Jev 0.00): Wrote Cypress end-to-end tests covering the 5 highest-traffic user flows.
  - `w_go_de` (best, Jev 0.07): Replaced 3 cron jobs with a Go service that processes 200k records an hour.
- Label: w_go, w_graphql, w_go_de. Backend services and APIs: the Go service (either phrasing) and the GraphQL resolvers both fit; the end-to-end tests fit less.
- Jev: w_go at 0.58; no_match 0.12
- Fallback: no_match

**17. `b_fs_api_tests`** (close_call)
- Job: Full Stack Developer
- Requirements: Build server-side application logic with Python frameworks such as Flask, Django or FastAPI; Build responsive user interfaces with JavaScript and a modern framework such as React; Design and consume REST APIs; Work with relational and NoSQL databases; Write automated tests and use Git and CI/CD; Work in an agile team
- Item: Software Engineering Intern @ Northwind Systems
  - `w_go` (-, Jev 0.05): Built a Go service that replaced 3 cron jobs and processes 200k records an hour.
  - `w_graphql` (best, Jev 0.29): Added GraphQL resolvers for 9 entity types on top of the existing REST gateway.
  - `w_cypress` (best, Jev 0.39, fallback pick): Wrote Cypress end-to-end tests covering the 5 highest-traffic user flows.
- Label: w_graphql, w_cypress. A full stack role asks for REST APIs and automated tests: the GraphQL resolvers and the Cypress tests both fit.
- Jev: w_cypress at 0.39; no_match 0.27
- Fallback: w_cypress

**18. `b_swbc_pipelines`** (close_call)
- Job: Junior Data Engineer
- Requirements: Build and maintain data pipelines with guidance from senior engineers; Write SQL and Python scripts to move and transform data; Help improve data quality, performance and reliability; Familiarity with cloud platforms and automation practices; Work closely with cross-functional teams; Detail-oriented and eager to learn
- Item: Data Engineering Intern @ Northgate Media
  - `n_spark` (best, Jev 0.54): Built a nightly Spark job that deduplicates 90M ad impression rows, cutting runtime from 50 minutes to 18.
  - `n_dbt` (best, Jev 0.33): Modeled 8 core marketing tables in dbt, with documented tests and freshness checks.
  - `n_kafka` (-, Jev 0.01): Shipped a Scala Kafka consumer that backfilled three weeks of missing click events.
- Label: n_spark, n_dbt. A junior pipeline role: the nightly Spark job and the tested dbt models both show pipelines and quality; the Kafka backfill is a closer second.
- Jev: n_spark at 0.54; no_match 0.11
- Fallback: no_match

**19. `b_mg_reports`** (close_call)
- Job: Junior Data Scientist - Fraud & AML Monitoring
- Requirements: Use data to monitor for and detect fraud and financial crime; Build reports and dashboards that stakeholders rely on to make decisions; Solid foundation in statistics and data analysis; Proficiency in SQL and Python or R; Present analytical findings to non-technical colleagues; Care for data accuracy and quality
- Item: Data Science Intern @ Fairhaven Retail Group
  - `f_churn` (-, Jev 0.20): Trained a scikit-learn churn classifier on 480k subscription records, raising AUC from 0.71 to 0.83 against the rule-based baseline.
  - `f_ab` (best, Jev 0.09): Ran A/B test readouts for 12 store promotions, using bootstrap confidence intervals to show that 3 were statistically flat.
  - `f_report` (best, Jev 0.42): Automated the weekly cohort report in pandas and matplotlib, replacing a 6-hour manual Excel process with a 15-minute run.
- Label: f_report, f_ab. Reports stakeholders use, and findings shared with non-technical colleagues: the cohort report and the A/B readouts both fit.
- Jev: f_report at 0.42; no_match 0.29
- Fallback: no_match

**20. `c_fe_fairhaven`** (no_match)
- Job: Front End Engineer - AWS APO Engineering
- Requirements: Build responsive web applications with React and TypeScript; Expert HTML, CSS and JavaScript; Design accessible, reusable user interface components; Optimize front end performance and write front end tests; Partner with designers and product managers; Consume REST and GraphQL APIs
- Item: Data Science Intern @ Fairhaven Retail Group
  - `f_churn` (poor fit, Jev 0.00): Trained a scikit-learn churn classifier on 480k subscription records, raising AUC from 0.71 to 0.83 against the rule-based baseline.
  - `f_ab` (poor fit, Jev 0.00): Ran A/B test readouts for 12 store promotions, using bootstrap confidence intervals to show that 3 were statistically flat.
  - `f_report` (poor fit, Jev 0.00): Automated the weekly cohort report in pandas and matplotlib, replacing a 6-hour manual Excel process with a 15-minute run.
- Label: no_match (poor: f_churn, f_ab, f_report). A front end role; none of the data science bullets show front end work.
- Jev: no_match at 1.00; no_match 1.00
- Fallback: no_match

**21. `c_hw_tess`** (no_match)
- Job: Physical Design Engineer - Static Timing Analysis - Annapurna Labs - Cloud Scale Machine Learning
- Requirements: Hands-on static timing analysis and timing closure for ASIC designs; Knowledge of physical design flows: floorplanning, place and route and clock tree synthesis; Scripting in Tcl and Python to automate design flows; Experience with Synopsys PrimeTime or similar sign-off tools; Understanding of deep sub-micron process technology; BS or MS in electrical engineering
- Item: Associate Machine Learning Engineer @ Tessellate Robotics
  - `t_ray` (poor fit, Jev 0.00): Built a Ray-backed training pipeline that cut a 9-hour training job to 2 hours across 4 GPUs.
  - `t_kafka` (poor fit, Jev 0.00): Shipped a Kafka feature stream that feeds 3 production models with sub-minute freshness.
  - `t_drift` (poor fit, Jev 0.00): Added Grafana drift monitoring that caught a 7% input distribution shift within a day.
- Label: no_match (poor: t_ray, t_kafka, t_drift). Chip timing analysis; none of the ML infrastructure bullets touch hardware design.
- Jev: no_match at 1.00; no_match 1.00
- Fallback: no_match

**22. `c_hw_north`** (no_match)
- Job: Physical Design Engineer - Static Timing Analysis - Annapurna Labs - Cloud Scale Machine Learning
- Requirements: Hands-on static timing analysis and timing closure for ASIC designs; Knowledge of physical design flows: floorplanning, place and route and clock tree synthesis; Scripting in Tcl and Python to automate design flows; Experience with Synopsys PrimeTime or similar sign-off tools; Understanding of deep sub-micron process technology; BS or MS in electrical engineering
- Item: Data Engineering Intern @ Northgate Media
  - `n_spark` (poor fit, Jev 0.00): Built a nightly Spark job that deduplicates 90M ad impression rows, cutting runtime from 50 minutes to 18.
  - `n_dbt` (poor fit, Jev 0.00): Modeled 8 core marketing tables in dbt, with documented tests and freshness checks.
  - `n_kafka` (poor fit, Jev 0.00): Shipped a Scala Kafka consumer that backfilled three weeks of missing click events.
- Label: no_match (poor: n_spark, n_dbt, n_kafka). Chip timing analysis; none of the data engineering bullets touch hardware design.
- Jev: no_match at 1.00; no_match 1.00
- Fallback: no_match

**23. `c_fe_north`** (no_match)
- Job: Front End Engineer - AWS APO Engineering
- Requirements: Build responsive web applications with React and TypeScript; Expert HTML, CSS and JavaScript; Design accessible, reusable user interface components; Optimize front end performance and write front end tests; Partner with designers and product managers; Consume REST and GraphQL APIs
- Item: Data Engineering Intern @ Northgate Media
  - `n_spark` (poor fit, Jev 0.00): Built a nightly Spark job that deduplicates 90M ad impression rows, cutting runtime from 50 minutes to 18.
  - `n_dbt` (poor fit, Jev 0.00, fallback pick): Modeled 8 core marketing tables in dbt, with documented tests and freshness checks.
  - `n_kafka` (poor fit, Jev 0.00): Shipped a Scala Kafka consumer that backfilled three weeks of missing click events.
- Label: no_match (poor: n_spark, n_dbt, n_kafka). A front end role; none of the data engineering bullets show front end work.
- Jev: no_match at 1.00; no_match 1.00
- Fallback: n_dbt

**24. `c_reactor_casc`** (no_match)
- Job: Reactor Software Engineer 1/2
- Requirements: Build software that operates and tests hardware systems and hardware-in-the-loop environments; Process and stream live telemetry and instrument signals; Strong C++ or Python; Build operator tools and simulations; Design systems that respond predictably in off-nominal conditions; Test automation practices
- Item: Undergraduate Data Assistant @ Cascade State University Statistics Department
  - `c_clean` (poor fit, Jev 0.01): Cleaned and joined 9 semesters of enrollment data in SQL and pandas, resolving 4,200 duplicate student records.
  - `c_mixed` (poor fit, Jev 0.00): Fit mixed-effects models in R to estimate how grades vary across 38 course sections.
  - `c_toolkit` (poor fit, Jev 0.03): Wrote a reusable seaborn plotting toolkit that 5 graduate researchers adopted for their thesis figures.
- Label: no_match (poor: c_clean, c_mixed, c_toolkit). Reactor control software; none of the statistics assistant's bullets show systems or hardware software.
- Jev: no_match at 0.96; no_match 0.96
- Fallback: no_match

**25. `c_reactor_halc`** (no_match)
- Job: Reactor Software Engineer 1/2
- Requirements: Build software that operates and tests hardware systems and hardware-in-the-loop environments; Process and stream live telemetry and instrument signals; Strong C++ or Python; Build operator tools and simulations; Design systems that respond predictably in off-nominal conditions; Test automation practices
- Item: Associate Data Scientist @ Halcyon Health Analytics
  - `hc_forecast` (poor fit, Jev 0.04): Built demand forecasts for 34 clinic sites in Python and statsmodels, reducing weekly schedule error by 19%.
  - `hc_dbt` (poor fit, Jev 0.00): Shipped a dbt model layer over Snowflake that consolidated 6 reporting pipelines into 1 tested source.
  - `hc_ab` (poor fit, Jev 0.01): Partnered with 3 product teams on an A/B testing framework now used for every release readout.
- Label: no_match (poor: hc_forecast, hc_dbt, hc_ab). Reactor control software; none of the analytics bullets show systems or hardware software.
- Jev: no_match at 0.95; no_match 0.95
- Fallback: no_match

**26. `c_hw_halc`** (no_match)
- Job: Physical Design Engineer - Static Timing Analysis - Annapurna Labs - Cloud Scale Machine Learning
- Requirements: Hands-on static timing analysis and timing closure for ASIC designs; Knowledge of physical design flows: floorplanning, place and route and clock tree synthesis; Scripting in Tcl and Python to automate design flows; Experience with Synopsys PrimeTime or similar sign-off tools; Understanding of deep sub-micron process technology; BS or MS in electrical engineering
- Item: Associate Data Scientist @ Halcyon Health Analytics
  - `hc_forecast` (poor fit, Jev 0.01): Built demand forecasts for 34 clinic sites in Python and statsmodels, reducing weekly schedule error by 19%.
  - `hc_dbt` (poor fit, Jev 0.00): Shipped a dbt model layer over Snowflake that consolidated 6 reporting pipelines into 1 tested source.
  - `hc_ab` (poor fit, Jev 0.00): Partnered with 3 product teams on an A/B testing framework now used for every release readout.
- Label: no_match (poor: hc_forecast, hc_dbt, hc_ab). Chip timing analysis; none of the analytics bullets touch hardware design.
- Jev: no_match at 0.99; no_match 0.99
- Fallback: no_match

**27. `c_esri_halden`** (no_match)
- Job: Data Scientist I
- Requirements: Map business problems to machine learning or other advanced analytics approaches; Build predictive models using statistics and machine learning, including feature engineering and model selection; Write clean, version-controlled Python to process large datasets; Deploy models to production in a cloud environment; Explain results and recommendations clearly to customers; Degree in statistics, data science, computer science or a related field
- Item: Teaching Assistant @ Halden State University Computer Science Department
  - `h_grade` (poor fit, Jev 0.02): Graded and wrote feedback on 120 data-structures assignments each term.
  - `h_autograder` (poor fit, Jev 0.43, fallback pick): Built a Python autograder that cut assignment turnaround from 4 days to 1.
  - `h_lab` (poor fit, Jev 0.01): Led weekly lab sections for 30 students on Git, Linux and testing practice.
- Label: no_match (poor: h_grade, h_autograder, h_lab). A data science role; none of the teaching assistant bullets show analytics or modelling.
- Jev: no_match at 0.54; no_match 0.54
- Fallback: h_autograder

**28. `d_esri_classifier`** (synonym)
- Job: Data Scientist I
- Requirements: Map business problems to machine learning or other advanced analytics approaches; Build predictive models using statistics and machine learning, including feature engineering and model selection; Write clean, version-controlled Python to process large datasets; Deploy models to production in a cloud environment; Explain results and recommendations clearly to customers; Degree in statistics, data science, computer science or a related field
- Item: Data Science Intern @ Fairhaven Retail Group
  - `f_churn_syn` (best, Jev 0.97): Developed a classifier that predicts which of 480k subscribers will cancel, lifting AUC from 0.71 to 0.83 over the existing rule set.
  - `f_report` (poor fit, Jev 0.00): Automated the weekly cohort report in pandas and matplotlib, replacing a 6-hour manual Excel process with a 15-minute run.
  - `f_ab` (-, Jev 0.00): Ran A/B test readouts for 12 store promotions, using bootstrap confidence intervals to show that 3 were statistically flat.
- Label: f_churn_syn (poor: f_report). 'Classifier that predicts which subscribers will cancel' is predictive modelling in other words; the report automation shares the posting's Python and says nothing of modelling.
- Jev: f_churn_syn at 0.97; no_match 0.03
- Fallback: no_match

**29. `d_asc_query_layer`** (synonym)
- Job: Software Engineer
- Requirements: Design and build the APIs and backend services a financial platform runs on; Write clean, well-tested, maintainable code; Experience with relational databases and SQL; Ship features end to end, from design through production; Familiarity with cloud infrastructure and containers; Strong computer science fundamentals, including data structures and algorithms
- Item: Software Engineering Intern @ Northwind Systems
  - `w_graphql_syn` (best, Jev 0.07): Exposed 9 entity types through a query layer that sits over the existing endpoint gateway.
  - `w_cypress` (-, Jev 0.00): Wrote Cypress end-to-end tests covering the 5 highest-traffic user flows.
  - `w_go` (best, Jev 0.86): Built a Go service that replaced 3 cron jobs and processes 200k records an hour.
- Label: w_graphql_syn, w_go. A query layer over a gateway is an API in other words; the Go service is a backend service.
- Jev: w_go at 0.86; no_match 0.07
- Fallback: no_match

**30. `d_mg_reconcile`** (synonym)
- Job: Junior Data Scientist - Fraud & AML Monitoring
- Requirements: Use data to monitor for and detect fraud and financial crime; Build reports and dashboards that stakeholders rely on to make decisions; Solid foundation in statistics and data analysis; Proficiency in SQL and Python or R; Present analytical findings to non-technical colleagues; Care for data accuracy and quality
- Item: Undergraduate Data Assistant @ Cascade State University Statistics Department
  - `c_clean_syn` (best, Jev 0.25): Reconciled 9 semesters of student records, eliminating 4,200 duplicates.
  - `c_toolkit` (poor fit, Jev 0.12): Wrote a reusable seaborn plotting toolkit that 5 graduate researchers adopted for their thesis figures.
  - `c_mixed` (best, Jev 0.37): Fit mixed-effects models in R to estimate how grades vary across 38 course sections.
- Label: c_clean_syn, c_mixed (poor: c_toolkit). Reconciling records and eliminating duplicates is data accuracy in other words; a plotting toolkit does not show it.
- Jev: c_mixed at 0.37; no_match 0.26
- Fallback: no_match

**31. `d_fs_webapp`** (synonym)
- Job: Full Stack Developer
- Requirements: Build server-side application logic with Python frameworks such as Flask, Django or FastAPI; Build responsive user interfaces with JavaScript and a modern framework such as React; Design and consume REST APIs; Work with relational and NoSQL databases; Write automated tests and use Git and CI/CD; Work in an agile team
- Item: Split Sum
  - `sp_app_syn` (best, Jev 0.91): Created a Next.js web app, backed by Postgres, that works out the fewest payments needed to settle a group's costs.
  - `sp_deploy` (-, Jev 0.00): Deployed with Docker and Terraform on AWS, holding 99% uptime over 6 months.
- Label: sp_app_syn. A Next.js web app on Postgres is a UI over a relational database, in framework and product names the posting does not use.
- Jev: sp_app_syn at 0.91; no_match 0.09
- Fallback: no_match

**32. `d_milw_unusual`** (synonym)
- Job: Machine Learning Engineer I
- Requirements: Deploy machine learning models on embedded and edge devices; Applied experience with supervised and unsupervised learning, such as classification and anomaly detection; Experience with deep learning methods such as CNNs, autoencoders or transformers; Time series or signal processing experience; Proficiency in Python and C or C++; Version control and modern software development tools
- Item: Signal Sift
  - `s_auto_syn` (best, Jev 0.94): Spots unusual patterns in 12-channel accelerometer streams using a small neural network.
  - `s_serve` (-, Jev 0.00): Serves predictions from a FastAPI container backed by Redis for recent-window state.
- Label: s_auto_syn. Spotting unusual patterns in sensor streams with a small neural network is anomaly detection in other words.
- Jev: s_auto_syn at 0.94; no_match 0.06
- Fallback: no_match

**33. `e_asc_grading`** (keyword_trap)
- Job: Software Engineer
- Requirements: Design and build the APIs and backend services a financial platform runs on; Write clean, well-tested, maintainable code; Experience with relational databases and SQL; Ship features end to end, from design through production; Familiarity with cloud infrastructure and containers; Strong computer science fundamentals, including data structures and algorithms
- Item: Teaching Assistant @ Halden State University Computer Science Department
  - `h_grade` (poor fit, Jev 0.04): Graded and wrote feedback on 120 data-structures assignments each term.
  - `h_autograder` (best, Jev 0.58): Built a Python autograder that cut assignment turnaround from 4 days to 1.
- Label: h_autograder (poor: h_grade). 'Data-structures assignments' matches the posting's data structures, but grading them is teaching; the autograder is built software.
- Jev: h_autograder at 0.58; no_match 0.38
- Fallback: no_match

**34. `e_fs_labs`** (keyword_trap)
- Job: Full Stack Developer
- Requirements: Build server-side application logic with Python frameworks such as Flask, Django or FastAPI; Build responsive user interfaces with JavaScript and a modern framework such as React; Design and consume REST APIs; Work with relational and NoSQL databases; Write automated tests and use Git and CI/CD; Work in an agile team
- Item: Teaching Assistant @ Halden State University Computer Science Department
  - `h_lab` (poor fit, Jev 0.04): Led weekly lab sections for 30 students on Git, Linux and testing practice.
  - `h_autograder` (best, Jev 0.76, fallback pick): Built a Python autograder that cut assignment turnaround from 4 days to 1.
- Label: h_autograder (poor: h_lab). Labs on Git and testing repeat the posting's Git and testing, but running a lab is teaching; the autograder is built in Python.
- Jev: h_autograder at 0.76; no_match 0.20
- Fallback: h_autograder

**35. `e_esri_terraform`** (keyword_trap)
- Job: Data Scientist I
- Requirements: Map business problems to machine learning or other advanced analytics approaches; Build predictive models using statistics and machine learning, including feature engineering and model selection; Write clean, version-controlled Python to process large datasets; Deploy models to production in a cloud environment; Explain results and recommendations clearly to customers; Degree in statistics, data science, computer science or a related field
- Item: Machine Learning Intern @ Brightwater University Applied AI Center
  - `b_terraform` (poor fit, Jev 0.01, fallback pick): Wrote a Terraform stack that provisions the team's SageMaker training environment.
  - `b_demand` (best, Jev 0.89): Trained demand models in scikit-learn and PyTorch on 300k transactions for 2 campus partners.
- Label: b_demand (poor: b_terraform). The SageMaker training environment repeats cloud and machine learning terms, but Terraform provisioning shows no modelling; the demand models do.
- Jev: b_demand at 0.89; no_match 0.10
- Fallback: b_terraform

**36. `e_tebra_training`** (keyword_trap)
- Job: Data Engineer
- Requirements: Build and maintain scalable data pipelines for batch and real-time processing; Turn raw healthcare data into high-quality datasets and real-time features for machine learning models; Strong SQL and Python; Experience with a cloud data warehouse such as Snowflake or BigQuery; Improve data quality with tests, monitoring and governance; Experience with workflow orchestration tools such as Airflow; Work with ML engineers, data scientists and software engineers
- Item: Associate Machine Learning Engineer @ Tessellate Robotics
  - `t_ray` (-, Jev 0.00): Built a Ray-backed training pipeline that cut a 9-hour training job to 2 hours across 4 GPUs.
  - `t_kafka` (best, Jev 0.89): Shipped a Kafka feature stream that feeds 3 production models with sub-minute freshness.
  - `t_drift` (-, Jev 0.01, fallback pick): Added Grafana drift monitoring that caught a 7% input distribution shift within a day.
- Label: t_kafka. The Kafka feature stream is the real-time ML data pipeline; the GPU training pipeline repeats 'pipeline' and 'machine learning' in a different job.
- Jev: t_kafka at 0.89; no_match 0.10
- Fallback: t_drift

**37. `e_hw_autograder`** (keyword_trap)
- Job: Physical Design Engineer - Static Timing Analysis - Annapurna Labs - Cloud Scale Machine Learning
- Requirements: Hands-on static timing analysis and timing closure for ASIC designs; Knowledge of physical design flows: floorplanning, place and route and clock tree synthesis; Scripting in Tcl and Python to automate design flows; Experience with Synopsys PrimeTime or similar sign-off tools; Understanding of deep sub-micron process technology; BS or MS in electrical engineering
- Item: Teaching Assistant @ Halden State University Computer Science Department
  - `h_autograder` (poor fit, Jev 0.13, fallback pick): Built a Python autograder that cut assignment turnaround from 4 days to 1.
  - `h_grade` (poor fit, Jev 0.03): Graded and wrote feedback on 120 data-structures assignments each term.
- Label: no_match (poor: h_autograder, h_grade). A Python autograder repeats the posting's Python scripting and automation, but nothing in the item shows chip design.
- Jev: no_match at 0.84; no_match 0.84
- Fallback: h_autograder

**38. `e_rbi_forecast`** (keyword_trap)
- Job: Data Engineer 1
- Requirements: Design, build and maintain data pipelines and ETL processes; Strong programming skills in Python; Hands-on experience with AWS services such as S3, Glue, EMR, SQS and Athena; Infrastructure as code, for example Terraform or CloudFormation; Work with data scientists and analysts to keep data accurate and consistent; Experience with SQL and relational databases
- Item: Associate Data Scientist @ Halcyon Health Analytics
  - `hc_dbt` (best, Jev 0.72, fallback pick): Shipped a dbt model layer over Snowflake that consolidated 6 reporting pipelines into 1 tested source.
  - `hc_forecast` (-, Jev 0.10): Built demand forecasts for 34 clinic sites in Python and statsmodels, reducing weekly schedule error by 19%.
  - `hc_ab` (-, Jev 0.00): Partnered with 3 product teams on an A/B testing framework now used for every release readout.
- Label: hc_dbt. The dbt consolidation of reporting pipelines is pipeline work; the forecasting bullet repeats the posting's Python.
- Jev: hc_dbt at 0.72; no_match 0.18
- Fallback: hc_dbt

**39. `e_next_teaching`** (keyword_trap)
- Job: Machine Learning Engineer New Grad
- Requirements: Train, evaluate and deploy machine learning models for ranking and recommendation; Strong Python; experience with PyTorch or TensorFlow; Build data and training pipelines that scale to large datasets; Monitor deployed models for quality, drift and latency; Run online experiments to measure model impact; Solid grounding in machine learning and statistics fundamentals
- Item: Teaching Assistant @ Halden State University Computer Science Department
  - `h_grade` (poor fit, Jev 0.01): Graded and wrote feedback on 120 data-structures assignments each term.
  - `h_lab` (poor fit, Jev 0.01): Led weekly lab sections for 30 students on Git, Linux and testing practice.
  - `h_autograder` (poor fit, Jev 0.41, fallback pick): Built a Python autograder that cut assignment turnaround from 4 days to 1.
- Label: no_match (poor: h_grade, h_lab, h_autograder). Python, Linux and testing words appear, but none of the teaching assistant bullets shows training or deploying models.
- Jev: no_match at 0.57; no_match 0.57
- Fallback: h_autograder

**40. `e_esri_go`** (keyword_trap)
- Job: Data Scientist I
- Requirements: Map business problems to machine learning or other advanced analytics approaches; Build predictive models using statistics and machine learning, including feature engineering and model selection; Write clean, version-controlled Python to process large datasets; Deploy models to production in a cloud environment; Explain results and recommendations clearly to customers; Degree in statistics, data science, computer science or a related field
- Item: Software Engineering Intern @ Northwind Systems
  - `w_go` (poor fit, Jev 0.12): Built a Go service that replaced 3 cron jobs and processes 200k records an hour.
  - `w_graphql` (poor fit, Jev 0.01): Added GraphQL resolvers for 9 entity types on top of the existing REST gateway.
  - `w_cypress` (poor fit, Jev 0.00): Wrote Cypress end-to-end tests covering the 5 highest-traffic user flows.
- Label: no_match (poor: w_go, w_graphql, w_cypress). 'Processes 200k records an hour' echoes processing large datasets, but it is a service, not data science; none shows modelling.
- Jev: no_match at 0.87; no_match 0.87
- Fallback: no_match

**41. `f_esri_churn`** (single)
- Job: Data Scientist I
- Requirements: Map business problems to machine learning or other advanced analytics approaches; Build predictive models using statistics and machine learning, including feature engineering and model selection; Write clean, version-controlled Python to process large datasets; Deploy models to production in a cloud environment; Explain results and recommendations clearly to customers; Degree in statistics, data science, computer science or a related field
- Item: Data Science Intern @ Fairhaven Retail Group
  - `f_churn` (best, Jev 0.78): Trained a scikit-learn churn classifier on 480k subscription records, raising AUC from 0.71 to 0.83 against the rule-based baseline.
- Label: f_churn. The only variant is the churn classifier, which is the job.
- Jev: f_churn at 0.78; no_match 0.22
- Fallback: no_match

**42. `f_tebra_connector`** (single)
- Job: Data Engineer
- Requirements: Build and maintain scalable data pipelines for batch and real-time processing; Turn raw healthcare data into high-quality datasets and real-time features for machine learning models; Strong SQL and Python; Experience with a cloud data warehouse such as Snowflake or BigQuery; Improve data quality with tests, monitoring and governance; Experience with workflow orchestration tools such as Airflow; Work with ML engineers, data scientists and software engineers
- Item: Feed Forge
  - `fe_connector` (best, Jev 0.54): Wrote a Kafka-to-Postgres connector with schema evolution and a replay mode that handles 5k messages a second on a laptop.
- Label: fe_connector. The only variant is a streaming connector, which fits the pipeline role.
- Jev: fe_connector at 0.54; no_match 0.46
- Fallback: no_match

**43. `f_fs_app`** (single)
- Job: Full Stack Developer
- Requirements: Build server-side application logic with Python frameworks such as Flask, Django or FastAPI; Build responsive user interfaces with JavaScript and a modern framework such as React; Design and consume REST APIs; Work with relational and NoSQL databases; Write automated tests and use Git and CI/CD; Work in an agile team
- Item: Split Sum
  - `sp_app` (best, Jev 0.71): Built a Next.js and Postgres app that settles group expenses in the fewest transfers.
- Label: sp_app. The only variant is the web app on Postgres, which fits.
- Jev: sp_app at 0.71; no_match 0.29
- Fallback: no_match

**44. `f_milw_auto`** (single)
- Job: Machine Learning Engineer I
- Requirements: Deploy machine learning models on embedded and edge devices; Applied experience with supervised and unsupervised learning, such as classification and anomaly detection; Experience with deep learning methods such as CNNs, autoencoders or transformers; Time series or signal processing experience; Proficiency in Python and C or C++; Version control and modern software development tools
- Item: Signal Sift
  - `s_auto` (best, Jev 0.92): Detects anomalies in 12-channel accelerometer streams with a lightweight autoencoder.
- Label: s_auto. The only variant is the autoencoder, which fits the edge ML role.
- Jev: s_auto at 0.92; no_match 0.08
- Fallback: no_match

**45. `f_fe_churn`** (single)
- Job: Front End Engineer - AWS APO Engineering
- Requirements: Build responsive web applications with React and TypeScript; Expert HTML, CSS and JavaScript; Design accessible, reusable user interface components; Optimize front end performance and write front end tests; Partner with designers and product managers; Consume REST and GraphQL APIs
- Item: Data Science Intern @ Fairhaven Retail Group
  - `f_churn` (poor fit, Jev 0.00): Trained a scikit-learn churn classifier on 480k subscription records, raising AUC from 0.71 to 0.83 against the rule-based baseline.
- Label: no_match (poor: f_churn). A churn classifier is not front end work.
- Jev: no_match at 1.00; no_match 1.00
- Fallback: no_match

**46. `f_hw_connector`** (single)
- Job: Physical Design Engineer - Static Timing Analysis - Annapurna Labs - Cloud Scale Machine Learning
- Requirements: Hands-on static timing analysis and timing closure for ASIC designs; Knowledge of physical design flows: floorplanning, place and route and clock tree synthesis; Scripting in Tcl and Python to automate design flows; Experience with Synopsys PrimeTime or similar sign-off tools; Understanding of deep sub-micron process technology; BS or MS in electrical engineering
- Item: Feed Forge
  - `fe_connector` (poor fit, Jev 0.00): Wrote a Kafka-to-Postgres connector with schema evolution and a replay mode that handles 5k messages a second on a laptop.
- Label: no_match (poor: fe_connector). A Kafka connector is not chip design.
- Jev: no_match at 1.00; no_match 1.00
- Fallback: no_match

**47. `f_reactor_chess`** (single)
- Job: Reactor Software Engineer 1/2
- Requirements: Build software that operates and tests hardware systems and hardware-in-the-loop environments; Process and stream live telemetry and instrument signals; Strong C++ or Python; Build operator tools and simulations; Design systems that respond predictably in off-nominal conditions; Test automation practices
- Item: Chess Ledger
  - `ch_parse` (poor fit, Jev 0.02): Parses 50k PGN games into MongoDB and reports opening win rates by rating band.
- Label: no_match (poor: ch_parse). Parsing chess games into MongoDB is not reactor control software.
- Jev: no_match at 0.98; no_match 0.98
- Fallback: no_match

**48. `f_hw_autoencoder`** (single)
- Job: Physical Design Engineer - Static Timing Analysis - Annapurna Labs - Cloud Scale Machine Learning
- Requirements: Hands-on static timing analysis and timing closure for ASIC designs; Knowledge of physical design flows: floorplanning, place and route and clock tree synthesis; Scripting in Tcl and Python to automate design flows; Experience with Synopsys PrimeTime or similar sign-off tools; Understanding of deep sub-micron process technology; BS or MS in electrical engineering
- Item: Signal Sift
  - `s_auto` (poor fit, Jev 0.00): Detects anomalies in 12-channel accelerometer streams with a lightweight autoencoder.
- Label: no_match (poor: s_auto). An anomaly detector on accelerometer streams is not chip timing analysis.
- Jev: no_match at 1.00; no_match 1.00
- Fallback: no_match

## Baseline agreements (36)

**49. `c_ds_mastercard`** (clear_match)
- Job: Data Scientist I
- Requirements: Apply data science techniques to large datasets, from development to deployment support; Build and deploy interactive dashboards with data engineers and developers; Communicate with clients and stakeholders and make sure their requirements are met; Strong foundation in statistics and machine learning; Proficiency in Python and SQL; Run customer trials and present results
- Label: data_science. Statistics, analysis and client-facing results: data science.
- Jev: data_science at 0.99 (data_science 0.99, none 0.01, data_engineering 0.00, machine_learning 0.00)
- Fallback: data_science

**50. `c_de_tebra`** (clear_match)
- Job: Data Engineer
- Requirements: Build and maintain scalable data pipelines for batch and real-time processing; Turn raw healthcare data into high-quality datasets and real-time features for machine learning models; Strong SQL and Python; Experience with a cloud data warehouse such as Snowflake or BigQuery; Improve data quality with tests, monitoring and governance; Experience with workflow orchestration tools such as Airflow; Work with ML engineers, data scientists and software engineers
- Label: data_engineering. Pipelines, warehouse, orchestration and data quality: data engineering.
- Jev: data_engineering at 0.99 (data_engineering 0.99, none 0.01, data_science 0.00, machine_learning 0.00)
- Fallback: data_engineering

**51. `c_ml_nextdoor`** (clear_match)
- Job: Machine Learning Engineer New Grad
- Requirements: Train, evaluate and deploy machine learning models for ranking and recommendation; Strong Python; experience with PyTorch or TensorFlow; Build data and training pipelines that scale to large datasets; Monitor deployed models for quality, drift and latency; Run online experiments to measure model impact; Solid grounding in machine learning and statistics fundamentals
- Label: machine_learning. Train, deploy and monitor models: machine learning engineering.
- Jev: machine_learning at 0.99 (machine_learning 0.99, none 0.01, data_engineering 0.00, data_science 0.00)
- Fallback: machine_learning

**52. `c_swe_ascend`** (clear_match)
- Job: Software Engineer
- Requirements: Design and build the APIs and backend services a financial platform runs on; Write clean, well-tested, maintainable code; Experience with relational databases and SQL; Ship features end to end, from design through production; Familiarity with cloud infrastructure and containers; Strong computer science fundamentals, including data structures and algorithms
- Label: software_engineering. Backend services, APIs and tested code: software engineering.
- Jev: software_engineering at 1.00 (software_engineering 1.00, data_engineering 0.00, data_science 0.00, machine_learning 0.00)
- Fallback: software_engineering

**53. `c_ds_mg`** (clear_match)
- Job: Junior Data Scientist - Fraud & AML Monitoring
- Requirements: Use data to monitor for and detect fraud and financial crime; Build reports and dashboards that stakeholders rely on to make decisions; Solid foundation in statistics and data analysis; Proficiency in SQL and Python or R; Present analytical findings to non-technical colleagues; Care for data accuracy and quality
- Label: data_science. Statistics, SQL and reporting on fraud: data science.
- Jev: data_science at 0.99 (data_science 0.99, none 0.01, data_engineering 0.00, machine_learning 0.00)
- Fallback: data_science

**54. `c_de_rbi`** (clear_match)
- Job: Data Engineer 1
- Requirements: Design, build and maintain data pipelines and ETL processes; Strong programming skills in Python; Hands-on experience with AWS services such as S3, Glue, EMR, SQS and Athena; Infrastructure as code, for example Terraform or CloudFormation; Work with data scientists and analysts to keep data accurate and consistent; Experience with SQL and relational databases
- Label: data_engineering. ETL pipelines on AWS: data engineering.
- Jev: data_engineering at 1.00 (data_engineering 1.00, data_science 0.00, machine_learning 0.00, none 0.00)
- Fallback: data_engineering

**55. `c_ml_milwaukee`** (clear_match)
- Job: Machine Learning Engineer I
- Requirements: Deploy machine learning models on embedded and edge devices; Applied experience with supervised and unsupervised learning, such as classification and anomaly detection; Experience with deep learning methods such as CNNs, autoencoders or transformers; Time series or signal processing experience; Proficiency in Python and C or C++; Version control and modern software development tools
- Label: machine_learning. Deploying deep learning models on edge devices: machine learning engineering.
- Jev: machine_learning at 0.98 (machine_learning 0.98, none 0.02, data_engineering 0.00, data_science 0.00)
- Fallback: machine_learning

**56. `c_swe_fullstack`** (clear_match)
- Job: Full Stack Developer
- Requirements: Build server-side application logic with Python frameworks such as Flask, Django or FastAPI; Build responsive user interfaces with JavaScript and a modern framework such as React; Design and consume REST APIs; Work with relational and NoSQL databases; Write automated tests and use Git and CI/CD; Work in an agile team
- Label: software_engineering. Server-side code, a React front end and REST APIs: software engineering.
- Jev: software_engineering at 1.00 (software_engineering 1.00, data_engineering 0.00, data_science 0.00, machine_learning 0.00)
- Fallback: software_engineering

**57. `h_ds_ml_cathexis`** (hybrid_title)
- Job: Data Scientist/Machine Learning Engineer
- Requirements: Applied machine learning experience: regression, classification, supervised and unsupervised learning; Strong mathematical background in linear algebra, calculus, probability and statistics; Excellent Python programming skills; Experience with scalable machine learning, such as MapReduce and streaming; Ability to drive a project independently and in a team; MS or PhD in computer science, electrical engineering or statistics preferred
- Label: data_science or machine_learning. Both halves of the title are real: modelling and statistics, and scalable machine learning.
- Jev: machine_learning at 0.77 (machine_learning 0.77, data_science 0.19, none 0.04, data_engineering 0.00)
- Fallback: machine_learning

**58. `h_de_ds_capgemini`** (hybrid_title)
- Job: Junior Data Engineer/Junior Data Scientist
- Requirements: Build ETL pipelines and prepare datasets in SQL and Python; Apply statistical analysis and modelling to answer business questions; Create dashboards and reports for stakeholders; Work with cloud data platforms; Communicate technical results to non-technical teams; Degree in a quantitative or computing field
- Label: data_engineering or data_science. Pipelines and analysis are both asked for.
- Jev: data_engineering at 0.48 (data_engineering 0.48, data_science 0.39, none 0.13, machine_learning 0.00)
- Fallback: data_engineering

**59. `h_analytics_eng_doordash`** (hybrid_title)
- Job: Analytics Engineer, Data Science
- Requirements: Build tested, documented dbt models that analysts and scientists query; Define metrics and own the data behind experiment readouts; Strong SQL and a working knowledge of Python; Partner with data scientists and product managers on analyses; Improve the reliability and discoverability of core datasets; Experience with A/B testing and statistical analysis is a plus
- Label: data_science or data_engineering. Warehouse modelling in service of analysis and experiments.
- Jev: data_engineering at 0.90 (data_engineering 0.90, none 0.06, data_science 0.04, machine_learning 0.00)
- Fallback: data_engineering

**60. `h_data_ai_arizent`** (hybrid_title)
- Job: Data & AI Engineer
- Requirements: Build data pipelines that feed machine learning and generative AI features; Deploy and operate models in production on a cloud platform; Strong Python and SQL; Work with data scientists to turn prototypes into services; Monitor data quality and model behaviour; Experience with orchestration tools such as Airflow
- Label: data_engineering or machine_learning. Pipelines that feed models, and deploying them.
- Jev: data_engineering at 0.84 (data_engineering 0.84, machine_learning 0.12, none 0.04, data_science 0.00)
- Fallback: machine_learning

**61. `h_swe_data_roblox`** (hybrid_title)
- Job: Software Engineer - Data Engineering
- Requirements: Build and improve the data pipelines, datasets and internal tools that power decisions; Turn ambiguous data needs into reliable, reusable data assets; Improve data quality, observability and reliability across the stack; Strong programming skills in Python or Java; Partner with engineers, data scientists and product teams; Take ownership of projects early
- Label: data_engineering or software_engineering. Software craft applied to data pipelines.
- Jev: data_engineering at 0.99 (data_engineering 0.99, none 0.01, data_science 0.00, machine_learning 0.00)
- Fallback: software_engineering

**62. `h_ml_ds`** (hybrid_title)
- Job: ML Data Scientist
- Requirements: Train predictive models and run offline evaluations; Feature engineering on large tabular and event data; Design and analyse online experiments; Take models from notebook to production with engineers; Strong Python, SQL and statistics; Explain model behaviour to product partners
- Label: data_science or machine_learning. Modelling and experiment analysis, with some productionizing.
- Jev: data_science at 0.66 (data_science 0.66, machine_learning 0.31, none 0.03, data_engineering 0.00)
- Fallback: machine_learning

**63. `h_swe_ml_platform`** (hybrid_title)
- Job: Software Engineer, Machine Learning Platform
- Requirements: Build the services and APIs that train and serve machine learning models; Strong Python or Go; Experience with containers and Kubernetes; Build tooling and automation for data scientists and ML engineers; Design for reliability, scale and observability; Write tested, maintainable code
- Label: software_engineering or machine_learning. Software engineering for an ML platform.
- Jev: software_engineering at 0.75 (software_engineering 0.75, machine_learning 0.22, none 0.03, data_engineering 0.00)
- Fallback: machine_learning

**64. `m_analyst_pipelines`** (title_mismatch)
- Job: Data Analyst
- Requirements: Build and maintain Airflow pipelines that load the company's warehouse; Write production SQL and Python to transform raw event data; Model warehouse tables with dbt and add tests and freshness checks; Monitor pipeline failures and fix them; Work with analysts who consume the data; Experience with Snowflake or BigQuery
- Label: data_engineering. The title says analyst; every duty is pipeline and warehouse engineering.
- Jev: data_engineering at 1.00 (data_engineering 1.00, data_science 0.00, machine_learning 0.00, none 0.00)
- Fallback: data_science

**65. `m_swe_models`** (title_mismatch)
- Job: Software Engineer
- Requirements: Train and evaluate deep learning models in PyTorch on GPUs; Run experiments and read the literature to improve model accuracy; Build data loaders and training pipelines that scale; Ship trained models to production services; Strong Python and machine learning fundamentals; Track model quality after release
- Label: machine_learning. The title says software engineer; the work is training and shipping models.
- Jev: machine_learning at 0.93 (machine_learning 0.93, software_engineering 0.05, none 0.01, data_engineering 0.00)
- Fallback: software_engineering

**66. `m_mle_webapps`** (title_mismatch)
- Job: Machine Learning Engineer
- Requirements: Build REST APIs and backend services in Python; Build the React front end customers use; Design PostgreSQL schemas and write migrations; Write automated tests and maintain CI/CD; Work in an agile team shipping weekly; Experience deploying services on AWS
- Label: software_engineering. The title says machine learning; there is no model work in the duties.
- Jev: software_engineering at 0.97 (software_engineering 0.97, machine_learning 0.02, none 0.01, data_engineering 0.00)
- Fallback: machine_learning

**67. `m_scientist_etl`** (title_mismatch)
- Job: Data Scientist
- Requirements: Build and maintain ETL pipelines into the data warehouse; Own orchestration and scheduling of daily batch jobs; Keep warehouse tables accurate, documented and tested; Write SQL and Python to move data between systems; Work with the analysts who read the tables; Experience with cloud data platforms
- Label: data_engineering. The title says data scientist; the duties are pipelines and warehousing.
- Jev: data_engineering at 1.00 (data_engineering 1.00, data_science 0.00, machine_learning 0.00, none 0.00)
- Fallback: data_science

**68. `m_research_ranking`** (title_mismatch)
- Job: Research Scientist
- Requirements: Develop ranking and recommendation models and ship them to production; Design and run online A/B tests to measure model impact; Train deep learning models on large behavioural datasets; Strong Python and PyTorch; Monitor deployed models for drift; Publish and read machine learning research
- Label: machine_learning. The title says research; the duties are building and shipping models.
- Jev: machine_learning at 0.90 (machine_learning 0.90, data_science 0.06, none 0.04, data_engineering 0.00)
- Fallback: none

**69. `m_analytics_eng_stats`** (title_mismatch)
- Job: Analytics Engineer
- Requirements: Fit statistical and causal models to estimate the effect of product changes; Design experiments and analyse their results; Forecast demand and report uncertainty; Strong statistics, R or Python; Present findings to leadership; SQL for data extraction
- Label: data_science. The title says analytics engineer; the duties are statistical modelling and experiments.
- Jev: data_science at 0.98 (data_science 0.98, none 0.02, data_engineering 0.00, machine_learning 0.00)
- Fallback: data_engineering

**70. `m_data_engineer_services`** (title_mismatch)
- Job: Data Engineer
- Requirements: Build backend microservices and REST APIs in Go; Design and operate services on Kubernetes; Build internal tools used by other engineering teams; Write automated tests and review code; Strong computer science fundamentals; Experience with relational databases
- Label: software_engineering. The title says data engineer; the duties are backend services.
- Jev: software_engineering at 0.94 (software_engineering 0.94, data_engineering 0.05, none 0.01, data_science 0.00)
- Fallback: data_engineering

**71. `n_product_manager`** (no_track)
- Job: Product Manager
- Requirements: Own the roadmap and prioritize features for a B2B product; Write product requirements and work with engineering to ship them; Run customer interviews and synthesize feedback; Define success metrics and track them; Communicate strategy to executives; Experience in enterprise software
- Label: none. A product role: none of the four tracks.
- Jev: none at 1.00 (none 1.00, data_engineering 0.00, data_science 0.00, machine_learning 0.00)
- Fallback: none

**72. `n_ux_designer`** (no_track)
- Job: UX Designer
- Requirements: Design user flows, wireframes and high-fidelity prototypes; Run usability tests and turn findings into design changes; Maintain a design system in Figma; Collaborate with product managers and engineers; Portfolio demonstrating end-to-end design work; Knowledge of accessibility standards
- Label: none. A design role: none of the four tracks.
- Jev: none at 1.00 (none 1.00, data_engineering 0.00, data_science 0.00, machine_learning 0.00)
- Fallback: none

**73. `n_timing_analysis`** (no_track)
- Job: Physical Design Engineer - Static Timing Analysis
- Requirements: Hands-on static timing analysis and timing closure for ASIC designs; Knowledge of physical design flows: floorplanning, place and route and clock tree synthesis; Scripting in Tcl and Python to automate design flows; Experience with Synopsys PrimeTime or similar sign-off tools; Understanding of deep sub-micron process technology; BS or MS in electrical engineering
- Label: none. Chip design: none of the four tracks.
- Jev: none at 1.00 (none 1.00, data_engineering 0.00, data_science 0.00, machine_learning 0.00)
- Fallback: none

**74. `n_security_analyst`** (no_track)
- Job: Cybersecurity Analyst
- Requirements: Monitor security alerts and investigate incidents; Perform vulnerability assessments and recommend fixes; Knowledge of network protocols and firewalls; Maintain incident response runbooks; Security certification such as Security+ preferred; Communicate risks to non-technical staff
- Label: none. A security operations role: none of the four tracks.
- Jev: none at 1.00 (none 1.00, data_engineering 0.00, data_science 0.00, machine_learning 0.00)
- Fallback: none

**75. `n_account_exec`** (no_track)
- Job: Account Executive
- Requirements: Own a quota and close new business; Run discovery calls and product demonstrations; Build and manage a sales pipeline in a CRM; Negotiate contracts with customers; Collaborate with marketing on campaigns; Two years of B2B sales experience
- Label: none. A sales role: none of the four tracks.
- Jev: none at 1.00 (none 1.00, data_engineering 0.00, data_science 0.00, machine_learning 0.00)
- Fallback: none

**76. `n_mechanical`** (no_track)
- Job: Mechanical Design Engineer
- Requirements: Design mechanical components and assemblies in SolidWorks; Run tolerance analysis and finite element simulations; Prepare drawings for manufacturing; Work with suppliers on prototypes and production; Test prototypes and iterate on designs; BS in mechanical engineering
- Label: none. A mechanical role: none of the four tracks.
- Jev: none at 1.00 (none 1.00, data_engineering 0.00, data_science 0.00, machine_learning 0.00)
- Fallback: none

**77. `n_recruiter`** (no_track)
- Job: Technical Recruiter
- Requirements: Source and screen engineering candidates; Partner with hiring managers on role requirements; Manage candidates through an applicant tracking system; Schedule interviews and keep candidates informed; Report on hiring pipeline metrics; Experience recruiting for technical roles
- Label: none. A recruiting role: none of the four tracks.
- Jev: none at 0.98 (none 0.98, software_engineering 0.02, data_engineering 0.00, data_science 0.00)
- Fallback: none

**78. `x_data_annotation`** (near_miss)
- Job: Data Annotation Specialist
- Requirements: Label images and text to the project's guidelines for machine learning training data; Review other annotators' work for consistency; Flag ambiguous examples to the machine learning team; Keep annotation throughput and quality targets; Attention to detail; No programming experience required
- Label: none. Machine learning vocabulary, but the job is labelling, not modelling or engineering.
- Jev: none at 0.97 (none 0.97, machine_learning 0.02, data_science 0.01, data_engineering 0.00)
- Fallback: none

**79. `x_data_governance`** (near_miss)
- Job: Data Governance Analyst
- Requirements: Maintain the data catalog and business glossary; Define data quality rules with business owners and track exceptions; Document data lineage for regulatory audits; Run data stewardship meetings and training; Knowledge of privacy and compliance regulations; Basic SQL for checking data
- Label: none. Data vocabulary, but the work is policy and stewardship, not building pipelines or models.
- Jev: none at 0.92 (none 0.92, data_engineering 0.08, data_science 0.00, machine_learning 0.00)
- Fallback: none

**80. `x_solutions_engineer`** (near_miss)
- Job: Solutions Engineer, Data Platform
- Requirements: Run technical demos of the data platform for prospective customers; Scope and support customer proofs of concept; Answer technical questions on sales calls; Work with account executives to close deals; Write sample queries and scripts to illustrate the product; Excellent presentation skills
- Label: none. Data platform vocabulary, but a pre-sales role: demos and customer calls.
- Jev: none at 0.82 (none 0.82, data_engineering 0.10, software_engineering 0.08, data_science 0.00)
- Fallback: data_engineering

**81. `x_tech_writer_ml`** (near_miss)
- Job: Technical Writer, Machine Learning Documentation
- Requirements: Write and maintain documentation for machine learning APIs and tutorials; Interview engineers and researchers to understand features; Edit release notes and guides; Keep documentation in version control; Excellent written English; Familiarity with machine learning concepts
- Label: none. Machine learning vocabulary, but a writing role.
- Jev: none at 0.78 (none 0.78, machine_learning 0.21, software_engineering 0.01, data_engineering 0.00)
- Fallback: machine_learning

**82. `x_it_support`** (near_miss)
- Job: IT Support Specialist
- Requirements: Resolve hardware and software tickets for employees; Set up laptops and manage user accounts; Write simple SQL queries to look up records in the ticketing system; Document solutions in the knowledge base; Customer service mindset; CompTIA A+ preferred
- Label: none. A little SQL and scripting, but a help desk job.
- Jev: none at 1.00 (none 1.00, data_engineering 0.00, data_science 0.00, machine_learning 0.00)
- Fallback: none

**83. `x_qa_manual`** (near_miss)
- Job: QA Analyst (Manual Testing)
- Requirements: Execute manual test plans for web and mobile releases; Log defects with clear reproduction steps; Verify fixes and regression-test releases; Work with developers to triage bugs; Attention to detail; No coding required
- Label: none. Software vocabulary, but manual testing with no engineering.
- Jev: none at 0.99 (none 0.99, software_engineering 0.01, data_engineering 0.00, data_science 0.00)
- Fallback: none

**84. `x_financial_analyst`** (near_miss)
- Job: Financial Analyst
- Requirements: Build Excel models to forecast revenue and budgets; Prepare monthly variance reports for finance leadership; Analyse spending trends and flag anomalies; Support the annual planning cycle; Strong Excel; SQL a plus; Bachelor's degree in finance or accounting
- Label: none. Forecasting and analysis vocabulary, but a finance role in Excel.
- Jev: none at 0.99 (none 0.99, data_science 0.01, data_engineering 0.00, machine_learning 0.00)
- Fallback: none

