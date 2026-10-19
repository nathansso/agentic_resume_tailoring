---
name: artie-tailor
description: Tailor the user's resume to a job posting with ART. Use when the user shares a job posting or link and wants their resume tailored, or asks to re-tailor, cut, or rework a resume for a specific role. Reads their knowledge graph, drafts a plan, waits for their approval, then executes and renders a one-page PDF.
---

# Tailor a resume with ART

ART holds the user's facts (the knowledge graph), their pinned preferences, and every
tailored version. You do the reading and writing. ART checks each change and commits
it. Every fact you put on the page must come from an ART tool result. **Never invent a
metric, date, employer, tool or outcome.** If the user needs something the graph
doesn't hold, ask them, and store it with `upsert_items` first (see the `artie-setup`
skill).

The tools named below are the ART MCP server's tools.

## 1. Read the brief

- Call `art_briefing` with the posting's role family if it is obvious. The families are
  `software_engineering`, `machine_learning`, `data_science`, `data_engineering`,
  `research`, `product_management`, `design`, `devops_infrastructure`, `security`,
  `hardware` and `other`.
- When you work on an existing job, or once `open_job` returns the job id, call
  `art_briefing` with `job_id` instead. It adds the job's own preferences and its recorded
  role family, which is exactly the set `execute_plan` enforces.
- **Pins are hard rules.** Quote them to yourself and honour them verbatim.
- **Page rules** (`rules` in the briefing) are what the user said about the page as a whole:
  `hard` ones (a page count, a bullets-per-item or skills limit, a section left off, a section
  order) are enforced, and `execute_plan` refuses a plan that loosens one; `soft` ones are
  defaults you may override, and the result reports them applied or not; `notes` are style
  notes (tone, wording): follow them, nothing blocks. A section a hard rule leaves off is
  already off the page, so plan nothing for its items.
- Note `job_rules`: yes/no questions about the posting, such as whether it requires
  enrollment after the internship. Answer each from the posting's own words, keeping
  the quote. If the posting doesn't say, ask the user.
- Call `get_profile`. If the name is "Default User" or contact fields are empty, ask
  the user and call `update_profile`.

## 2. Open the job

- Get the posting text. If the user gave a URL, fetch it.
- Extract its requirements as atomic statements. `ingest_schema("requirement")` gives
  the shape. Fill `type` (required, preferred or incidental), `criticality` (1–5),
  `terms`, and `source_section` with the posting's own heading.
- Call `open_job` with `jd_text`, `requirements`,
  `metadata: {title, company, url, role_family}` and `rule_answers`. Give `role_family`
  from your own reading of the posting; without it ART guesses from the title.
- If `baseline` comes back with `applies: true`, the job's first version starts as a copy
  of the user's saved baseline for that track instead of the whole graph. Say so to the
  user. The copy is made when the job's first version is committed, so `get_head` returns no
  version until then. To read the baseline's wording before planning, commit it with an
  empty plan (`execute_plan` with `parent` null and `nodes: []`), then call `get_head`.
  `baseline.source` says who chose the
  track: `host` (the `role_family` you gave names a saved track exactly, so ART did not ask
  Jev), `jev` or `cache` (Jev chose among the user's saved tracks by reading the posting;
  `p` is its probability, not a calibrated confidence), or `fallback` (no Jev answer, so
  the track named after the role family). If `baseline` is null, no saved track fit (Jev's
  answer included) and the job starts from the whole graph. Give `role_family` only when you
  are sure of the family: a value that names a saved track overrides Jev.
- Keep the `job_id`. Look at `top_terms`: these are the weighted terms the candidate
  can support.
- `skill_matches` lists the requirement terms that name skills the candidate has, exactly
  or through ART's alias map; those skills lead the default skills list. Each term in
  `unmatched_terms` names no skill. ART does not guess: `kg_search` for it under another
  name, ask the user, and never claim a skill without evidence.
- If any rule comes back `needs_answer`, ask the user, then call
  `open_job(job_id=…, rule_answers=…)` again.

## 3. Read the candidate

- Call `list_items` to see every experience, project, skill, education entry and
  achievement.
- Call `get_item` for each experience and project you might use. It returns the source
  bullets. Evidence ids are `<item key>#b<n>`, the n-th source bullet, counting from 0.
