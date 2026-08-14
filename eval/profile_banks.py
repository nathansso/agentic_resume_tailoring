"""Authored raw material for the benchmark profile set (issue #172).

This module is **content**; `eval/profile_generator.py` is **mechanism**. The
split is deliberate: composition rules, hazard transforms and rendering are code
that gets unit-tested, while the résumé material is prose that gets *read* — and
the two want completely different review.

**Why authored slots rather than templated bullets.** LongMemEval
(arXiv:2410.10813) generated one background per attribute and then had three
in-house NLP researchers rewrite all 500 questions by hand — roughly 400 human
hours. WikiDYK (arXiv:2505.12306) generated 12,290 facts with no human
validation and its own Limitations section carries that as the weakness. A
bullet bank sampled at random would have produced the WikiDYK failure mode
inside our own repo: fifteen profiles that differ by a shuffle, whose measured
"stratum effect" is a template artefact. So the raw material is authored per
person, and the generator's job is to compose, transform and render it — not to
invent it.

Fifteen people, five role families x three variants:

| variant       | breadth    | evidence density | redundancy |
|---------------|------------|------------------|------------|
| `specialist`  | specialist | metric_rich      | clean      |
| `generalist`  | generalist | metric_rich      | clean      |
| `metric_poor` | specialist | metric_poor      | clean      |

Each variant differs from `specialist` on **exactly one axis**, so a contrast
against it isolates that axis. A fourth profile per family is *derived* rather
than authored — `specialist` plus `redundant_role_bullets` /
`redundant_project_bullets` appended — which makes the redundancy contrast a
matched pair on the same synthetic person. See the generator's module docstring.

**Level is authored, not computed.** An intern's material (a summer internship,
a campus job, a degree in progress) is different material from a new grad's, so
`level` is a property of the slot rather than a label applied to it afterwards.
The set is balanced so that every family carries both levels and every variant
carries both levels — see `tests/test_profile_set.py`.

**Every person here is synthetic.** `Alex Rivera` in `benchmark_profile.md` is
the precedent and it is not negotiable: no real user résumé ever enters `eval/`.

**Two vocabulary hazards constrain the wording**, both discovered by reading
`agents/ats_scorer.py` rather than by running anything:

* `_detect_level()` returns the **highest** seniority tier whose keyword appears
  *anywhere* in the résumé text, and its `lead` tier matches the bare substring
  `lead`. So "leading", "leadership", "leaderboard" and "team lead" all promote a
  student résumé to lead-level, as do "senior" (as in "senior capstone"),
  "staff" (as in "staffing"), "principal" (as in "principal component analysis")
  and "manager" (as in "package manager"). None of those words appear here, and
  `tests/test_profile_set.py` asserts no profile detects above `junior`.
* `intern` is the *lowest* tier, so an intern-level profile must avoid `junior`,
  `associate` and `entry level` entirely, while an entry-level profile may
  mention its internships freely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# ── shapes ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Role:
    company: str
    title: str
    dates: str
    bullets: Tuple[str, ...]


@dataclass(frozen=True)
class Project:
    name: str
    descriptor: str
    bullets: Tuple[str, ...]
    url: Optional[str] = None
    # Seeded onto `Project.metrics` post-ingest by the harness. Shape is what
    # `services.py::_build_repo_metrics()` produces, so the row is
    # indistinguishable from one a real GitHub ingest wrote. Closes the fixture
    # gap #155's deviations recorded.
    github: Optional[Dict] = None


@dataclass(frozen=True)
class Slot:
    """One authored person, before composition and rendering."""
    family: str
    variant: str
    level: str
    name: str
    handle: str
    summary: str
    education: str
    roles: Tuple[Role, ...]
    projects: Tuple[Project, ...]
    achievements: Tuple[str, ...] = ()
    # Redundancy-bearing derivative only: appended to roles[0] / projects[0].
    # Authored rather than generated — mechanical paraphrase reads like
    # mechanical paraphrase, and the thing being measured is résumé padding as a
    # human actually writes it.
    redundant_role_bullets: Tuple[str, ...] = ()
    redundant_project_bullets: Tuple[str, ...] = ()


# ── family vocabularies ───────────────────────────────────────────────────────
#
# `core` is the family's own toolchain; `adjacent` is the neighbouring families'.
# A specialist lists `core`; a generalist lists `core + adjacent`. That is the
# whole breadth transform, and it is deliberately a vocabulary difference rather
# than a wording difference so the axis stays isolated.

FAMILY_VOCAB: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "data_science": {
        "core": ("Python", "R", "SQL", "pandas", "NumPy", "scikit-learn",
                 "statsmodels", "SciPy", "Jupyter", "matplotlib", "seaborn",
                 "Tableau", "A/B testing", "Postgres", "Git"),
        "adjacent": ("PyTorch", "Spark", "Airflow", "dbt", "Snowflake", "AWS",
                     "Docker", "FastAPI", "BigQuery", "Streamlit", "Looker",
                     "Excel"),
    },
    "data_engineering": {
        "core": ("Python", "SQL", "Spark", "Airflow", "dbt", "Snowflake",
                 "Postgres", "Kafka", "AWS", "Docker", "Terraform", "BigQuery",
                 "Parquet", "Bash", "Git"),
        "adjacent": ("Scala", "Kubernetes", "Databricks", "Flink", "pandas",
                     "Tableau", "MongoDB", "Redis", "Java", "Looker",
                     "GitHub Actions", "Linux"),
    },
    "ml_engineering": {
        "core": ("Python", "PyTorch", "scikit-learn", "TensorFlow", "MLflow",
                 "NumPy", "pandas", "Docker", "AWS", "SageMaker", "ONNX",
                 "FastAPI", "SQL", "Weights & Biases", "Git"),
        "adjacent": ("Spark", "Airflow", "Kafka", "Ray", "C++", "Hugging Face",
                     "Postgres", "Terraform", "Grafana", "Kubernetes", "Redis",
                     "CUDA"),
    },
    "software_engineering": {
        "core": ("Java", "Python", "TypeScript", "JavaScript", "React",
                 "Node.js", "PostgreSQL", "Spring Boot", "REST APIs", "pytest",
                 "JUnit", "Docker", "Linux", "Git", "GitHub Actions"),
        "adjacent": ("Go", "Kubernetes", "GraphQL", "Redis", "Kafka", "Next.js",
                     "MongoDB", "Terraform", "AWS", "Cypress", "gRPC", "Jenkins"),
    },
    "ai_engineering": {
        "core": ("Python", "LangChain", "OpenAI API", "Hugging Face", "FastAPI",
                 "RAG", "pgvector", "prompt engineering", "PyTorch",
                 "sentence-transformers", "Streamlit", "SQLite", "Docker",
                 "TypeScript", "Git"),
        "adjacent": ("React", "AWS", "Postgres", "Redis", "Kubernetes",
                     "Next.js", "Node.js", "Anthropic API", "Airflow", "MLflow",
                     "Pinecone", "vLLM"),
    },
}

FAMILIES: Tuple[str, ...] = tuple(FAMILY_VOCAB)

# Variant → the strata it fixes. `redundancy` is set by the generator for the
# derived variant; every authored slot is clean.
VARIANT_STRATA: Dict[str, Dict[str, str]] = {
    "specialist":  {"breadth": "specialist", "evidence_density": "metric_rich"},
    "generalist":  {"breadth": "generalist", "evidence_density": "metric_rich"},
    "metric_poor": {"breadth": "specialist", "evidence_density": "metric_poor"},
}

VARIANTS: Tuple[str, ...] = tuple(VARIANT_STRATA)


# ── data science ──────────────────────────────────────────────────────────────

_DS_SPECIALIST = Slot(
    family="data_science", variant="specialist", level="intern",
    name="Priya Raman", handle="praman-stats",
    summary=(
        "Statistics undergraduate focused on applied modeling and experiment "
        "analysis. Comfortable taking a question from messy source data to a "
        "readout a non-technical team can act on."),
    education="B.S. Statistics, Cascade State University (Expected May 2027), GPA: 3.8",
    roles=(
        Role("Fairhaven Retail Group", "Data Science Intern", "Jun 2026 – Aug 2026", (
            "Built a customer churn model in scikit-learn over 480k subscription records, raising AUC from 0.71 to 0.83 against the incumbent rule set.",
            "Ran an A/B testing readout on 12 store-level promotions, using bootstrap confidence intervals to flag 3 as statistically flat.",
            "Automated a weekly cohort report in pandas and matplotlib, cutting a 6-hour manual Excel process to 15 minutes.",
        )),
        Role("Cascade State University Statistics Department", "Undergraduate Data Assistant", "Sep 2025 – Present", (
            "Cleaned and joined 9 semesters of enrollment data in SQL and pandas, resolving 4,200 duplicate student records.",
            "Fit mixed-effects models in R to estimate course-level grade variation across 38 sections.",
            "Wrote a seaborn plotting toolkit reused by 5 graduate researchers for thesis figures.",
        )),
    ),
    projects=(
        Project("Churn Explorer", "Retention analysis dashboard", (
            "Interactive retention dashboard over a public telecom dataset, segmenting 7,000 customers by tenure and contract type.",
            "Implemented survival analysis and surfaced per-segment hazard curves with matplotlib.",
        ), url="github.com/praman-stats/churn-explorer",
            github={"stars": 24, "languages": ["Python"], "readme_length": 1450,
                    "contributors": 1, "author_commits": 63, "total_commits": 63,
                    "project_type": "self_project"}),
        Project("Grade Signal", "Course outcome analysis", (
            "Analyzed 11 years of public grade distributions in pandas, testing for inflation with a linear trend model.",
            "Published the work as a reproducible Jupyter notebook with 30 unit-tested transformation functions.",
        )),
    ),
    achievements=("**2nd Place, Cascade Analytics Challenge** (2026) — forecasting track, team of 4",),
    redundant_role_bullets=(
        "Built churn prediction models in scikit-learn for 480k subscription records, improving AUC over the incumbent rule set.",
        "Built the weekly cohort reporting in pandas and matplotlib, replacing a manual Excel process for the retail analytics team.",
        "Built bootstrap confidence intervals into the promotion readout covering 12 store-level promotions.",
    ),
    redundant_project_bullets=(
        "Built an interactive dashboard over the same public telecom dataset, segmenting customers by tenure and contract type.",
    ),
)

_DS_GENERALIST = Slot(
    family="data_science", variant="generalist", level="entry",
    name="Danielle Okafor", handle="dokafor",
    summary=(
        "Data scientist who works across the stack the analysis sits on — "
        "warehouse modeling, forecasting, and the reporting layer the business "
        "actually reads."),
    education="B.S. Data Science, Rivermount College (May 2026), GPA: 3.6",
    roles=(
        Role("Halcyon Health Analytics", "Associate Data Scientist", "Jul 2026 – Present", (
            "Built demand forecasts for 34 clinic sites in Python and statsmodels, reducing weekly schedule error by 19%.",
            "Shipped a dbt model layer over Snowflake that consolidated 6 reporting pipelines into 1 tested source.",
            "Partnered with 3 product teams on an A/B testing framework now used for every release readout.",
        )),
        Role("Rivermount College Office of Institutional Research", "Data Science Intern", "Jun 2025 – May 2026", (
            "Migrated a 40-table reporting warehouse from Excel exports to Postgres and Airflow.",
            "Trained a gradient-boosted retention model on 22,000 student records, beating the prior heuristic by 14 points of recall.",
            "Wrote a Docker image that reproduced the analysis stack for 8 downstream analysts.",
        )),
    ),
    projects=(
        Project("Transit Pulse", "Public transit reliability analysis", (
            "Ingested 2 years of GTFS feeds into BigQuery and modeled on-time performance across 190 routes.",
            "Deployed a FastAPI service returning per-route reliability scores, containerized on AWS.",
        ), url="github.com/dokafor/transit-pulse",
            github={"stars": 118, "languages": ["Python", "SQL"], "readme_length": 2600,
                    "contributors": 4, "author_commits": 92, "total_commits": 210,
                    "project_type": "open_source"}),
        Project("Ballot Tally", "Election results pipeline", (
            "Parsed 3,100 county result files with pandas and Spark, reconciling 5 conflicting schemas.",
            "Published an interactive Tableau workbook opened 900 times in its first month.",
        )),
    ),
)

_DS_METRIC_POOR = Slot(
    family="data_science", variant="metric_poor", level="intern",
    name="Tomas Iversen", handle="tiversen",
    summary=(
        "Applied mathematics undergraduate working on statistical modeling and "
        "survey research. Interested in the part of the work that turns a "
        "messy dataset into a defensible claim."),
    education="B.S. Applied Mathematics, Northfield University (Expected June 2027)",
    roles=(
        Role("Meridian Insurance Group", "Data Science Intern", "Jun 2026 – Aug 2026", (
            "Built a claims severity model in scikit-learn and compared it against the existing actuarial baseline.",
            "Explored policyholder segments with clustering and summarized what distinguished them for the pricing team.",
            "Documented the modeling workflow in Jupyter so the analytics team could rerun it each quarter.",
        )),
        Role("Northfield University Mathematics Department", "Research Assistant", "Jan 2026 – Present", (
            "Cleaned survey response data in R and prepared it for a longitudinal study of study habits.",
            "Ran hypothesis tests comparing treatment and control groups and drafted the results section.",
            "Maintained a shared SQL database of the instruments the research group reuses each term.",
        )),
    ),
    projects=(
        Project("Weather Window", "Precipitation forecasting notebook", (
            "Compared regression and tree-based forecasts of daily rainfall using public station data.",
            "Wrote a reusable pandas feature pipeline for lag and rolling-window terms.",
        )),
        Project("Bookshelf Stats", "Reading habit analysis", (
            "Analyzed a personal reading log and visualized genre trends over time with matplotlib.",
            "Added a SQLite loader so the notebook runs from a fresh clone without manual setup.",
        ), url="github.com/tiversen/bookshelf-stats"),
    ),
)

# ── data engineering ──────────────────────────────────────────────────────────

_DE_SPECIALIST = Slot(
    family="data_engineering", variant="specialist", level="entry",
    name="Wei Lin Tan", handle="weilintan",
    summary=(
        "Data engineer working on batch and streaming ingestion. Most of my "
        "work is making a pipeline someone else depends on stop being a "
        "surprise."),
    education="B.S. Computer Science, Lakeshore Institute of Technology (May 2026)",
    roles=(
        Role("Corvid Logistics", "Data Engineer I", "Jun 2026 – Present", (
            "Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery.",
            "Cut warehouse spend 28% by repartitioning the 12 largest Parquet tables and pruning unused columns.",
            "Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting.",
        )),
        Role("Lakeshore Institute of Technology Research Computing", "Data Engineering Intern", "May 2025 – Apr 2026", (
            "Migrated 3 lab pipelines from cron scripts to Airflow, with retries and alerting.",
            "Loaded 1.4TB of sensor archives into Postgres and cut a common query from 90 seconds to 4.",
            "Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command.",
        )),
    ),
    projects=(
        Project("Slate Ingest", "Idempotent file ingestion framework", (
            "Loads 500k-row vendor drops into Postgres with content hashing, so a replayed file is a no-op.",
            "Ships a Docker Compose stack that stands the whole pipeline up in under 2 minutes.",
        ), url="github.com/weilintan/slate-ingest",
            github={"stars": 47, "languages": ["Python", "Dockerfile"], "readme_length": 1900,
                    "contributors": 2, "author_commits": 78, "total_commits": 96,
                    "project_type": "open_source"}),
        Project("Column Drift", "Schema change detector", (
            "Diffs 200 warehouse tables between runs and reports column additions, drops, and type changes.",
            "Posts a daily digest that replaced a manual review of 3 upstream feeds.",
        )),
    ),
    achievements=("**Lakeshore Capstone Award** (2026) — best systems project, cohort of 90",),
    redundant_role_bullets=(
        "Built Airflow DAGs that moved shipment events from Kafka into the Snowflake warehouse at 99.9% delivery.",
        "Built the dbt test coverage across 60 warehouse models, catching schema regressions before reporting saw them.",
        "Built the repartitioning work over the largest Parquet tables that cut warehouse spend 28%.",
    ),
    redundant_project_bullets=(
        "Loads vendor drops into Postgres with content hashing so replaying the same file is a no-op.",
    ),
)

_DE_GENERALIST = Slot(
    family="data_engineering", variant="generalist", level="intern",
    name="Adaeze Nwosu", handle="anwosu",
    summary=(
        "Information systems undergraduate building data infrastructure — "
        "streaming ingestion, warehouse modeling, and the deployment glue that "
        "keeps both running unattended."),
    education="B.S. Information Systems, Grantwood University (Expected May 2027), GPA: 3.7",
    roles=(
        Role("Northgate Media", "Data Engineering Intern", "Jun 2026 – Aug 2026", (
            "Built a Spark job that deduplicated 90M ad impression rows nightly, trimming runtime from 50 minutes to 18.",
            "Modeled 8 core marketing tables in dbt with documented tests and freshness checks.",
            "Shipped a Kafka consumer in Scala that backfilled 3 weeks of missing click events.",
        )),
        Role("Grantwood University Library Systems", "Student Developer", "Sep 2025 – Present", (
            "Automated catalog exports with Python and Bash, replacing a 5-step manual process.",
            "Built a Postgres reporting schema serving 3 dashboards for the circulation team.",
            "Containerized the ingest job with Docker so it runs identically on 2 campus servers.",
        )),
    ),
    projects=(
        Project("Feed Forge", "Streaming ETL toolkit", (
            "Kafka-to-Postgres connector with schema evolution and a replay mode, handling 5k messages a second on a laptop.",
            "Added Terraform and Docker Compose so a full stack starts from 1 command.",
        ), url="github.com/anwosu/feed-forge",
            github={"stars": 61, "languages": ["Python", "Dockerfile", "HCL"], "readme_length": 2100,
                    "contributors": 2, "author_commits": 87, "total_commits": 104,
                    "project_type": "open_source"}),
        Project("Campus Air", "Air-quality data pipeline", (
            "Collected readings from 6 sensors into BigQuery and published a daily summary table.",
            "Wrote 24 pytest cases covering the parser's malformed-payload paths.",
        )),
    ),
)

_DE_METRIC_POOR = Slot(
    family="data_engineering", variant="metric_poor", level="entry",
    name="Gabriel Ferreira", handle="gferreira-dev",
    summary=(
        "Data engineer maintaining the ingestion and warehouse layer for a "
        "freight billing platform. Most of what I do is make failures visible "
        "before someone downstream finds them."),
    education="B.S. Computer Engineering, Porto Vista State University (June 2026)",
    roles=(
        Role("Alder Freight Systems", "Junior Data Engineer", "Jul 2026 – Present", (
            "Maintained the Airflow pipelines that load carrier invoices into the Snowflake warehouse.",
            "Rewrote a fragile shipment ingest job in Python, adding retries and structured logging.",
            "Added dbt tests to the billing models after a schema change reached the reporting layer unnoticed.",
        )),
        Role("Porto Vista State University Facilities Analytics", "Data Engineering Intern", "Jan 2026 – Jun 2026", (
            "Loaded building meter readings into Postgres and documented the table layout for analysts.",
            "Replaced a spreadsheet handoff with a scheduled export written in Python.",
            "Wrote Bash tooling that checked feed freshness before the morning reports ran.",
        )),
    ),
    projects=(
        Project("Ledger Loader", "Personal finance ETL", (
            "Parses bank exports into a normalized Postgres schema with idempotent loads.",
            "Handles duplicate transactions by hashing the source row rather than trusting file order.",
        )),
        Project("Route Cache", "Transit schedule store", (
            "Caches GTFS schedule data in Parquet and serves lookups without a network call.",
            "Includes a Docker Compose setup so a fresh clone runs end to end.",
        ), url="github.com/gferreira-dev/route-cache"),
    ),
)

# ── ML engineering ────────────────────────────────────────────────────────────

_MLE_SPECIALIST = Slot(
    family="ml_engineering", variant="specialist", level="intern",
    name="Sofia Marchetti", handle="smarchetti",
    summary=(
        "Computer science undergraduate training and shipping models — mostly "
        "vision, mostly the part between a working notebook and something that "
        "survives a serving budget."),
    education="B.S. Computer Science, Ardenne Polytechnic (Expected May 2027), GPA: 3.9",
    roles=(
        Role("Vector Health", "Machine Learning Intern", "Jun 2026 – Aug 2026", (
            "Trained a PyTorch sequence model on 1.1M clinical events, improving readmission recall by 12 points over the baseline.",
            "Packaged the model behind a FastAPI endpoint in Docker, holding p95 latency under 80ms.",
            "Tracked 60 experiment runs in MLflow and wrote the comparison that selected the shipped checkpoint.",
        )),
        Role("Ardenne Polytechnic Vision Lab", "Undergraduate Research Assistant", "Sep 2025 – Present", (
            "Fine-tuned 4 image classification backbones on a 40k-image microscopy set, reaching 91% top-1.",
            "Cut training time 35% by moving augmentation into a prefetching data loader.",
            "Wrote evaluation tooling that produced per-class confusion matrices for 3 papers under review.",
        )),
    ),
    projects=(
        Project("TinyDetect", "Compact object detection", (
            "Distilled a detector to 8MB for edge devices, retaining 88% of teacher mAP.",
            "Exported to ONNX and benchmarked 3 runtimes on a Raspberry Pi.",
        ), url="github.com/smarchetti/tinydetect",
            github={"stars": 340, "languages": ["Python", "C++"], "readme_length": 3100,
                    "contributors": 5, "author_commits": 121, "total_commits": 198,
                    "project_type": "open_source"}),
        Project("Rehearsal", "Continual learning experiments", (
            "Implemented 5 replay strategies and measured forgetting across a 10-task sequence.",
            "Reproduced 2 published baselines within 1 point of their reported numbers.",
        )),
    ),
    achievements=("**Ardenne Undergraduate Research Prize** (2026) — awarded to 6 of 210 applicants",),
    redundant_role_bullets=(
        "Trained a PyTorch model over 1.1M clinical events that improved readmission recall against the baseline.",
        "Trained and tracked 60 MLflow experiment runs to select the checkpoint that shipped.",
        "Trained the FastAPI serving path's model to hold p95 latency under 80ms in Docker.",
    ),
    redundant_project_bullets=(
        "Distilled the detector down to 8MB for edge deployment while retaining most of the teacher's mAP.",
    ),
)

_MLE_GENERALIST = Slot(
    family="ml_engineering", variant="generalist", level="entry",
    name="Omar Haddad", handle="ohaddad",
    summary=(
        "Machine learning engineer working across training infrastructure, "
        "serving, and the monitoring that tells you a model has quietly "
        "stopped being right."),
    education="B.S. Computer Science (Statistics minor), Brightwater University (May 2026)",
    roles=(
        Role("Tessellate Robotics", "Associate Machine Learning Engineer", "Jun 2026 – Present", (
            "Built a Ray-backed training pipeline that cut a 9-hour job to 2 hours across 4 GPUs.",
            "Shipped a Kafka feature stream feeding 3 production models with sub-minute freshness.",
            "Added drift monitoring in Grafana that caught a 7% input distribution shift within a day.",
        )),
        Role("Brightwater University Applied AI Center", "Machine Learning Intern", "Jan 2025 – May 2026", (
            "Trained demand models in scikit-learn and PyTorch on 300k transactions for 2 campus partners.",
            "Built an Airflow retraining schedule with automated evaluation gates.",
            "Wrote a Terraform stack provisioning the team's SageMaker training environment.",
        )),
    ),
    projects=(
        Project("Grasp Bench", "Robotic grasp evaluation", (
            "Benchmarks 6 grasp policies in simulation and reports success rate with confidence intervals.",
            "Runs on Kubernetes from a Docker image pinned to a reproducible CUDA build.",
        ), url="github.com/ohaddad/grasp-bench"),
        Project("Signal Sift", "Sensor anomaly detection", (
            "Detects anomalies in 12-channel accelerometer streams with a lightweight autoencoder.",
            "Serves predictions from a FastAPI container backed by Redis for recent-window state.",
        )),
    ),
)

_MLE_METRIC_POOR = Slot(
    family="ml_engineering", variant="metric_poor", level="intern",
    name="Hana Kobayashi", handle="hkobayashi-ml",
    summary=(
        "Data science undergraduate working on ranking and speech models. Most "
        "of my time goes to evaluation — deciding whether a change actually "
        "helped."),
    education="B.S. Data Science, Ridgeline University (Expected December 2027)",
    roles=(
        Role("Solace Labs", "Machine Learning Intern", "Jun 2026 – Sep 2026", (
            "Trained ranking models in PyTorch and compared them against the existing heuristic ordering.",
            "Built an evaluation harness that reported offline metrics for every candidate checkpoint.",
            "Documented the feature preparation steps so the team could rerun the study after I left.",
        )),
        Role("Ridgeline University Language Lab", "Research Assistant", "Sep 2025 – Present", (
            "Prepared annotated speech corpora for model training and wrote the loader in Python.",
            "Ran fine-tuning experiments with scikit-learn and PyTorch and logged the results in MLflow.",
            "Reviewed annotation disagreements with the research group and revised the guidelines.",
        )),
    ),
    projects=(
        Project("Note Sorter", "Handwriting classification", (
            "Classifies scanned lecture notes by course with a small convolutional network.",
            "Includes a Docker image so the training run reproduces on a fresh machine.",
        )),
        Project("Metric Mirror", "Evaluation utilities", (
            "Reports calibration curves and threshold sweeps for binary classifiers as one artifact.",
            "Wraps scikit-learn metrics behind a single interface reused across the lab's projects.",
        ), url="github.com/hkobayashi-ml/metric-mirror"),
    ),
)

# ── software engineering ──────────────────────────────────────────────────────

_SWE_SPECIALIST = Slot(
    family="software_engineering", variant="specialist", level="entry",
    name="Julia Brandt", handle="jbrandt-dev",
    summary=(
        "Backend-leaning full stack engineer. I like the work where the fix is "
        "measurable: a slow page, an untested module, a manual process that "
        "should not be manual."),
    education="B.S. Computer Science, Fairmont University (May 2026), GPA: 3.5",
    roles=(
        Role("Pallas Software", "Software Engineer I", "Jul 2026 – Present", (
            "Shipped 14 REST endpoints in Spring Boot backing a customer portal used by 8,000 accounts.",
            "Cut a checkout page's p95 render from 1.8s to 600ms by batching 5 sequential API calls.",
            "Raised service test coverage from 46% to 78% with JUnit and contract tests.",
        )),
        Role("Fairmont University Campus IT", "Software Engineering Intern", "May 2025 – Jun 2026", (
            "Built a React and TypeScript scheduling tool that replaced a paper process for 12 departments.",
            "Wrote a Node.js sync service moving 30k roster records nightly into PostgreSQL.",
            "Added GitHub Actions CI that ran 220 tests on every pull request.",
        )),
    ),
    projects=(
        Project("Queue Keeper", "Office-hours queue app", (
            "Real-time queue used by 400 students across 6 courses, built in React and Node.js.",
            "Backed by PostgreSQL with WebSocket updates and a 1-command Docker deployment.",
        ), url="github.com/jbrandt-dev/queue-keeper",
            github={"stars": 96, "languages": ["TypeScript", "JavaScript", "Dockerfile"],
                    "readme_length": 2400, "contributors": 3, "author_commits": 154,
                    "total_commits": 188, "project_type": "open_source"}),
        Project("Path Finder", "Pathfinding visualizer", (
            "Visualizes 4 shortest-path algorithms over an editable grid in TypeScript.",
            "Runs entirely in the browser and holds a 90+ Lighthouse performance score.",
        )),
    ),
    redundant_role_bullets=(
        "Shipped REST endpoints in Spring Boot for the customer portal serving 8,000 accounts.",
        "Shipped the contract and JUnit tests that raised service coverage from 46% to 78%.",
        "Shipped the checkout batching change that cut p95 render from 1.8s to 600ms.",
    ),
    redundant_project_bullets=(
        "Real-time office-hours queue in React and Node.js, used across 6 courses by 400 students.",
    ),
)

_SWE_GENERALIST = Slot(
    family="software_engineering", variant="generalist", level="intern",
    name="Devon Whitaker", handle="dwhitaker",
    summary=(
        "Computer science undergraduate who has shipped in Go, TypeScript and "
        "Python. Teaching a lab section taught me more about clear code than "
        "any course did."),
    education="B.S. Computer Science, Halden State University (Expected May 2027), GPA: 3.4",
    roles=(
        Role("Northwind Systems", "Software Engineering Intern", "Jun 2026 – Aug 2026", (
            "Built a Go service that replaced 3 cron jobs, processing 200k records an hour.",
            "Added GraphQL resolvers for 9 entity types behind an existing REST gateway.",
            "Wrote Cypress end-to-end tests covering the 5 highest-traffic user flows.",
        )),
        Role("Halden State University Computer Science Department", "Teaching Assistant", "Sep 2025 – Present", (
            "Graded and gave written feedback on 120 data-structures assignments a term.",
            "Built a Python autograder that cut assignment turnaround from 4 days to 1.",
            "Ran weekly lab sections for 30 students on Git, Linux, and testing practice.",
        )),
    ),
    projects=(
        Project("Split Sum", "Expense splitting app", (
            "Next.js and Postgres app that settles group expenses in the fewest transfers.",
            "Deployed with Docker and Terraform on AWS, holding 99% uptime over 6 months.",
        ), url="github.com/dwhitaker/split-sum"),
        Project("Chess Ledger", "Game analysis tool", (
            "Parses 50k PGN games into MongoDB and reports opening win rates by rating band.",
            "Serves the results through a Redis-cached REST API.",
        )),
    ),
)

_SWE_METRIC_POOR = Slot(
    family="software_engineering", variant="metric_poor", level="entry",
    name="Ana Sokolova", handle="asokolova",
    summary=(
        "Software engineer on an account management platform. I care most "
        "about code that the next person can change without fear."),
    education="B.S. Software Engineering, Kestrel Institute of Technology (June 2026)",
    roles=(
        Role("Beacon Point Systems", "Junior Software Engineer", "Aug 2026 – Present", (
            "Implemented REST endpoints in Java and Spring Boot for the account management service.",
            "Fixed defects in the billing module and added JUnit coverage around the affected paths.",
            "Reviewed pull requests from the rest of the team and kept the CI pipeline green.",
        )),
        Role("Kestrel Institute of Technology IT Services", "Software Engineering Intern", "Jan 2026 – Jul 2026", (
            "Built an internal React dashboard that replaced a spreadsheet workflow for the help desk.",
            "Wrote Python scripts that reconciled asset inventory records between two systems.",
            "Documented the deployment steps so the next intern could run a release unaided.",
        )),
    ),
    projects=(
        Project("Form Forge", "Form builder library", (
            "TypeScript library for building validated forms with no runtime dependency.",
            "Ships with a test suite covering validation, serialization, and error rendering.",
        )),
        Project("Trail Notes", "Hiking log app", (
            "React and PostgreSQL app for logging hikes, with offline-first sync.",
            "Runs in Docker with a seeded development database.",
        ), url="github.com/asokolova/trail-notes"),
    ),
)

# ── AI engineering ────────────────────────────────────────────────────────────

_AI_SPECIALIST = Slot(
    family="ai_engineering", variant="specialist", level="intern",
    name="Ravi Deshmukh", handle="rdeshmukh",
    summary=(
        "Computer science undergraduate building retrieval-augmented systems. "
        "I spend most of my time on evaluation, because that is where these "
        "systems are usually wrong."),
    education="B.S. Computer Science, Vellore Ridge University (Expected May 2027), GPA: 3.8",
    roles=(
        Role("Lumen Assistants", "AI Engineering Intern", "Jun 2026 – Aug 2026", (
            "Built a RAG pipeline over 120k support documents with pgvector, lifting answer accuracy from 61% to 79%.",
            "Cut token spend 40% by adding a prompt cache and trimming 3 redundant context blocks.",
            "Wrote an evaluation set of 200 graded questions that gated every prompt change.",
        )),
        Role("Vellore Ridge University AI Studio", "Undergraduate Researcher", "Sep 2025 – Present", (
            "Built LangChain agents that call 6 campus APIs and return grounded answers with citations.",
            "Ran a 3-model comparison on a 400-question benchmark and published the scoring rubric.",
            "Shipped a Streamlit interface used by 90 students during a pilot term.",
        )),
    ),
    projects=(
        Project("Cite Guard", "Citation verification for model output", (
            "Flags unsupported claims by checking each sentence against retrieved sources, at 84% agreement with hand labels.",
            "Serves verification from FastAPI within a 200ms budget per response.",
        ), url="github.com/rdeshmukh/cite-guard",
            github={"stars": 205, "languages": ["Python", "TypeScript"], "readme_length": 2800,
                    "contributors": 3, "author_commits": 143, "total_commits": 176,
                    "project_type": "open_source"}),
        Project("Prompt Ledger", "Prompt versioning tool", (
            "Tracks 500 prompt revisions with diffs and per-version evaluation scores.",
            "Stores runs in SQLite and exports a side-by-side comparison report.",
        )),
    ),
    achievements=("**1st Place, Vellore Ridge Hack Week** (2026) — retrieval track, 48 teams",),
    redundant_role_bullets=(
        "Built a retrieval pipeline over 120k support documents with pgvector that lifted answer accuracy to 79%.",
        "Built the 200-question graded evaluation set that gated every prompt change.",
        "Built the prompt cache and context trimming that cut token spend 40%.",
    ),
    redundant_project_bullets=(
        "Checks each generated sentence against its retrieved sources and flags unsupported claims.",
    ),
)

_AI_GENERALIST = Slot(
    family="ai_engineering", variant="generalist", level="entry",
    name="Claire Bouchard", handle="cbouchard",
    summary=(
        "AI engineer shipping retrieval and evaluation systems end to end — "
        "the model call is the small part; the plumbing and the grading are "
        "the work."),
    education="B.S. Cognitive Science (Computer Science minor), Saint Clare University (May 2026)",
    roles=(
        Role("Ardent Chat", "Associate AI Engineer", "Jun 2026 – Present", (
            "Shipped a retrieval service over 2M documents with pgvector and Redis caching at 120ms p95.",
            "Built an evaluation harness scoring 40 scenarios per release, catching 6 regressions before launch.",
            "Reduced hallucination reports 30% by adding grounded citation rendering to the response path.",
        )),
        Role("Saint Clare University Digital Learning", "AI Engineering Intern", "Jun 2025 – May 2026", (
            "Built a LangChain tutoring agent used by 250 students across 4 courses.",
            "Fine-tuned a Hugging Face classifier routing 12 question types to the right tool.",
            "Deployed the stack on AWS with Docker behind a Next.js frontend.",
        )),
    ),
    projects=(
        Project("Lecture Lens", "Lecture Q&A over transcripts", (
            "Answers questions over 800 hours of transcripts with timestamped citations.",
            "Runs on FastAPI and Postgres with a React player integration.",
        ), url="github.com/cbouchard/lecture-lens"),
        Project("Rubric Runner", "Model grading harness", (
            "Scores model outputs against 5 rubrics and reports where the rubrics disagree.",
            "Caches 20k judgments in Redis to keep a full re-run under a minute.",
        )),
    ),
)

_AI_METRIC_POOR = Slot(
    family="ai_engineering", variant="metric_poor", level="intern",
    name="Noah Adeyemi", handle="nadeyemi",
    summary=(
        "Computer science undergraduate building assistants that stay inside "
        "what their sources actually say. Refusal behavior interests me more "
        "than fluency."),
    education="B.S. Computer Science, Alder Bay University (Expected June 2028)",
    roles=(
        Role("Quillstone AI", "AI Engineering Intern", "Jun 2026 – Sep 2026", (
            "Built a retrieval pipeline over the company handbook and wired it into an internal chat tool.",
            "Compared several chunking strategies and recorded which produced grounded answers.",
            "Wrote prompt templates with explicit refusal instructions for unsupported questions.",
        )),
        Role("Alder Bay University Writing Center", "Student Technologist", "Jan 2026 – Present", (
            "Built a LangChain assistant that suggests revisions without rewriting a student's draft.",
            "Collected tutor feedback on assistant responses and folded it into the prompt guidelines.",
            "Maintained the FastAPI service and the deployment notes the center's coordinators follow.",
        )),
    ),
    projects=(
        Project("Source Trace", "Answer attribution demo", (
            "Shows which retrieved passage supports each sentence of a generated answer.",
            "Runs locally with SQLite and a small embedding model, with no API key required.",
        )),
        Project("Quiet Hours", "Study assistant", (
            "Summarizes reading assignments and generates practice questions from the source text.",
            "Includes an offline mode so the app still works when the model provider is unreachable.",
        ), url="github.com/nadeyemi/quiet-hours"),
    ),
)


SLOTS: Tuple[Slot, ...] = (
    _DS_SPECIALIST, _DS_GENERALIST, _DS_METRIC_POOR,
    _DE_SPECIALIST, _DE_GENERALIST, _DE_METRIC_POOR,
    _MLE_SPECIALIST, _MLE_GENERALIST, _MLE_METRIC_POOR,
    _SWE_SPECIALIST, _SWE_GENERALIST, _SWE_METRIC_POOR,
    _AI_SPECIALIST, _AI_GENERALIST, _AI_METRIC_POOR,
)
