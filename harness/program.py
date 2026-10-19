"""Plan programs (issue #197): the whole plan a host submits as one document.

A program names a parent version and a list of typed nodes drawn from the
planner's action space (`agents.tailor_planner.OPS`; `keyword_weave` and the
other rewrites are *strategies* of `revise`). The host names stable ids — item
keys, evidence ids (`<item key>#b<n>`), preference ids — and ART resolves them;
it never parses display text. See docs/harness.md § 7.

Validation here is structural only. Whether a key exists, a cite resolves or a
preference forbids a node is arbitration, which needs the store and lives in
`harness/executor.py`.

Every node of a new program carries a short `because` (#392), which the dry run's
`plan_summary` shows the user. A program saved before that rule still replays: `patch_plan`
validates with `LEGACY_NODES` in the validation context, naming the nodes it carried over
unchanged from the saved program, and those are not held to it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from agents.checks import PAGE_LINE_BUDGET
from agents.tailor_planner import OPS, REVISION_STRATEGIES
from harness.acceptance import DEFAULT_TOLERANCES, GUARDS, TARGETS
from harness.rules import MAX_PAGES

# `delete_bullet` (#289) is a plan op only: it removes whole bullets from one item. The planner's
# own action space (`OPS`) is unchanged, so the planner and `suggest_actions` never propose it.
PLAN_OPS = (*OPS, "delete_bullet")
Op = Literal[PLAN_OPS]  # type: ignore[valid-type]
Strategy = Literal[REVISION_STRATEGIES]  # type: ignore[valid-type]
Target = Literal[TARGETS]  # type: ignore[valid-type]
Guard = Literal[GUARDS]  # type: ignore[valid-type]
Section = Literal["education", "experience", "projects", "skills", "achievements"]

# What a node's `because` may say (#392): what in the posting calls for it, what the user asked,
# or a stored preference. Only the last two are requests: a `delete` or `delete_bullet` with one
# is judged on the hard gates alone (#279); a `job:` reason is the host's own reading.
REASON_PREFIXES = ("job:", "user:", "pref:")
REQUEST_PREFIXES = ("user:", "pref:")
MAX_REASON_CHARS = 240
# Validation-context key: ids of nodes carried over unchanged from a saved program (`patch_plan`),
# which are not held to the #392 rule, so a program saved before it still replays.
LEGACY_NODES = "legacy_nodes"


def is_request(because: Optional[str]) -> bool:
    """Whether a node's reason is the user's or a preference's (a requested delete, #279)."""
    return (because or "").strip().startswith(REQUEST_PREFIXES)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanBullet(_M):
    text: str = Field(min_length=1)
    cites: List[str] = Field(
        default_factory=list,
        description="Stable ids this bullet rests on: item keys (exp:/proj:/skill:/edu:/ach:) "
                    "or source bullets as '<item key>#b<n>'. At least one is required, "
                    "unless the bullet names an approved variant (from_variant), whose own "
                    "cites are then used.")
    from_variant: Optional[str] = Field(
        None, description="The approved bullet variant (a variant_id from get_item or "
                          "suggest_actions) this bullet starts from (#229). Only approved "
                          "variants of the item are accepted, and the bullet may not drift "
                          "from it past the variant_drift tolerance.")


class Accept(_M):
    improves: List[Target] = Field(
        default_factory=list,
        description="Targets this node must improve (any target when empty).")
    tolerances: Dict[Guard, float] = Field(
        default_factory=dict,
        description="Tighter guard tolerances for this node. Loosening one is rejected.")


def _because_required(schema: Dict) -> None:
    """The published schema lists `because` as required (#392); the model leaves it optional
    so that a program saved before the rule still loads (see `LEGACY_NODES`)."""
    required = schema.setdefault("required", [])
    if "because" not in required:
        required.append("because")