- A project's record says where it was done: `context` is its role or degree, and
  `context_status` is `personal` for the user's own project. Work is stronger evidence
  than coursework; never present a course or personal project as a job. A role's
  record lists its `projects`; an achievement's `project` is what it was won for.
- `get_item` also lists the item's `variants`: wording the user approved for that
  experience or project. Approved wording beats regenerated wording (see step 4).
- If the user wants their GitHub repos considered, have them run `artie github login` in their
  own terminal (`--private` for private repos; the tool's `not_logged_in` error repeats it).
  If `artie` is not on their PATH, the same command is
  `uvx --python 3.12 --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.2.0/art_mcp-0.2.0-py3-none-any.whl" artie github login`.
  Then call `list_github_repos`. Read the repos
  worth reading, clone one only after they agree, and store what you learn with
  `upsert_items` (`source: github:<repo>`) before you cite it; a repo is not evidence until
  it is stored.
- Call `get_head(job_id)`. If the job already has a version, build on it; the plan's
  `parent` must be its `node_id`. Respect anything under `editor_edits`: the user made
  those changes by hand. When the user edited the `.tex`, `editor_edits[].changes.tex_edits`
  lists the bullets they revised, added or removed (`diff_nodes` has the whole list), and
  anything outside the bullets as an `unmapped` diff.
- A commit on top of a `.tex` edit is refused (`discards_editor_edits`, listing the edited
  bullets) because a new version is built from structured content and would drop the
  user's text. Carry the edits over as nodes: a `revise` for each reworded or added bullet
  with the user's text, a `delete_bullet` with a `because` for each bullet they removed,
  then run again. Send the user's text **verbatim** (the `after` of each `tex_edits`
  entry, the rest of the item's bullets unchanged) and the executor recognises it against
  their `.tex` (`carried_edit` on the node): no guard vetoes the user's own words, and
  any MTLD or keyword-density drop is listed under `waived`, so tell them what it cost.
  Hard gates still apply. If one refuses it, the node's `reason` names the gate and the
  claim (a number or name its cited evidence does not state): record that fact with
  `upsert_items` (or ask the user whether it is true), cite it, and retry. Do not reword
  the user's text to get past a gate: reworded, it is an ordinary revise and every guard
  applies again. An `unmapped` region cannot be carried. When
  the user does not want an edit kept, ask them, and only then set
  `discard_editor_edits: true`; the response lists what was discarded, so tell them.

## 4. Draft the plan and get approval

Call `suggest_actions(job_id)` first. **Read `active_constraints` at the top before you
choose or write anything**: every hard pin, pending ban, preference and page rule the user has
stated that binds this job, each with its scope (an `execute_plan` dry run leads with the same
block). A hard pin or hard rule you break is refused; the rest are the user's stated wishes, so
follow them too. For each experience and project on the current
version it names the approved variant that best fits this posting (`match: variant`,
with `variant.variant_id`, `text`, `cites` and `score`) or `no_match`, and the actions
that are valid for the item. Jev makes the choice (`source: jev`, or `cache` when it is an
earlier answer replayed): `variant.score` is its probability for that variant and
`propensity` is its whole distribution over the item's variants and `no_match`. Jev picks
its likeliest variant when it puts at least 0.5 of its probability on the variants
together (1 − `no_match`), so a close call between two good phrasings is picked even with
each near 0.4 in `propensity`: read the runner-up there and, if it reads better against
the posting, you may use it instead. `no_match` means Jev put more than half its
probability on none fitting. With `source: fallback` Jev did not answer (no key, switched off, an
error, or the item has no approved variant) and a word-overlap rule picked, whose `score`
is an overlap share, not a probability, and which can miss a variant worded differently
from the posting. **Start from the approved text, not from the raw facts:**

- `match: variant`: revise the item from that variant. Put its `text` in the bullet
  (verbatim, or edited lightly to weave in a term the cited evidence supports) and set
  that bullet's `from_variant` to its `variant_id`. Leave `cites` empty to use the
  variant's own. A bullet that strays too far from its variant is reverted (the
  `variant_drift` guard), so edit a few words, not the whole bullet.
- `no_match`: write the bullets from the raw facts as below. Only these items are
  generated from scratch.

Draft a plan program for `execute_plan`. Its input schema is published with the tool.

