"""
SyntheticUserAgent — generates user utterances from scenario contracts.

Modes:
  canonical  — replay exact hand-authored turns (deterministic, no LLM needed)
  synthetic  — generate paraphrases via the eval model (requires configured LLM)
  mixed      — canonical variant first, then synthetic paraphrases
"""
import logging
from typing import List

logger = logging.getLogger(__name__)


class SyntheticUserAgent:
    def __init__(self, scenario: dict, mode: str = "canonical"):
        self.scenario = scenario
        self.mode = mode
        self._canonical: List[str] = [
            t["user"] for t in scenario.get("canonical_turns", [])
        ]

    def generate_variants(self, n: int = 1) -> List[List[str]]:
        """Return up to n variants of the conversation turns.

        In canonical mode always returns exactly 1 variant (the hand-authored turns).
        In synthetic mode returns n variants (canonical + n-1 paraphrases).
        In mixed mode returns the canonical variant plus up to n-1 paraphrases.
        """
        if self.mode == "canonical" or n <= 1:
            return [list(self._canonical)]

        variants: List[List[str]] = [list(self._canonical)]
        needed = n - 1 if self.mode in ("synthetic", "mixed") else 0

        for _ in range(needed):
            try:
                paraphrased = self._paraphrase_turns(self._canonical)
                variants.append(paraphrased)
            except Exception as exc:
                logger.warning("Paraphrase generation failed, using canonical: %s", exc)
                variants.append(list(self._canonical))

        return variants[:n]

    def _paraphrase_turns(self, turns: List[str]) -> List[str]:
        """Use the eval LLM to paraphrase each canonical turn."""
        from llm import get_llm, ModelRole
        llm = get_llm(role=ModelRole.EVAL, temperature=0.9)
        result: List[str] = []
        for turn in turns:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a user paraphrase generator for a resume assistant evaluation system. "
                        "Given a user message, return a natural plain-English paraphrase with the same "
                        "intent but different wording. "
                        "Rules: plain English only, no internal tool names (like run_ingest_github), "
                        "no slash commands unless the original uses one. "
                        "Return just the paraphrase, nothing else."
                    ),
                },
                {"role": "user", "content": f"Paraphrase this message: {turn}"},
            ]
            try:
                response = llm.invoke(messages)
                text = response.content if hasattr(response, "content") else str(response)
                result.append(text.strip() or turn)
            except Exception:
                result.append(turn)
        return result
