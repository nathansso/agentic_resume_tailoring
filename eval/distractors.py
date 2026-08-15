"""The distractor pool: difficulty without relabelling (issue #172, chunk 5).

LongMemEval (arXiv:2410.10813)'s most reusable structural idea is that ground
truth stays fixed while the haystack scales: the same 500 questions run at ~50
sessions and at 500, and only irrelevant padding grows. ART's version injects
plausible-but-irrelevant skills and projects into a profile **without moving the
answer key**, so a 15-skill and an 85-skill candidate share one label set and a
harder benchmark costs almost nothing.

## The problem this solves

`skills.selection_ratio` is currently uninterpretable across strata. A specialist
carries 15 skills and a generalist 27, so the same ratio means different things,
and the `breadth` contrast the profile set reports is confounded with profile
size. Distractors decouple haystack size from the true-positive set: hold the
real skills fixed, vary the noise, and selection quality becomes measurable on
its own. A second effect matters for #51 Phase 2 — `MAX_SKILLS = 18` does not
bind on a 15-skill specialist, so the cap's behaviour is unobservable until the
profile is padded past it.

## Admission is an exact condition, not a similarity judgement

"Irrelevant" cannot be eyeballed. A distractor that is genuinely relevant to
*some* posting silently corrupts that posting's labels, and the corruption is
invisible — every reported number still looks well-formed. So admission is
checked against **every posting in `eval/jd_dataset/`**, and it is derived from
how the pipeline actually decides relevance rather than from a threshold someone
liked.

Two facts make this exact rather than approximate:

1. **`SkillMatcherAgent.match` iterates over JD skills**, checking each against
   the candidate's skills. Adding a skill to a profile can therefore only flip a
   JD skill from *missing* to *matched* — never the reverse. So "the answer key
   does not move" reduces to "no JD skill becomes matched", and that decomposes
   onto the matcher's four channels exactly.
2. **`keyword_coverage` is substring matching** over
   `ATSScoringEngine._extract_keywords`. A term sharing no extracted keyword with
   a posting cannot change that posting's keyword score, by construction.

The resulting rules, each tied to the channel it protects:

| # | Rule | Channel it protects |
|---|---|---|
| L | shares no keyword with any posting, under the metric's own extractor | `keyword_coverage`; also the matcher's direct and name-match channels, since a JD skill name is drawn from the posting text |
| S | cosine below `SkillMatcherAgent.SEMANTIC_THRESHOLD` against every corpus keyword | the matcher's semantic channel |
| G | project text names no corpus keyword | the matcher's indirect (knowledge-graph) channel, which traverses project → skill edges built by substring match |

## Why the semantic rule survives chunk 6's finding

Chunk 6 measured that this encoder does not discriminate *relevance* between a JD
requirement and a résumé bullet, and dropped it as a gate there. Rule S is a
different question. It does not ask "is this distractor relevant?" — it asks
"**would `_check_semantic_match` fire?**", and computes precisely that, with the
production model at the production threshold over a superset of the JD skill
vocabulary. The encoder is not being trusted to judge; it is being replayed.

The audit shows the difference plainly. Every semantic block in the committed
result is an orthographic artefact rather than a relevance match — `CATIA` blocked
by *scania*, `QuickBooks` by *playbooks*, `Blender` by *blends*, `Objective-C` by
*objective*. MiniLM on short single tokens is dominated by surface form. That
would be a fatal flaw in a relevance judgement and is merely a cost here, because
the error direction is **conservative**: a false block loses a candidate, while a
false admission would corrupt labels. Rule S is also doubly conservative by
construction, since it scores against every extracted keyword and most of those
would never be extracted as a JD skill.

    python eval/distractors.py            # audit the bank against the corpus
    python eval/distractors.py --emit     # ADMITTED literal to paste back
    python eval/distractors.py --verbose  # show rejected candidates too
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATASET_DIR = ROOT / "eval" / "jd_dataset"


class AdmissionError(ValueError):
    """The bank and the committed admission result disagree."""


@dataclass(frozen=True)
class Verdict:
    """Why one candidate was admitted or rejected."""
    term: str
    admitted: bool
    lexical_conflicts: Tuple[str, ...] = ()
    nearest_keyword: str = ""
    nearest_score: float = 0.0

    @property
    def reason(self) -> str:
        if self.lexical_conflicts:
            return f"shares corpus keyword(s): {', '.join(self.lexical_conflicts)}"
        if not self.admitted:
            return (f"semantic: {self.nearest_score:.4f} >= threshold "
                    f"(nearest corpus keyword {self.nearest_keyword!r})")
        return "admitted"


# ── the corpus side ───────────────────────────────────────────────────────────

def corpus_keywords(dataset_dir: Optional[Path] = None) -> Set[str]:
    """Every keyword the metric extracts from every posting, title included.

    The union across all 150 postings, not per posting: a distractor is admitted
    for the whole corpus or not at all, because the benchmark runs one profile
    against every task and a per-task exception would silently corrupt whichever
    task it was excepted for.
    """
    from agents.ats_scorer import ATSScoringEngine

    directory = Path(dataset_dir or DATASET_DIR)
    keywords: Set[str] = set()
    for path in sorted(directory.glob("*.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        keywords |= ATSScoringEngine._extract_keywords(task.get("description") or "")
        keywords |= ATSScoringEngine._extract_keywords(task.get("title") or "")
    return keywords


def semantic_threshold() -> float:
    """The matcher's own threshold, imported rather than restated.

    If `SkillMatcherAgent.SEMANTIC_THRESHOLD` moves, admission has to move with
    it or the committed pool stops describing the pipeline it was audited
    against.
    """
    from agents.matcher import SkillMatcherAgent

    return float(SkillMatcherAgent.SEMANTIC_THRESHOLD)


# ── rule L: lexical ───────────────────────────────────────────────────────────

def lexical_conflicts(term: str, keywords: Optional[Set[str]] = None) -> List[str]:
    """Corpus keywords this term shares, under the metric's own extractor.

    Non-empty means the term can move `keyword_coverage` on at least one posting,
    which is disqualifying however plausible the term looks as a distractor.
    """
    from agents.ats_scorer import ATSScoringEngine

    keywords = corpus_keywords() if keywords is None else keywords
    return sorted(ATSScoringEngine._extract_keywords(term) & keywords)


def is_lexically_admissible(term: str, keywords: Optional[Set[str]] = None) -> bool:
    return not lexical_conflicts(term, keywords)


# ── rule S: semantic, replaying the matcher ───────────────────────────────────

def nearest_corpus_keyword(
    terms: Sequence[str],
    encoder: Callable[[List[str]], object],
    keywords: Optional[Sequence[str]] = None,
) -> Dict[str, Tuple[str, float]]:
    """`{term: (nearest corpus keyword, cosine)}`.

    Batched in one sorted encode so composition is a function of the inputs alone
    (#158). This is the expensive call in the module — thousands of keywords —
    which is why the result is committed rather than recomputed per test.
    """
    import numpy as np

    keys = sorted(keywords if keywords is not None else corpus_keywords())
    ordered = sorted(terms)
    if not keys or not ordered:
        return {}
    key_vectors = np.asarray(encoder(keys), dtype=float)
    term_vectors = np.asarray(encoder(ordered), dtype=float)
    sims = term_vectors @ key_vectors.T
    out: Dict[str, Tuple[str, float]] = {}
    for i, term in enumerate(ordered):
        j = int(np.argmax(sims[i]))
        out[term] = (keys[j], round(float(sims[i][j]), 4))
    return out


# ── the audit ─────────────────────────────────────────────────────────────────

def audit(candidates: Sequence[str],
          encoder: Optional[Callable[[List[str]], object]] = None,
          keywords: Optional[Set[str]] = None) -> List[Verdict]:
    """Run both rules over *candidates*, cheapest first.

    Rule L runs on everything and needs no model. Rule S runs only on what
    survives it, because encoding is the expensive step and a lexically-rejected
    candidate is already out.
    """
    keywords = corpus_keywords() if keywords is None else keywords
    verdicts: List[Verdict] = []
    survivors: List[str] = []
    for term in sorted(candidates):
        conflicts = lexical_conflicts(term, keywords)
        if conflicts:
            verdicts.append(Verdict(term, False, tuple(conflicts)))
        else:
            survivors.append(term)

    if not survivors:
        return sorted(verdicts, key=lambda v: v.term)
    if encoder is None:
        # Lexical-only audit: report survivors as undecided rather than admitted,
        # since rule S has not run. Never silently upgrade to admitted.
        verdicts += [Verdict(t, False, (), "<rule S not run>", 0.0) for t in survivors]
        return sorted(verdicts, key=lambda v: v.term)

    threshold = semantic_threshold()
    nearest = nearest_corpus_keyword(survivors, encoder, sorted(keywords))
    for term in survivors:
        key, score = nearest.get(term, ("", 0.0))
        verdicts.append(Verdict(term, score < threshold, (), key, score))
    return sorted(verdicts, key=lambda v: v.term)


def admitted_terms(verdicts: Iterable[Verdict]) -> List[str]:
    return sorted(v.term for v in verdicts if v.admitted)


# ── committed result ──────────────────────────────────────────────────────────

def committed_admission(bank: Sequence[str]) -> Dict[str, Tuple[str, float]]:
    """The audited `{term: (nearest keyword, cosine)}` for every banked term.

    A term added to the bank without re-auditing fails here rather than being
    silently injected into profiles unchecked.
    """
    missing = sorted(t for t in bank if t not in ADMITTED)
    if missing:
        raise AdmissionError(
            f"not audited: {', '.join(missing)}. "
            "Run: python eval/distractors.py --emit")
    return {t: ADMITTED[t] for t in bank}


#: Audited on the committed 150-posting corpus against `all-MiniLM-L6-v2` at
#: `SkillMatcherAgent.SEMANTIC_THRESHOLD`. `{term: (nearest corpus keyword,
#: cosine)}` — the nearest keyword is kept, not just the score, because it is what
#: makes a rejection reviewable. Regenerate with `--emit`.
ADMITTED: Dict[str, Tuple[str, float]] = {
    "ANSYS": ("ats", 0.5619),
    "Ada": ("assistant", 0.4933),
    "AutoCAD": ("automate", 0.5373),
    "COBOL": ("corl", 0.5732),
    "ColdFusion": ("cfo", 0.6071),
    "Delphi": ("phi", 0.5337),
    "Erlang": ("ergs", 0.6200),
    "Fortran": ("fort", 0.5755),
    "GAMS": ("gtm", 0.6050),
    "Grasshopper": ("flywheel", 0.4652),
    "Haskell": ("fqhcs", 0.4893),
    "Julia": ("monica", 0.5961),
    "LaTeX": ("document", 0.5771),
    "Maple": ("grid", 0.4870),
    "Mathematica": ("mathematical", 0.6352),
    "NVivo": ("9nvo4", 0.5808),
    "OpenFOAM": ("mozilla", 0.5251),
    "Pascal": ("combinations", 0.5479),
    "Praat": ("ppt", 0.5511),
    "Prolog": ("logic", 0.5198),
    "QGIS": ("sharepoint", 0.6009),
    "Rhino 3D": ("3d-vision", 0.4840),
    "Scribus": ("scooter", 0.5236),
    "Sibelius": ("sigint", 0.6413),
    "Simulink": ("sim", 0.4944),
    "SolidWorks": ("solid", 0.5568),
    "Smalltalk": ("communicates", 0.5377),
    "Stata": ("playa", 0.5504),
    "VBScript": ("scripting", 0.5607),
    "VHDL": ("virtual", 0.5301),
    "Verilog": ("fpga", 0.4821),
}

#: Candidates the audit rejected, kept so the bank records what was tried and
#: why. Every semantic rejection here is an orthographic artefact rather than a
#: relevance match — see the module docstring.
REJECTED: Dict[str, str] = {
    "AMPL": "semantic 0.6578 (nearest 'amplify')",
    "ArcGIS": "semantic 0.6863 (nearest 'arc')",
    "Blender": "semantic 0.6703 (nearest 'blends')",
    "CATIA": "semantic 0.6659 (nearest 'scania')",
    "ELAN": "semantic 0.7085 (nearest 'elt')",
    "F#": "semantic 0.7210 (nearest 'f-1')",
    "Finale": "semantic 0.6691 (nearest 'final')",
    "InDesign": "semantic 0.6509 (nearest 'ind')",
    "LabVIEW": "semantic 0.6878 (nearest 'lab')",
    "Objective-C": "semantic 0.6969 (nearest 'objective')",
    "PeopleSoft": "semantic 0.6737 (nearest 'peoples')",
    "QuickBooks": "semantic 0.6629 (nearest 'playbooks')",
    "Atlas.ti": "lexical: shares 'atlas'",
    "Common Lisp": "lexical: shares 'common'",
    "Crystal Reports": "lexical: shares 'reports'",
    "GRASS GIS": "lexical: shares 'gis'",
    "Lotus Notes": "lexical: shares 'notes'",
    "Max/MSP": "lexical: shares 'max'",
    "OCaml": "lexical: shares 'ocaml'",
    "Perl": "lexical: shares 'perl'",
    "Pro Tools": "lexical: shares 'pro', 'tools'",
    "SAP ABAP": "lexical: shares 'abap', 'sap'",
    "SPSS": "lexical: shares 'spss'",
    "Scheme": "lexical: shares 'scheme'",
    "Unreal Engine": "lexical: shares 'engine', 'unreal'",
}


def select_for(profile_skills: Sequence[str], n: int,
               extra: Sequence[str] = ()) -> List[str]:
    """The `n` distractors to inject into a profile that already claims
    *profile_skills*, plus any profile-specific *extra* from its sidecar.

    Deterministic: the admitted pool is taken in sorted order, so the same
    (profile, n) always injects the same set and two runs are comparable
    (#158/#171). Terms the profile already claims are skipped — injecting a
    duplicate would change nothing but would make `n` mean different things for
    different candidates.

    Asking for more than the pool holds returns the whole pool rather than
    raising: a difficulty dial that errors at its top setting is worse than one
    that saturates, and the run banner reports what was actually injected.
    """
    claimed = {(s or "").strip().lower() for s in profile_skills}
    chosen: List[str] = []
    for term in list(extra) + sorted(ADMITTED):
        if len(chosen) >= n:
            break
        key = term.strip().lower()
        if key in claimed:
            continue
        claimed.add(key)
        chosen.append(term)
    return chosen


def live_encoder() -> Callable[[List[str]], object]:
    """The production encoder, or a clear error naming the install command."""
    try:
        from agents.matcher import get_embedding_model

        model = get_embedding_model()
    except Exception as exc:  # not installed / offline / OOM
        raise AdmissionError(
            f"the production encoder is unavailable ({type(exc).__name__}: {exc}). "
            "Install it with: pip install -r requirements-full.txt") from exc
    if model is None:
        raise AdmissionError(
            "get_embedding_model() returned None — rule S replays the matcher's "
            "semantic channel and cannot run without its model.")
    return lambda texts: model.encode(texts, normalize_embeddings=True,
                                      batch_size=256)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--emit", action="store_true",
                    help="print an ADMITTED literal to paste back into this file")
    ap.add_argument("--verbose", action="store_true",
                    help="show rejected candidates and their reasons")
    args = ap.parse_args(argv)

    from eval.profile_banks import DISTRACTOR_CANDIDATES

    keywords = corpus_keywords()
    print(f"corpus keywords: {len(keywords)}    "
          f"candidates: {len(DISTRACTOR_CANDIDATES)}    "
          f"semantic threshold: {semantic_threshold()}")
    verdicts = audit(DISTRACTOR_CANDIDATES, live_encoder(), keywords)
    ok = [v for v in verdicts if v.admitted]
    bad = [v for v in verdicts if not v.admitted]
    print(f"admitted {len(ok)} / {len(verdicts)}\n")
    for verdict in ok:
        print(f"  ok     {verdict.nearest_score:.4f}  {verdict.term:<14} "
              f"nearest={verdict.nearest_keyword}")
    if args.verbose and bad:
        print()
        for verdict in bad:
            print(f"  reject {verdict.term:<14} {verdict.reason}")

    if args.emit:
        print("\nADMITTED: Dict[str, Tuple[str, float]] = {")
        for verdict in ok:
            print(f'    "{verdict.term}": ("{verdict.nearest_keyword}", '
                  f'{verdict.nearest_score:.4f}),')
        print("}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