- **nodes**, one per experience or project you touch. **Every node needs a short `because`**,
  the reason the user will read: `job:<what in the posting calls for it>`, `user:<what they
  asked>` or `pref:<preference_id>`. A node without one is refused.
  - `keep`: include the item unchanged.
  - `revise`: rewrite its bullets. Give the full new `bullets` list; each bullet
    `cites` the evidence ids it rests on (or names a `from_variant`). Set `strategy` to one of `keyword_weave`,
    `quantify`, `tighten` or `reframe`, and `keywords` to the posting terms you are
    weaving in. Only weave terms the cited evidence supports.
  - `replace`: swap one project for another (`replacement_key`).
  - `delete`: drop the item. A `because` of `user:<what they asked>` or
    `pref:<preference_id>` makes it a requested delete; a `job:` reason is judged like any edit.
  - `delete_bullet`: remove whole bullets from the item and change nothing else. Give
    `cut_bullets` (ids like `exp:...#b3`, the index in the item's list on the version you build
    on). Never send a cut as a `revise` with one bullet fewer: a `revise` faces
    every guard and MTLD reverts it. A `delete` or `delete_bullet` with a `user:` or `pref:`
    `because` is judged on the hard gates; a guard regression it causes is accepted and listed
    under `waived`, which you should tell the user.
  - `accept.improves`: the targets a node should raise (`coverage`,
    `relevance_density`, `semantic_coverage`). `semantic_coverage` counts a requirement as
    met when some bullet shows it, in any words; a node that only makes a requirement
    evident can be accepted on it alone. It exists only when ART's Jev check answers: with
    no key it is absent and a node is judged on the other two.
- **skills**: an ordered list of skill names from the graph, with posting terms first.
- Keep each bullet to two rendered lines and the whole page to one.
- **Leave `finalize` out.** Its defaults are ART's calibrated one-page budget: 60 lines,
  plus the skills cap and floor, or the user's page rules when they set any. Don't invent a
  smaller budget; cut content instead when finalize reports `line_budget`.

**Show the user the plan before you execute it.** Run it with `dry_run: true` and show the
user `plan_summary.text` from the result, verbatim: each item, the change, its reason, and the
skills line. Keep the result's `plan_token`. Then **end your turn** and wait for their reply.

## 5. Execute

