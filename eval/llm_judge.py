"""
LLM-as-judge resume-quality scoring (carries the aim of issue #27 into the
tailoring benchmark).

The algorithmic metrics in eval/metrics.py measure structure (coverage,
allocation, repetition counts). This judge scores what they cannot: whether the
tailored resume actually *reads* well against the job description. One LLM call
per (resume, JD) pair, three 1-5 scores plus a one-line rationale each:

  - relevance_balance : does the most JD-relevant content get the most space?
  - redundancy        : is wording varied, or the same skill terms stuffed?
  - faithfulness      : does it stay within what the source profile supports?

Used by eval/tailoring_benchmark.py --judge (real-LLM mode only) and by the
integration-gated test in tests/test_tailoring_benchmark.py.
"""
import json
import logging
from typing import Dict, Optional

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)

_AXES = ("relevance_balance", "redundancy", "faithfulness")

_SYSTEM = (
    "You are a strict resume-quality judge. You compare a tailored resume "
    "against the job description it was tailored for and the candidate's "
    "source profile. Score each axis 1-5 (5 = excellent):\n"
    "- relevance_balance: the most job-relevant experiences/projects get the "
    "most detail; irrelevant items are brief\n"
    "- redundancy: 5 = varied wording, no term stuffed; 1 = the same skill "
    "terms repeated over and over across bullets and sections\n"
    "- faithfulness: 5 = every claim is supported by the source profile; "
    "1 = fabricated skills, metrics, or experiences\n"
    "Respond with ONLY valid JSON."
)

_USER = (
    "JOB DESCRIPTION:\n{jd_text}\n\n"
    "SOURCE PROFILE (ground truth):\n{profile_text}\n\n"
    "TAILORED RESUME (JSON sections):\n{tailored_json}\n\n"
    "Return JSON: {{\"relevance_balance\": {{\"score\": 1-5, \"rationale\": \"...\"}}, "
    "\"redundancy\": {{\"score\": 1-5, \"rationale\": \"...\"}}, "
    "\"faithfulness\": {{\"score\": 1-5, \"rationale\": \"...\"}}}}"
)


def judge_resume_quality(
    tailored_content: Dict,
    jd_text: str,
    profile_text: str,
    llm=None,
) -> Optional[Dict]:
    """
    Score one tailored resume with an LLM judge. Returns
    {axis: {score, rationale}, mean_score} or None when judging fails —
    the benchmark records the miss rather than crashing the run.
    """
    if llm is None:
        from llm import get_llm
        llm = get_llm(role="eval", temperature=0.0)

    prompt = ChatPromptTemplate.from_messages([("system", _SYSTEM), ("user", _USER)])
    chain = prompt | llm | JsonOutputParser()
    try:
        out = chain.invoke({
            "jd_text": (jd_text or "")[:4000],
            "profile_text": (profile_text or "")[:4000],
            "tailored_json": json.dumps(
                {k: tailored_content.get(k) for k in
                 ("experiences", "projects", "skills_ranked", "skills_emphasized")},
                ensure_ascii=False,
            )[:6000],
        })
    except Exception as e:
        logger.warning("LLM judge failed: %s", e)
        return None

    scores = []
    result: Dict = {}
    for axis in _AXES:
        entry = out.get(axis) if isinstance(out, dict) else None
        if not isinstance(entry, dict) or "score" not in entry:
            logger.warning("LLM judge returned malformed axis %r: %r", axis, entry)
            return None
        score = entry["score"]
        if not isinstance(score, (int, float)) or not 1 <= score <= 5:
            logger.warning("LLM judge score out of range for %r: %r", axis, score)
            return None
        result[axis] = {"score": score, "rationale": str(entry.get("rationale", ""))}
        scores.append(float(score))
    result["mean_score"] = round(sum(scores) / len(scores), 2)
    return result
