"""Plan programs (issue #197): the whole plan a host submits as one document.

A program names a parent version and a list of typed nodes drawn from the
planner's action space (`agents.tailor_planner.OPS`; `keyword_weave` and the
other rewrites are *strategies* of `revise`). The host names stable ids — item
keys, evidence ids (`<item key>#b<n>`), preference ids — and ART resolves them;
it never parses display text. See docs/harness.md § 7.

Validation here is structural only. Whether a key exists, a cite resolves or a
preference forbids a node is arbitration, which needs the store and lives in
`harness/executor.py`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.checks import PAGE_LINE_BUDGET
from agents.skill_scorer import MAX_SKILLS, MIN_SKILLS
from agents.tailor_planner import OPS, REVISION_STRATEGIES
from harness.acceptance import DEFAULT_TOLERANCES, GUARDS, TARGETS

Op = Literal[OPS]  # type: ignore[valid-type]
Strategy = Literal[REVISION_STRATEGIES]  # type: ignore[valid-type]
Target = Literal[TARGETS]  # type: ignore[valid-type]
Guard = Literal[GUARDS]  # type: ignore[valid-type]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanBullet(_M):
    text: str = Field(min_length=1)
    cites: List[str] = Field(
        default_factory=list,
        description="Stable ids this bullet rests on: item keys (exp:/proj:/skill:/edu:/ach:) "
                    "or source bullets as '<item key>#b<n>'. At least one is required.")


class Accept(_M):
    improves: List[Target] = Field(
        default_factory=list,
        description="Targets this node must improve (any target when empty).")
    tolerances: Dict[Guard, float] = Field(
        default_factory=dict,
        description="Tighter guard tolerances for this node. Loosening one is rejected.")


class ProgramNode(_M):
    id: str = Field(min_length=1)
    op: Op
    item_key: str = Field(min_length=1, description="exp:... or proj:... from list_items.")
    strategy: Optional[Strategy] = None
    keywords: List[str] = Field(default_factory=list)
    from_variant: Optional[str] = Field(
        None, description="The approved bullet variant this revision starts from (#199).")
    bullets: Optional[List[PlanBullet]] = Field(
        None, description="The item's full bullet list after this node (revise; optional "
                          "for replace, which otherwise uses the replacement's source bullets).")
    replacement_key: Optional[str] = None
    use_variant: Optional[str] = None
    because: Optional[str] = Field(
        None, description="Why a delete is wanted: 'pref:<preference_id>' or 'user:<what they "
                          "asked>'. A delete with a reason needs no target to improve.")
    accept: Accept = Field(default_factory=Accept)

    @model_validator(mode="after")
    def _shape(self):
        op = self.op
        if op == "revise" and not self.bullets:
            raise ValueError(f"node {self.id}: revise needs bullets")
        if op == "replace":
            if not self.replacement_key:
                raise ValueError(f"node {self.id}: replace needs replacement_key")
            if not (self.item_key.startswith("proj:") and self.replacement_key.startswith("proj:")):
                raise ValueError(f"node {self.id}: replace swaps one project for another")
        elif self.replacement_key or self.use_variant:
            raise ValueError(f"node {self.id}: replacement_key/use_variant are for replace")
        if op in ("keep", "delete") and self.bullets is not None:
            raise ValueError(f"node {self.id}: {op} takes no bullets")
        if self.strategy and op != "revise":
            raise ValueError(f"node {self.id}: strategy is for revise")
        if self.because and not self.because.startswith(("pref:", "user:")):
            raise ValueError(f"node {self.id}: because must start with pref: or user:")
        for guard, value in self.accept.tolerances.items():
            if value < 0 or value > DEFAULT_TOLERANCES[guard]:
                raise ValueError(
                    f"node {self.id}: tolerance {guard}={value} loosens the default "
                    f"{DEFAULT_TOLERANCES[guard]}; a node may only tighten")
        if not self.item_key.startswith(("exp:", "proj:")):
            raise ValueError(f"node {self.id}: item_key must be an exp: or proj: key")
        return self


class Finalize(_M):
    line_budget: float = Field(PAGE_LINE_BUDGET, gt=0, le=PAGE_LINE_BUDGET,
                               description="Page budget in bullet lines (#200).")
    max_skills: int = Field(MAX_SKILLS, ge=1)
    min_skills: int = Field(MIN_SKILLS, ge=0)
    max_bullet_lines: int = Field(2, ge=1, le=2)


class HostInfo(_M):
    name: Optional[str] = None
    version: Optional[str] = None
    model: Optional[str] = None


class Program(_M):
    job_id: str
    parent: Optional[str] = Field(
        None, description="The node this plan builds on — HEAD from get_head. Null only "
                          "for a job with no history yet.")
    nodes: List[ProgramNode] = Field(default_factory=list)
    skills: Optional[List[str]] = Field(
        None, description="The final skills list, in order. Omit to keep the parent's.")
    section_order: Optional[List[str]] = None
    finalize: Finalize = Field(default_factory=Finalize)
    host: HostInfo = Field(default_factory=HostInfo)

    @model_validator(mode="after")
    def _unique(self):
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node ids must be unique")
        keys = [n.item_key.strip().lower() for n in self.nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("one node per item_key")
        if self.finalize.min_skills > self.finalize.max_skills:
            raise ValueError("finalize.min_skills exceeds max_skills")
        return self


def canonical(program: Dict) -> str:
    """The program as canonical JSON: what `program_id` hashes."""
    return json.dumps(program, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def program_id(program: Dict) -> str:
    return "prog_" + hashlib.sha256(canonical(program).encode("utf-8")).hexdigest()[:16]


def apply_patch(doc: Dict, edits: List[Dict]) -> Dict:
    """RFC 6902 `add` / `remove` / `replace` by JSON pointer, on a copy.

    Enough of the RFC for a host to amend a saved program without resending it
    (docs/harness.md § 7 step 4). Raises ValueError on a bad pointer or op.
    """
    doc = json.loads(json.dumps(doc))

    def parts(pointer: str) -> List[str]:
        if pointer == "":
            return []
        if not pointer.startswith("/"):
            raise ValueError(f"bad pointer {pointer!r}")
        return [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")]

    for edit in edits:
        try:
            _apply_one(doc, edit, parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"cannot apply {edit!r}: {exc!r}") from None
    return doc


def _apply_one(doc: Dict, edit: Dict, parts) -> None:
    op, path = edit.get("op"), parts(edit.get("path", ""))
    if op not in ("add", "remove", "replace") or not path:
        raise ValueError(f"unsupported edit {edit!r}")
    parent = doc
    for p in path[:-1]:
        parent = parent[int(p)] if isinstance(parent, list) else parent[p]
    last = path[-1]
    if isinstance(parent, list):
        idx = len(parent) if (op == "add" and last == "-") else int(last)
        if op == "add":
            parent.insert(idx, edit["value"])
        elif op == "remove":
            parent.pop(idx)
        else:
            parent[idx] = edit["value"]
    else:
        if op in ("remove", "replace") and last not in parent:
            raise ValueError(f"no member {last!r} at {edit['path']!r}")
        if op == "remove":
            del parent[last]
        else:
            parent[last] = edit["value"]