When the user approves, call `execute_plan({program, plan_token, approval_quote})` with the
same program, the dry run's `plan_token`, and `approval_quote` set to **the user's whole reply**,
copied exactly ("looks good, go ahead"). If they said to skip approval ("just do it, don't
ask"), that reply is the quote. ART checks the quote against the messages the user sent after
the dry run: a short quote must be the whole message, so "ok" taken from "ok, but change the
second bullet" is refused; only a long reply may be quoted in part, by its approving sentence of
20 characters or more. Never write a quote yourself. If they asked for changes, change the
program, dry-run it again and show the new summary: a changed program needs a new token and a
new approval. A refusal with `approval_required` says what is missing. `approval: "token_only"`
means this ART has no prompt hook to check the words: tell the user only the token was checked.
A `patch_plan` commit works the same way: dry-run the patch, show its summary, then commit with
its `plan_token` and the user's words. The first empty plan in step 2 needs neither.

Read the result:

- `nodes[].status`:
  - `accepted` and `kept` went in.
  - `reverted` failed ART's acceptance rule; `reason` says which gate, guard or target.
    A `guard: variant_drift` reason means the bullet moved too far from the variant it
    starts from: use the variant's words, or write it from the raw facts without `from_variant`.
    A `consistency` gate reason lists the numbers, dates or names in your bullet that its
    cited source bullets don't contain. Use only figures and names the evidence states
    (`1,000,000` for `1M` is fine); don't derive new ones ("4x") from other figures.
  - `refused` never ran: an unknown key, an unresolvable cite, a `from_variant` that is
    not an approved variant of that item (`unknown_variant`, `variant_not_approved`,
    `variant_wrong_item`), or a crossed hard preference. It also covers `tombstoned`, which names or cites an item the user
    deleted (don't bring it back), and `negative_pin`, a bullet that mentions a fact the
    user said must never appear, even when cited.
  - Tell the user about anything reverted or refused. Don't silently resubmit the
    same text.
  - A `reverted` reason of `faithfulness` naming `unsupported:` or `contradicts:` means
    an independent check found the bullet claims more than (or the opposite of) the
    evidence it cites. Reword it to what the cited bullets say, or cite better evidence.
    With no Jev answer the reason ends `(fallback check: role: led)` or similar: a plain word
    check found a seniority word, a scope claim (`entire`, `organization`, `end to end`), a
    named method or tool, or a reversed statement that the cited evidence doesn't make. Drop
    that word or claim, or cite the bullet that backs it; don't swap it for a synonym.
  - A `reverted` reason of `preferences` naming `negative_pin:<pin>@<item> :: "<bullet>"`
    means the bullet mentions a topic the user pinned, possibly in other words (a
    paraphrase, or a product, employer or project of that topic), so no pinned word need
    appear. Rewrite it so it no longer refers to the topic, or drop the claim. A `:: company`
    or `:: name` detail means the item's own name refers to it: leave that item out of the plan.
- `nodes[].review` (and `support.review`): bullets that were kept but that the same check
  could not clearly call supported (`label`, `p`). Show them to the user; don't drop them.
  An entry with `source: "fallback"` has `score` and `signals` in place of `p`: the plain word
  check found a soft stretch (`platform`, `designed`, `managed`, `deployed`) the cited evidence
  doesn't use. Show the `signals` as the reason.
- `nodes[].review` entries with `check: "negative_pin"` (also in `negative_pins.review`):
  text that was kept but that Jev thinks may mention a pinned topic (`pin`, `where`, `p`).
  Show them to the user and ask whether the wording is acceptable; don't decide for them.
- `committed: false` with a `preferences` violation `negative_pin:<pin>@<item> :: "<bullet>"`
  and a "revise …" hint: finalize found text that mentions a pinned topic, possibly in other
  words. It checks the whole page, so this can be a bullet you never touched. Revise that
  item's bullet through `patch_plan`, or cut the item. `negative_pins.review` lists the
  whole page's uncertain ones; show them to the user.
- `semantic_coverage` (top level, and `nodes[].semantic` for each node's effect, by requirement
  number from `open_job`): how well the page's bullets show each required or preferred
  requirement, beside the literal `coverage`. The two lists are your planning signal; they
  never decide anything on their own.
  - `semantic_only`: a bullet already shows the requirement, but `missing` terms are not on
    the page. The claim is true and just isn't in the posting's words, so these are the
    `keyword_weave` candidates. Weave a missing term into that bullet only when the cited
    evidence supports the word; if it doesn't, leave the bullet alone.
  - An education entry counts as evidence too: "Bachelor's degree in Computer Science" is
    covered by the degree line. Such an entry is marked `by: education`, and its `missing`
    terms ("bachelor") are not words to weave into a bullet: the degree line already shows
    the requirement, and the page only lacks the posting's wording. A degree marked expected
    shows enrollment, not an earned degree; a finished degree is judged without its date, and
    ART does not compare levels or dates, so a requirement that hinges on either ("graduating
    before June", "Master's required") stays uncovered: read the posting and ask the user.
  - `literal_only`: `present` terms are on the page (a skills line counts) but no bullet shows
    the requirement. That is the stuffing signature. Do not add the term to more bullets.
    Look for real evidence of it (`kg_search`) and revise a bullet around that, or tell the
    user the graph holds none.
  - A requirement in neither list is either shown in the posting's own words (good) or not
    shown at all. Never claim it to fill the gap.
  - Absent (no key, or no required or preferred requirement): plan on `coverage` alone.
- `committed: false` with `violations`: nothing was saved. Fix the plan with
  `patch_plan(program_id, edits)`, which takes JSON-pointer edits to the saved program.
  `cut_hints` say which bullets or projects to cut to fit the page, each with the op that
  carries it out: a `cut_bullet` is a `delete_bullet` (one node per item, all its bullets in
  `cut_bullets`) and a `drop_item` is a `delete`, both with `because: "user:fit the one-page
  budget"`. Add those nodes with `patch_plan`. `stale_parent`
  means the user edited in the meantime: read `get_head`, then replace `/parent`.
- `committed: false` with `reason: "no_change"`: the plan left the page identical to its
  parent (every node reverted, refused or kept), so no version was made. Read `nodes[]` for
  why, then revise the plan or tell the user nothing needed to change. This is not an error.
- `rules_applied`: the job-scoped values ART set, such as a graduation date. Mention
  them.

## 6. Render

- Call `render(job_id)`. It writes `resume.tex` and `resume.pdf` and returns their
  paths, `pages` and `line_budget`. If the user named a place ("save it to my Downloads"),
  pass `save_to`: `downloads`, `desktop`, `documents` or a folder path.
- If the reply says `destination: "ask_user"`, ask the user once where to save their
  resumes, then render again with `save_to` and `remember: true`. With no preference, use
  `save_to: "downloads"`. Later renders save there on their own. If `render` rejects
  `save_to` as an unknown argument, the installed ART predates it: render without it.
- If `pages` > 1, cut using `line_budget.cut_hints` through `patch_plan` (the same
  `delete_bullet` and `delete` nodes as in step 5), then render again. A page over the
  limit is not saved for the user (`not_saved: "over_page_limit"`); the copy is saved once
  it fits, replacing ART's own earlier copy of the job.
- If `pdf_path` is null, give the user the `.tex` path and the `hint`.
- Tell the user where the PDF is: `saved_paths` when it was saved for them, else
  `pdf_path`.
- If they want to edit by hand, give them the editor command to run in a terminal,
  with the job id filled in:

  ```
  uvx --python 3.12 --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.2.0/art_mcp-0.2.0-py3-none-any.whl" artie ui --job <job_id>
  ```

  Their saves come back to you: through the prompt hook if it's installed, and
  otherwise under `editor_edits` in `get_head`. Read `get_head` before building on
  the job again.

## Afterwards

- A bullet the user is happy with can join their library: `promote_bullet(node_id,
  bullet)` makes a **draft**, and only after the user says yes call
  `approve_variant(variant_id)`. Never approve one on your own. If they want a version to
  be the starting point for a kind of role, `save_baseline(node_id, track)` pins it, with
  `track` named after the role family (for example `data_science`).

- To see versions, use `history` and `diff_nodes`. To go back, use `checkout`: show
  the diff and confirm with the user first.
- Remember what the user says they want. See the next section.

## Remembering preferences

A lasting preference is a rule about how every resume (or one job's) should read: "never
list coursework", "always lead with the recommender project". A one-off edit ("shorten
that bullet") and a fact about their experience are not; facts go to `upsert_items`.

- ART's prompt hook runs its memory gate on every message. When it adds a line starting
  "ART: this message may be a standing preference", **ask the user** in your reply whether
  it is a lasting rule, saying how you read it: what it is about, whether to feature it or
  leave it out, how firm, and whether it is for every resume or only this job. Call
  `record_preference` only after they say yes.
  - `strength` is 1 (a passing remark) to 5 (an absolute rule they stated as
    non-negotiable, such as "never"). A 5 becomes a hard gate on every plan, and a 5
    suppression is a negative pin: what it names can never reach the page. Use 5 only for
    what they insisted on. ART never saves a 5 without your confirmation.
  - `target` is a key from `list_items`, `section:<name>` or a topic ("GPA"). It is
    matched exactly: if `suggestions` come back, the target did not resolve; ask or retry
    with a key.
  - `scope` is `global` unless they said it is for one job (`job`, with the job id) or one
    kind of role (`role_family`).
  - A different polarity or strength for the same item and scope replaces the old preference;
    the old one stays on record, superseded.
  - A rule about the page as a whole ("keep it to one page", "no projects section", "skills
    first", "formal tone") is not about an item: it is a document rule. `record_preference`
    stores the plain limits itself; for anything else call `record_rule` with a `kind`
    (`limit`, `section_order`, `style_note`) and `params`. A narrower rule can only tighten a
    hard one: ART refuses it and names the rule, so ask the user before changing the broader
    rule (`retract_rule`).
- When a line says "ART saved a standing preference from this message", tell the user in
  your reply what was saved. If it is wrong, correct it with `record_preference`.
- The hook may not exist on every host. Without it, call `observe` with the user's message
  yourself and follow what it returns (`drop`, `host`, `write`). When the message leans on
  your last reply ("never list that again", "keep it like that", "yes, always"), pass that
  reply as `previous_turn`; ART reads the pair together. The hook does the same from the
  transcript.
- A message like that is easy to misread alone, so when you confirm one, say what you took
  "that" to be.
- `art_pins` returns the pins word for word, for example after your context was trimmed.
  Honour every one.
