"""The implicitness filter, calibrated on ART's own encoder (issue #172, chunk 6).

Ability **B — grounded inference** asks whether tailoring can use evidence that
never names the requirement. `eval/README.md`'s coverage map records it as
`none`, and the reason is in `agents/ats_scorer.py`: `keyword_coverage` is
substring matching, so covering a requirement means its term appearing literally.
A dataset where the required capability is *never literally present* forces real
inference and makes that gap measurable — but only once "never literally present"
is a measured property rather than an assertion.

## The issue specifies one threshold. Measurement says it cannot exist.

ImplexConv / TaciTree (arXiv:2503.07018) keeps implicit evidence implicit by
discarding any instance whose similarity to the original trait exceeds
**β = 0.4** — an upper bound, so pairs are kept below it. #172 says not to adopt
that number but to measure our own, and to report a failure to separate as a
finding rather than paper over it. This is that finding.

Measured on `all-MiniLM-L6-v2` (`config.py`) over the 31 labelled pairs below:

| population | n  | min    | mean   | max    |
|------------|----|--------|--------|--------|
| explicit   | 10 | 0.1144 | 0.3261 | 0.5236 |
| implicit   | 11 | 0.0407 | 0.1495 | 0.2501 |
| unrelated  | 10 | 0.0328 | 0.1218 | 0.3953 |

**The populations do not separate**: explicit's minimum (0.1144) sits below
implicit's maximum (0.2501), so no cosine value classifies the set correctly.
The first labelled set was itself defective — 7 of 12 `explicit` pairs shared
**zero** extracted keywords — so the explicit pairs were re-mined by *measured*
overlap rather than intuition and the result held. `label_violations` now re-derives
every lexical label on every run so that mistake cannot recur silently.

**But cosine is not uninformative, and the distinction matters.** It *ranks*
explicit above implicit well — **AUC 0.918** — and merely fails to *separate*
them. A β filter discards everything above a threshold, so it operates on the
tails, and the tails are exactly where these two distributions interleave. Good
ranking and usable thresholding are different properties, and ImplexConv needs
the second one.

Why the tails interleave: cosine measures *topical relatedness*, explicitness is
*term containment*, and pairs land in both off-diagonal corners. "2 years of
experience as a Full Stack Developer" and "Added Terraform and Docker Compose so
a full stack starts from 1 command" share the literal term and score **0.205**,
while "At least one back-end technology (.NET, Node.js, Java Spring Boot, or
Python)" and "Exported to ONNX and benchmarked 3 runtimes on a Raspberry Pi"
share nothing, evidence nothing, and score **0.395** — the highest `unrelated`
score in the set — because both are written in the register of a technology list.

That is deeper than "0.4 was computed on a different model". ImplexConv compares a
generated trait against *its own source trait* — paraphrase versus original, where
cosine is exactly the right instrument. ART compares a JD requirement against a
résumé bullet: different genres of text about a shared topic. **The construct does
not transfer, not merely the constant.**

## The conjunction was tried, and the second conjunct did not earn its place

The design this pointed at was a conjunction: a pair is `implicit` when it is
(1) **lexically invisible** — zero shared keywords under
`ATSScoringEngine._extract_keywords`, the exact extractor `keyword_coverage`
scores on, so "implicit" means precisely *the metric cannot score this pair* —
and (2) **topically plausible**, cosine above a floor, so the bullet is genuine
unnamed evidence rather than unrelated text that also happens to share no terms.

Rule 2 needs something to separate *against*, which is why this module carries a
third population: `unrelated`, pairs with zero keyword overlap where the bullet
does **not** demonstrate the requirement. So the floor was measured on that axis
too, and it is weak:

* **AUC 0.709** for implicit over unrelated;
* best achievable accuracy **76.2%** at a floor of 0.1426, against a
  **52.4%** majority-class baseline.

A gate whose false positives silently corrupt dataset labels needs to be much
better than 76%, and the one outlier above shows how it fails — an `unrelated`
pair scoring 0.395, above every `implicit` pair in the set. So **rule 2 is not
shipped as a gate**. `is_implicit` is rule 1 alone: deterministic, model-free,
and definitionally the property ability B needs. `EVIDENCE_FLOOR` stays `None`
with the measurement recorded beside it, and cosine is carried per pair as a
reported diagnostic rather than a decision.

The honest summary is that this encoder tells you roughly how *topically related*
two texts are, and neither of the two questions this dataset needs answered —
"does the metric already score this?" and "is this genuine evidence?" — is a
question about topical relatedness.

## Every pair is real, and every label is verified

Requirement text is copied from a posting in `eval/jd_dataset/` and bullet text
from a profile in `eval/profiles/`, both named on the pair, so a reviewer checks
both ends against the committed corpus rather than trusting this file. And the
lexical half of every label is **re-derived, never trusted**: `label_violations`
fails a pair whose declared label disagrees with the measured overlap. That is
`eval/profile_checks.py`'s discipline applied here — a declaration is free, and a
fixture that reports under a label it does not hold is worse than one that
declares nothing.

## Why the scores are committed

Loading a sentence-transformer on every test invocation is too slow for the
default suite, and `eval/profile_checks.py` already refused that dependency for
the same reason. So the measurement is committed in `MEASURED` and the contract
splits: the **fast** suite asserts the committed scores satisfy the floor and
that every lexical label re-derives, with no model; an **integration** test
re-measures against the live encoder and asserts the committed scores still hold.

    python eval/implicitness.py              # distributions, floor, violations
    python eval/implicitness.py --remeasure  # score against the live encoder
    python eval/implicitness.py --emit       # MEASURED literal to paste back
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXPLICIT = "explicit"
IMPLICIT = "implicit"
UNRELATED = "unrelated"
LABELS = (EXPLICIT, IMPLICIT, UNRELATED)

#: Labels whose defining property is zero shared keywords. `explicit` is the
#: complement, so this is the whole lexical contract in one place.
LEXICALLY_INVISIBLE = (IMPLICIT, UNRELATED)


@dataclass(frozen=True)
class Pair:
    """One hand-labelled (requirement, bullet) pair.

    *requirement* is copied from `eval/jd_dataset/<jd>.json`'s description and
    *bullet* from `eval/profiles/<profile>.md`.
    """
    key: str
    label: str
    requirement: str
    bullet: str
    jd: str
    profile: str
    note: str = ""


# ── lexical half: deterministic, no model ─────────────────────────────────────

def shared_keywords(requirement: str, bullet: str) -> Set[str]:
    """Keywords the two texts share, under the extractor `keyword_coverage` uses.

    Deliberately `ATSScoringEngine._extract_keywords` and not a private tokenizer:
    the claim "this pair is invisible to the metric" is only true if it is
    measured with the metric's own vocabulary.
    """
    from agents.ats_scorer import ATSScoringEngine

    return (ATSScoringEngine._extract_keywords(requirement)
            & ATSScoringEngine._extract_keywords(bullet))


def is_lexically_invisible(requirement: str, bullet: str) -> bool:
    """Rule 1: `keyword_coverage` can score nothing from this pair."""
    return not shared_keywords(requirement, bullet)


def label_violations(pairs: Sequence["Pair"] = ()) -> List[Tuple[str, str, List[str]]]:
    """Pairs whose declared label disagrees with the measured keyword overlap.

    `(key, problem, shared)` per violation. This is the verify-don't-trust check:
    the lexical half of a label is a property of the text, so it is re-derived on
    every run rather than believed.
    """
    out: List[Tuple[str, str, List[str]]] = []
    for pair in sorted(pairs or PAIRS, key=lambda p: p.key):
        shared = sorted(shared_keywords(pair.requirement, pair.bullet))
        invisible = not shared
        if pair.label in LEXICALLY_INVISIBLE and not invisible:
            out.append((pair.key, f"labelled {pair.label} but shares keywords", shared))
        elif pair.label == EXPLICIT and invisible:
            out.append((pair.key, "labelled explicit but shares no keyword", []))
    return out


# ── the labelled set ──────────────────────────────────────────────────────────
#
# explicit  : shares at least one extracted keyword — keyword_coverage scores it
# implicit  : shares none, and the bullet DOES demonstrate the requirement
# unrelated : shares none, and the bullet does NOT
#
# The explicit pairs were mined by measured overlap rather than chosen by
# intuition, after the first hand-picked set failed `label_violations`.

PAIRS: Tuple[Pair, ...] = (
    # ── explicit ──────────────────────────────────────────────────────────────
    Pair(
        key="explicit_docker_kubernetes",
        label=EXPLICIT,
        requirement="Docker containerization and Kubernetes basics",
        bullet="Runs on Kubernetes from a Docker image pinned to a reproducible CUDA build.",
        jd="atoms_careers_page_cloud_platform_data_engineer",
        profile="ml_engineering_generalist_omar_haddad",
        note="shares docker, kubernetes",
    ),
    Pair(
        key="explicit_python_systems",
        label=EXPLICIT,
        requirement="Experience with C++, Python, or similar systems languages",
        bullet="Wrote Python scripts that reconciled asset inventory records between two systems.",
        jd="astera_institute_software_engineering_intern_distributed_simulati",
        profile="software_engineering_metric_poor_ana_sokolova",
        note="shares python, systems",
    ),
    Pair(
        key="explicit_dbt_warehouse",
        label=EXPLICIT,
        requirement="Experience with dbt for data transformations and warehouse modeling",
        bullet="Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting.",
        jd="atoms_careers_page_cloud_platform_data_engineer",
        profile="data_engineering_specialist_wei_lin_tan",
        note="shares dbt, warehouse",
    ),
    Pair(
        key="explicit_warehouse_schemas",
        label=EXPLICIT,
        requirement="Experience designing data models and warehouse schemas from scratch.",
        bullet="Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting.",
        jd="arizent_data_ai_engineer",
        profile="data_engineering_specialist_wei_lin_tan",
        note="shares models, warehouse",
    ),
    Pair(
        key="explicit_spring_boot",
        label=EXPLICIT,
        requirement="At least one back-end technology (.NET, Node.js, Java Spring Boot, or Python)",
        bullet="Implemented REST endpoints in Java and Spring Boot for the account management service.",
        jd="assyst_inc_junior_full_stack_developer",
        profile="software_engineering_metric_poor_ana_sokolova",
        note="shares boot, java, spring",
    ),
    Pair(
        key="explicit_full_stack",
        label=EXPLICIT,
        requirement="2 years of experience as a Full Stack Developer or in a similar role.",
        bullet="Added Terraform and Docker Compose so a full stack starts from 1 command.",
        jd="accenture_federal_services_full_stack_developer",
        profile="data_engineering_generalist_adaeze_nwosu",
        note="shares full, stack — and scores LOW (0.114), which is half the finding",
    ),
    Pair(
        key="explicit_dashboards_reporting",
        label=EXPLICIT,
        requirement="Build alerts and monitoring dashboards that surface tracking issues before they affect reporting.",
        bullet="Built a Postgres reporting schema serving 3 dashboards for the circulation team.",
        jd="arizent_data_ai_engineer",
        profile="data_engineering_generalist_adaeze_nwosu",
        note="shares dashboards, reporting",
    ),
    Pair(
        key="explicit_data_warehouse",
        label=EXPLICIT,
        requirement="Support the Senior Manager of Data & AI in maintaining our BigQuery data warehouse following established workflows and governance practices.",
        bullet="Cut warehouse spend 28% by repartitioning the 12 largest Parquet tables and pruning unused columns.",
        jd="arizent_data_ai_engineer",
        profile="data_engineering_specialist_wei_lin_tan",
        note="shares warehouse",
    ),
    Pair(
        key="explicit_visualization_tableau",
        label=EXPLICIT,
        requirement="Experience with data visualization tools (e.g.: Tableau, Power BI, D3, etc.), machine learning, dataset analysis or developing analytics",
        bullet="Published an interactive Tableau workbook opened 900 times in its first month.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="data_science_generalist_danielle_okafor",
        note="shares tableau",
    ),
    Pair(
        key="explicit_data_quality_reporting",
        label=EXPLICIT,
        requirement="Data quality: Reporting across the business is consistently accurate and trusted, with measurable reductions in tracking errors and data discrepancies.",
        bullet="Added dbt tests to 60 models, catching 14 schema regressions before they reached reporting.",
        jd="arizent_data_ai_engineer",
        profile="data_engineering_specialist_wei_lin_tan",
        note="shares reporting",
    ),

    # ── implicit: zero overlap, and the bullet does demonstrate it ────────────
    Pair(
        key="implicit_etl_via_airflow",
        label=IMPLICIT,
        requirement="Design, build, and manage ETL pipelines integrating Omeda, Salesforce, Google Analytics, and third-party services (Reach Marketing, Discovery, Bombora, and others).",
        bullet="Built Airflow DAGs moving 40M shipment events a day from Kafka into Snowflake at 99.9% delivery.",
        jd="arizent_data_ai_engineer",
        profile="data_engineering_specialist_wei_lin_tan",
        note="the cleanest case in this corpus: 'ETL' appears in zero bullets across the whole profile set, and this is unambiguously ETL",
    ),
    Pair(
        key="implicit_etl_via_ingest",
        label=IMPLICIT,
        requirement="Design, build, and manage ETL pipelines integrating Omeda, Salesforce, Google Analytics, and third-party services (Reach Marketing, Discovery, Bombora, and others).",
        bullet="Loads 500k-row vendor drops into Postgres with content hashing, so a replayed file is a no-op.",
        jd="arizent_data_ai_engineer",
        profile="data_engineering_specialist_wei_lin_tan",
        note="extract-transform-load described end to end without the acronym",
    ),
    Pair(
        key="implicit_iac_terraform",
        label=IMPLICIT,
        requirement="Experience with cloud-based data analytics platforms",
        bullet="Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="data_engineering_specialist_wei_lin_tan",
        note="cloud platform work evidenced by naming AWS primitives, never the category",
    ),
    Pair(
        key="implicit_deployment_via_onnx",
        label=IMPLICIT,
        requirement="Familiarity with production-level deployment of machine learning models",
        bullet="Exported to ONNX and benchmarked 3 runtimes on a Raspberry Pi.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="ml_engineering_specialist_sofia_marchetti",
        note="deployment demonstrated as runtime export and benchmarking",
    ),
    Pair(
        key="implicit_monitoring_via_digest",
        label=IMPLICIT,
        requirement="Build alerts and monitoring dashboards that surface tracking issues before they affect reporting.",
        bullet="Posts a daily digest that replaced a manual review of 3 upstream feeds.",
        jd="arizent_data_ai_engineer",
        profile="data_engineering_specialist_wei_lin_tan",
        note="monitoring by outcome; neither 'alert' nor 'monitoring' appears",
    ),
    Pair(
        key="implicit_scale_via_latency",
        label=IMPLICIT,
        requirement="Experience working with large-scale or complex datasets",
        bullet="Loaded 1.4TB of sensor archives into Postgres and cut a common query from 90 seconds to 4.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="data_engineering_specialist_wei_lin_tan",
        note="scale evidenced in bytes and latency, not by the phrase",
    ),
    Pair(
        key="implicit_feature_engineering",
        label=IMPLICIT,
        requirement="Experience with automated feature engineering or model selection tools",
        bullet="Tracked 60 experiment runs in MLflow and wrote the comparison that selected the shipped checkpoint.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="ml_engineering_specialist_sofia_marchetti",
        note="model selection performed and described without the phrase",
    ),
    Pair(
        key="implicit_privacy_via_iam",
        label=IMPLICIT,
        requirement="Knowledge of data privacy and security best practices in analytics",
        bullet="Wrote a Terraform module that provisioned the group's S3 buckets and IAM roles in 1 command.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="data_engineering_specialist_wei_lin_tan",
        note="IAM roles are access control; neither 'privacy' nor 'security' appears",
    ),
    Pair(
        key="implicit_agile_via_tests",
        label=IMPLICIT,
        requirement="Familiarity with agile development methodologies in data science projects",
        bullet="Raised service test coverage from 46% to 78% with JUnit and contract tests.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="software_engineering_specialist_julia_brandt",
        note="engineering practice evidenced without the methodology's name",
    ),
    Pair(
        key="implicit_mining_via_dedupe",
        label=IMPLICIT,
        requirement="Knowledge of data mining techniques specific to network logs or structured metadata",
        bullet="Built a Spark job that deduplicated 90M ad impression rows nightly, trimming runtime from 50 minutes to 18.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="data_engineering_generalist_adaeze_nwosu",
        note="log-scale structured processing; 'data mining' never appears",
    ),
    Pair(
        key="implicit_quality_via_ci",
        label=IMPLICIT,
        requirement="Data quality: Reporting across the business is consistently accurate and trusted, with measurable reductions in tracking errors and data discrepancies.",
        bullet="Added GitHub Actions CI that ran 220 tests on every pull request.",
        jd="arizent_data_ai_engineer",
        profile="software_engineering_specialist_julia_brandt",
        note="quality assurance by mechanism rather than by name",
    ),

    # ── unrelated: zero overlap, and the bullet does NOT demonstrate it ───────
    #
    # The negative class the evidence floor is measured against. Without it there
    # is nothing for a floor to separate and any value would be arbitrary.
    Pair(
        key="unrelated_etl_vs_frontend",
        label=UNRELATED,
        requirement="Design, build, and manage ETL pipelines integrating Omeda, Salesforce, Google Analytics, and third-party services (Reach Marketing, Discovery, Bombora, and others).",
        bullet="Visualizes 4 shortest-path algorithms over an editable grid in TypeScript.",
        jd="arizent_data_ai_engineer",
        profile="software_engineering_specialist_julia_brandt",
        note="an algorithm visualiser is not pipeline work by any reading",
    ),
    Pair(
        key="unrelated_privacy_vs_distillation",
        label=UNRELATED,
        requirement="Knowledge of data privacy and security best practices in analytics",
        bullet="Distilled a detector to 8MB for edge devices, retaining 88% of teacher mAP.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="ml_engineering_specialist_sofia_marchetti",
        note="model compression has nothing to do with privacy practice",
    ),
    Pair(
        key="unrelated_visualization_vs_latency",
        label=UNRELATED,
        requirement="Experience with data visualization tools (e.g.: Tableau, Power BI, D3, etc.), machine learning, dataset analysis or developing analytics",
        bullet="Ships a Docker Compose stack that stands the whole pipeline up in under 2 minutes.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="data_engineering_specialist_wei_lin_tan",
        note="developer tooling, not visualization or analysis",
    ),
    Pair(
        key="unrelated_kubernetes_vs_survey",
        label=UNRELATED,
        requirement="Docker containerization and Kubernetes basics",
        bullet="Published an interactive Tableau workbook opened 900 times in its first month.",
        jd="atoms_careers_page_cloud_platform_data_engineer",
        profile="data_science_generalist_danielle_okafor",
        note="a BI workbook evidences no container work",
    ),
    Pair(
        key="unrelated_agile_vs_partitioning",
        label=UNRELATED,
        requirement="Familiarity with agile development methodologies in data science projects",
        bullet="Cut warehouse spend 28% by repartitioning the 12 largest Parquet tables and pruning unused columns.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="data_engineering_specialist_wei_lin_tan",
        note="storage optimisation says nothing about process methodology",
    ),
    Pair(
        key="unrelated_graph_vs_digest",
        label=UNRELATED,
        requirement="Experience with graph-based algorithms and analytics",
        bullet="Posts a daily digest that replaced a manual review of 3 upstream feeds.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="data_engineering_specialist_wei_lin_tan",
        note="a reporting digest evidences no graph work",
    ),
    Pair(
        key="unrelated_deployment_vs_dedupe",
        label=UNRELATED,
        requirement="Familiarity with production-level deployment of machine learning models",
        bullet="Loads 500k-row vendor drops into Postgres with content hashing, so a replayed file is a no-op.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="data_engineering_specialist_wei_lin_tan",
        note="batch ingest is not model deployment",
    ),
    Pair(
        key="unrelated_spring_vs_onnx",
        label=UNRELATED,
        requirement="At least one back-end technology (.NET, Node.js, Java Spring Boot, or Python)",
        bullet="Exported to ONNX and benchmarked 3 runtimes on a Raspberry Pi.",
        jd="assyst_inc_junior_full_stack_developer",
        profile="ml_engineering_specialist_sofia_marchetti",
        note="model export is not a backend web technology",
    ),
    Pair(
        key="unrelated_dbt_vs_tests",
        label=UNRELATED,
        requirement="Experience with dbt for data transformations and warehouse modeling",
        bullet="Raised service test coverage from 46% to 78% with JUnit and contract tests.",
        jd="atoms_careers_page_cloud_platform_data_engineer",
        profile="software_engineering_specialist_julia_brandt",
        note="JVM unit testing evidences no warehouse modelling",
    ),
    Pair(
        key="unrelated_scale_vs_digest",
        label=UNRELATED,
        requirement="Experience working with large-scale or complex datasets",
        bullet="Posts a daily digest that replaced a manual review of 3 upstream feeds.",
        jd="accenture_federal_services_jr_data_scientist",
        profile="data_engineering_specialist_wei_lin_tan",
        note="a digest states no scale at all",
    ),
)


# ── the calibration ───────────────────────────────────────────────────────────

#: **Deliberately unset.** No single cosine threshold separates `explicit` from
#: `implicit` on this encoder — see the module docstring. Kept as a named `None`
#: rather than deleted so a future reader finds the negative result where they
#: would look for the constant.
BETA: Optional[float] = None

#: **Also deliberately unset.** Rule 2 of the conjunction — a minimum cosine for a
#: lexically-invisible pair to count as evidence — was measured on the
#: `implicit` vs `unrelated` axis and came back too weak to gate on: AUC 0.709,
#: best accuracy 76.2% at 0.1426 against a 52.4% baseline. A gate whose false
#: positives silently corrupt labels has to beat that comfortably.
EVIDENCE_FLOOR: Optional[float] = None

#: The measurement that retired rule 2, kept so the decision is auditable rather
#: than folded into prose. Regenerate with `--emit`.
EVIDENCE_AXIS_AUC = 0.709
EVIDENCE_AXIS_BEST_ACCURACY = 0.762
EVIDENCE_AXIS_BEST_FLOOR = 0.1426
EVIDENCE_AXIS_BASELINE = 0.524

#: Ranking quality on the axis #172 specified. Recorded because "no usable
#: threshold" and "no signal" are different claims and only the first is true.
BETA_AXIS_AUC = 0.918

#: Encoder the commitment describes. A model change invalidates it.
MEASURED_WITH = "all-MiniLM-L6-v2"

#: Committed cosine per pair key, so the fast suite asserts the contract with no
#: model load. Re-measure with `--emit` after editing any pair.
MEASURED: Dict[str, float] = {
    "explicit_dashboards_reporting": 0.4137,
    "explicit_data_quality_reporting": 0.2610,
    "explicit_data_warehouse": 0.2503,
    "explicit_dbt_warehouse": 0.3885,
    "explicit_docker_kubernetes": 0.5236,
    "explicit_full_stack": 0.2049,
    "explicit_python_systems": 0.1144,
    "explicit_spring_boot": 0.3915,
    "explicit_visualization_tableau": 0.3316,
    "explicit_warehouse_schemas": 0.3819,
    "implicit_agile_via_tests": 0.0407,
    "implicit_deployment_via_onnx": 0.2034,
    "implicit_etl_via_airflow": 0.1554,
    "implicit_etl_via_ingest": 0.0870,
    "implicit_feature_engineering": 0.1426,
    "implicit_iac_terraform": 0.2501,
    "implicit_mining_via_dedupe": 0.1497,
    "implicit_monitoring_via_digest": 0.1650,
    "implicit_privacy_via_iam": 0.1041,
    "implicit_quality_via_ci": 0.1057,
    "implicit_scale_via_latency": 0.2406,
    "unrelated_agile_vs_partitioning": 0.1119,
    "unrelated_dbt_vs_tests": 0.0908,
    "unrelated_deployment_vs_dedupe": 0.0620,
    "unrelated_etl_vs_frontend": 0.1195,
    "unrelated_graph_vs_digest": 0.1372,
    "unrelated_kubernetes_vs_survey": 0.0389,
    "unrelated_privacy_vs_distillation": 0.1389,
    "unrelated_scale_vs_digest": 0.0328,
    "unrelated_spring_vs_onnx": 0.3953,
    "unrelated_visualization_vs_latency": 0.0907,
}


class CalibrationError(ValueError):
    """The labelled set and the committed calibration disagree."""


def pairs_by_label(label: str, pairs: Sequence[Pair] = PAIRS) -> List[Pair]:
    return [p for p in pairs if p.label == label]


def committed_scores(pairs: Sequence[Pair] = PAIRS) -> Dict[str, float]:
    """Committed cosine per pair, or an error naming the gap.

    A pair added without re-measuring would otherwise be silently skipped by
    every check below — the quiet failure this module exists to prevent.
    """
    missing = sorted(p.key for p in pairs if p.key not in MEASURED)
    if missing:
        raise CalibrationError(
            f"no committed score for: {', '.join(missing)}. "
            "Run: python eval/implicitness.py --emit")
    return {p.key: MEASURED[p.key] for p in pairs}


def score_pairs(encoder: Callable[[List[str]], object],
                pairs: Sequence[Pair] = PAIRS) -> Dict[str, float]:
    """Cosine per pair under *encoder*, which takes a list of texts and returns
    row vectors — the shape `agents/matcher.get_embedding_model()` produces and
    the shape the redundancy suite already injects (#122).

    Encoded in one sorted batch, so batch composition is a function of the pair
    set alone (#158).
    """
    import numpy as np

    ordered = sorted(pairs, key=lambda p: p.key)
    texts = [p.requirement for p in ordered] + [p.bullet for p in ordered]
    vectors = np.asarray(encoder(texts), dtype=float)
    n = len(ordered)
    out: Dict[str, float] = {}
    for i, pair in enumerate(ordered):
        req, bullet = vectors[i], vectors[n + i]
        denom = float(np.linalg.norm(req) * np.linalg.norm(bullet))
        out[pair.key] = round(float(np.dot(req, bullet) / denom), 4) if denom else 0.0
    return out


def distributions(scores: Dict[str, float],
                  pairs: Sequence[Pair] = PAIRS) -> Dict[str, Dict[str, float]]:
    """n / min / mean / max per label — reported rather than summarised into a
    verdict, which is what #172 requires of this calibration."""
    out: Dict[str, Dict[str, float]] = {}
    for label in LABELS:
        values = sorted(scores[p.key] for p in pairs
                        if p.label == label and p.key in scores)
        if not values:
            continue
        out[label] = {"n": len(values), "min": round(values[0], 4),
                      "mean": round(mean(values), 4), "max": round(values[-1], 4)}
    return out


def separation(scores: Dict[str, float], low_label: str, high_label: str,
               pairs: Sequence[Pair] = PAIRS) -> Tuple[bool, float, float]:
    """`(separated, low_max, high_min)` for two populations.

    Separated means every *low_label* pair scores below every *high_label* one,
    so a threshold exists that classifies both correctly.
    """
    dist = distributions(scores, pairs)
    if low_label not in dist or high_label not in dist:
        return False, 0.0, 0.0
    low_max = dist[low_label]["max"]
    high_min = dist[high_label]["min"]
    return high_min > low_max, low_max, high_min


def suggest_floor(scores: Dict[str, float],
                  pairs: Sequence[Pair] = PAIRS) -> Optional[float]:
    """Midpoint between `unrelated` and `implicit`, or None when they overlap.

    The midpoint rather than either edge: sitting the floor on the highest
    unrelated score fails the next unrelated pair a hair above it, and sitting it
    on the lowest implicit score does the same in the other direction.
    """
    separated, low, high = separation(scores, UNRELATED, IMPLICIT, pairs)
    if not separated:
        return None
    return round((low + high) / 2, 4)


def auc(positive: Sequence[float], negative: Sequence[float]) -> Optional[float]:
    """P(a random *positive* outscores a random *negative*), ties counted as half.

    Reported because "the populations do not separate" and "the encoder carries
    no signal" are different claims, and conflating them would have retired a
    usable ranking signal on the strength of an unusable threshold.
    """
    if not positive or not negative:
        return None
    wins = sum(1.0 if a > b else 0.5 if a == b else 0.0
               for a in positive for b in negative)
    return round(wins / (len(positive) * len(negative)), 3)


def best_threshold(positive: Sequence[float],
                   negative: Sequence[float]) -> Tuple[Optional[float], float, float]:
    """`(threshold, accuracy, majority_baseline)` for the best split available.

    The number that retired the evidence floor: a gate is only worth having if
    its best achievable accuracy clears the baseline by a margin that justifies
    the false positives it will produce.
    """
    if not positive or not negative:
        return None, 0.0, 0.0
    total = len(positive) + len(negative)
    baseline = max(len(positive), len(negative)) / total
    best = (None, 0.0)
    for candidate in sorted(set(list(positive) + list(negative))):
        correct = (sum(1 for v in positive if v >= candidate)
                   + sum(1 for v in negative if v < candidate))
        if correct / total > best[1]:
            best = (candidate, correct / total)
    return best[0], round(best[1], 3), round(baseline, 3)


def is_implicit(requirement: str, bullet: str) -> bool:
    """The shipped filter: **lexically invisible, and nothing else.**

    The conjunction's second rule — a cosine floor — was measured and dropped;
    see the module docstring and `EVIDENCE_AXIS_AUC`. What remains is
    deterministic, needs no model, and is definitionally the property ability B
    requires: `keyword_coverage` can score nothing from this pair.

    Cosine is still worth *recording* for a pair, which is what `MEASURED` is
    for. It is simply not consulted here.
    """
    return is_lexically_invisible(requirement, bullet)


def floor_violations(scores: Dict[str, float], floor: Optional[float] = None,
                     pairs: Sequence[Pair] = PAIRS) -> List[Tuple[str, str, float]]:
    """Pairs the committed floor misclassifies: `implicit` below it, `unrelated`
    at or above it. The build-failing assertion is that this is empty.

    `explicit` pairs are not checked against the floor — rule 1 already excludes
    them, and the whole finding above is that their cosine says nothing.
    """
    floor = EVIDENCE_FLOOR if floor is None else floor
    if floor is None:
        return []
    out: List[Tuple[str, str, float]] = []
    for pair in sorted(pairs, key=lambda p: p.key):
        score = scores.get(pair.key)
        if score is None:
            continue
        if pair.label == IMPLICIT and score < floor:
            out.append((pair.key, "implicit below floor", score))
        elif pair.label == UNRELATED and score >= floor:
            out.append((pair.key, "unrelated at or above floor", score))
    return out


def live_encoder() -> Callable[[List[str]], object]:
    """The production encoder, or a clear error naming the install command."""
    try:
        from agents.matcher import get_embedding_model

        model = get_embedding_model()
    except Exception as exc:  # not installed / offline / OOM
        raise CalibrationError(
            f"the production encoder is unavailable ({type(exc).__name__}: {exc}). "
            "Install it with: pip install -r requirements-full.txt") from exc
    if model is None:
        raise CalibrationError(
            "get_embedding_model() returned None — this calibration describes a "
            "specific encoder and cannot be measured without it.")
    return lambda texts: model.encode(texts, normalize_embeddings=True)


# ── CLI ───────────────────────────────────────────────────────────────────────

def report(scores: Dict[str, float]) -> str:
    lines: List[str] = [f"encoder: {MEASURED_WITH}    pairs: {len(PAIRS)}", ""]
    dist = distributions(scores)
    for label in LABELS:
        if label in dist:
            d = dist[label]
            lines.append(f"  {label:<10} n={d['n']:<3} min={d['min']:.4f}  "
                         f"mean={d['mean']:.4f}  max={d['max']:.4f}")
    lines.append("")

    ok, low, high = separation(scores, IMPLICIT, EXPLICIT)
    lines.append("  beta (explicit vs implicit) — the axis #172 specified:")
    if ok:
        lines.append(f"    separated: implicit max {low:.4f} < explicit min {high:.4f}")
    else:
        lines.append(f"    NOT SEPARATED: implicit max {low:.4f} >= explicit min {high:.4f}")
        lines.append("    Cosine measures topical relatedness; explicitness is term")
        lines.append("    containment. Different axes — see the module docstring.")

    by_label = {label: [scores[p.key] for p in PAIRS
                        if p.label == label and p.key in scores]
                for label in LABELS}
    lines.append(f"    ranking AUC explicit>implicit: "
                 f"{auc(by_label[EXPLICIT], by_label[IMPLICIT])}  "
                 "(ranks well, thresholds badly — see the docstring)")

    ok, low, high = separation(scores, UNRELATED, IMPLICIT)
    lines.append("")
    lines.append("  evidence floor (unrelated vs implicit) — the fallback axis:")
    if ok:
        lines.append(f"    separated: unrelated max {low:.4f} < implicit min {high:.4f} "
                     f"(gap {high - low:.4f})")
        lines.append(f"    suggested floor (gap midpoint): {suggest_floor(scores):.4f}")
    else:
        lines.append(f"    NOT SEPARATED: unrelated max {low:.4f} >= implicit min {high:.4f}")
    thr, acc, base = best_threshold(by_label[IMPLICIT], by_label[UNRELATED])
    lines.append(f"    ranking AUC implicit>unrelated: "
                 f"{auc(by_label[IMPLICIT], by_label[UNRELATED])}")
    if thr is not None:
        lines.append(f"    best accuracy {acc:.3f} at floor {thr:.4f} "
                     f"(majority baseline {base:.3f}) -> too weak to gate on")
    lines.append(f"    committed floor: "
                 f"{'unset (rule 2 dropped)' if EVIDENCE_FLOOR is None else format(EVIDENCE_FLOOR, '.4f')}")

    bad_labels = label_violations(PAIRS)
    lines.append("")
    if bad_labels:
        lines.append(f"  {len(bad_labels)} LABEL VIOLATION(S) — declared vs measured overlap:")
        for key, problem, shared in bad_labels:
            lines.append(f"    {key:<34} {problem} {shared}")
    else:
        lines.append("  every label re-derives from the measured keyword overlap")

    bad_floor = floor_violations(scores)
    if bad_floor:
        lines.append(f"  {len(bad_floor)} FLOOR VIOLATION(S):")
        for key, problem, score in bad_floor:
            lines.append(f"    {key:<34} {problem} ({score:.4f})")
    elif EVIDENCE_FLOOR is not None:
        lines.append("  no floor violations")

    lines.append("")
    for pair in sorted(PAIRS, key=lambda p: (p.label, scores.get(p.key, 0.0))):
        if pair.key in scores:
            lines.append(f"  {pair.label:<10} {scores[pair.key]:.4f}  {pair.key}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remeasure", action="store_true",
                    help="score against the live encoder instead of the committed values")
    ap.add_argument("--emit", action="store_true",
                    help="print a MEASURED literal to paste back into this file")
    args = ap.parse_args(argv)

    scores = (score_pairs(live_encoder()) if (args.remeasure or args.emit)
              else committed_scores())
    print(report(scores))

    if args.emit:
        print("\nMEASURED: Dict[str, float] = {")
        for key in sorted(scores):
            print(f'    "{key}": {scores[key]:.4f},')
        print("}")

    return 1 if (label_violations(PAIRS) or floor_violations(scores)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