class ProgramNode(_M):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_because_required)

    id: str = Field(min_length=1)
    op: Op
    item_key: str = Field(min_length=1, description="exp:... or proj:... from list_items.")
    strategy: Optional[Strategy] = None
    keywords: List[str] = Field(default_factory=list)
    from_variant: Optional[str] = Field(
        None, description="Shorthand for a one-bullet revise: the approved variant that "
                          "bullet starts from. With any other number of bullets, name "
                          "variants per bullet (bullets[].from_variant) instead (#229).")
    bullets: Optional[List[PlanBullet]] = Field(
        None, description="The item's full bullet list after this node (revise; optional "
                          "for replace, which otherwise uses the replacement's source bullets).")
    cut_bullets: List[str] = Field(
        default_factory=list,
        description="delete_bullet: the bullets to remove from item_key, as '<item key>#b<n>' "
                    "(n = the bullet's 0-based index in the item's list on the parent version), "
                    "exactly the `bullet` of cut_hints' cut_bullet entries (#289). The other "
                    "bullets are kept verbatim.")
    replacement_key: Optional[str] = None
    use_variant: Optional[str] = None
    because: Optional[str] = Field(
        None, description="Required on every node (#392): why, in a short phrase the user reads in "
                          "the dry run's plan_summary. 'job:<what in the posting calls for it>', "
                          "'user:<what they asked>' or 'pref:<preference_id>'. A delete or "
                          "delete_bullet whose reason is user: or pref: is a requested delete: it "
                          "needs no target to improve, and no guard vetoes it (hard gates still do; "
                          "#279). A job: reason is your own reading, so such a delete is judged "
                          "like any edit.")
    accept: Accept = Field(default_factory=Accept)

    @model_validator(mode="after")
    def _shape(self, info: ValidationInfo):
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
        if op in ("keep", "delete", "delete_bullet") and self.bullets is not None:
            raise ValueError(f"node {self.id}: {op} takes no bullets")
        if op == "delete_bullet":
            if not self.cut_bullets:
                raise ValueError(f"node {self.id}: delete_bullet needs cut_bullets")
            prefix = self.item_key.strip().lower() + "#b"
            for ref in self.cut_bullets:
                idx = ref.strip().lower()[len(prefix):]
                if not ref.strip().lower().startswith(prefix) or not idx.isdigit():
                    raise ValueError(
                        f"node {self.id}: cut_bullets entry {ref!r} must be '{self.item_key}#b<n>' "
                        "(a bullet of this node's item_key)")
        elif self.cut_bullets:
            raise ValueError(f"node {self.id}: cut_bullets is for delete_bullet")
        if self.from_variant:
            # A variant is one bullet, so a node-level name is only unambiguous for one bullet.
            if op != "revise" or not self.bullets or len(self.bullets) != 1:
                raise ValueError(
                    f"node {self.id}: from_variant names one variant, so it needs a revise "
                    "with exactly one bullet; with several bullets name a variant on each "
                    "(bullets[].from_variant)")
            bullet = self.bullets[0]
            if bullet.from_variant and bullet.from_variant != self.from_variant:
                raise ValueError(f"node {self.id}: from_variant and bullets[0].from_variant "
                                 "name different variants")
            bullet.from_variant = self.from_variant
        if self.strategy and op != "revise":
            raise ValueError(f"node {self.id}: strategy is for revise")
        if (self.because or "").strip() and not self.because.startswith(REASON_PREFIXES):
            raise ValueError(f"node {self.id}: because must start with job:, user: or pref:")
        for guard, value in self.accept.tolerances.items():
            if value < 0 or value > DEFAULT_TOLERANCES[guard]:
                raise ValueError(
                    f"node {self.id}: tolerance {guard}={value} loosens the default "
                    f"{DEFAULT_TOLERANCES[guard]}; a node may only tighten")
        if not self.item_key.startswith(("exp:", "proj:")):
            raise ValueError(f"node {self.id}: item_key must be an exp: or proj: key")
        if self.id not in ((info.context or {}).get(LEGACY_NODES) or ()):
            self._check_reason()
        return self

    def _check_reason(self) -> None:
        """The #392 rule: a short reason on every node of a new program."""
        where = f"node {self.id} ({self.op} {self.item_key})"
        because = (self.because or "").strip()
        if not because:
            raise ValueError(
                f"{where}: because is required on every node. Say why in a short phrase the "
                "user will read: 'job:<what in the posting calls for it>', 'user:<what they "
                "asked>' or 'pref:<preference_id>'")
        if not because.partition(":")[2].strip():
            raise ValueError(f"{where}: because {because!r} gives no reason after the prefix")
        if len(because) > MAX_REASON_CHARS:
            raise ValueError(f"{where}: because is {len(because)} characters; keep it under "
                             f"{MAX_REASON_CHARS}, a short phrase")


class Finalize(_M):
    """What finalize checks. A field left unset takes its default, and the user's document rules
    (#298, `harness/rules.py`) set the defaults: a plan may tighten one, never loosen it past a
    hard rule. The built-in defaults are `line_budget` one page, `max_skills`/`min_skills` from
    the skills scorer, `max_bullet_lines` 2."""
    line_budget: Optional[float] = Field(
        None, gt=0, le=MAX_PAGES * PAGE_LINE_BUDGET,
        description="Page budget in bullet lines (#200). Unset: the rules' pages, else one page "
                    f"({PAGE_LINE_BUDGET}).")
    max_skills: Optional[int] = Field(None, ge=1)
    min_skills: Optional[int] = Field(None, ge=0)
    max_bullet_lines: Optional[int] = Field(None, ge=1, le=2)
    bullets_per_item: Optional[int] = Field(
        None, ge=1, description="Most bullets under one role or project (#298). Unset: the "
                                "rules', else no limit.")
    keep_sections: List[Section] = Field(
        default_factory=list,
        description="Sections to keep although a soft rule leaves them off (#298). Naming one a "
                    "hard rule leaves off is a violation.")


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
    discard_editor_edits: bool = Field(
        False, description="Set only after asking the user: commit even though the parent holds "
                           "hand edits to its .tex that this plan does not carry over (#278). "
                           "Without it such a commit is refused (violation discards_editor_edits).")

    @model_validator(mode="after")
    def _unique(self):
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node ids must be unique")
        keys = [n.item_key.strip().lower() for n in self.nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("one node per item_key")
        fin = self.finalize
        if (fin.min_skills is not None and fin.max_skills is not None
                and fin.min_skills > fin.max_skills):
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
