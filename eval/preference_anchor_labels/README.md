# Preference anchor labels (#296)

A hand-labelled set that measures `harness/anchors.py`: given a subject a user's preference is about
("Kafka", "torch", "one page"), does the resolver bind it to the right item, or leave it pending, or
call it a document subject, or refuse it as several?

`pairs.json` holds the rows. Each is `{id, profile, subject, phrasing, category, expected}`:

- `profile` is a stem under `eval/profiles/` (five synthetic profiles; never `personal/`).
- `subject` is the thing the `phrasing` is about, as a host would pass it to `record_preference`.
- `expected` is **what the user means**, labelled by hand before the resolver was run: an item key
  (`skill:kafka`, `exp:title|company`, `proj:`, `edu:`, `ach:`), or `ambiguous`, `pending`,
  `document`, `multiple`.
- `add` (optional) overlays items the profile lacks: a second role with one title, a longer name that
  contains a shorter one. `candidates` lists the keys an `ambiguous` row must name. `note` says why a
  label is debatable or a row is expected to miss.

## Run

```bash
python -m pytest tests/test_preference_anchor_labels.py -q -s
```

`-s` prints the per-category table. Nothing here calls a model or a store.

## What the numbers mean

| Number | Status | Meaning |
|---|---|---|
| wrong-node bindings | **hard gate, must be 0** | the resolver bound a key other than the one expected, or bound when the user meant no single item. A wrong binding puts a ban or a pin on the wrong item. |
| alias recall (`alias` rows) | **gate, at least 0.9** | spellings `SKILL_ALIASES` / `skill_forms` know (`torch`, `sklearn`, `postgresql`). |
| per-category accuracy | reported | `correct / rows` per category. A miss is a pending term, never a wrong item. |
| document precision, recall | reported | of the rows the resolver called `document`, the share that are; of the rows labelled `document`, the share it found. |

## Categories

`exact_key`, `label`, `bare_name`, `role`, `company_alone` (expected `pending`, #295), `ambiguous`,
`alias`, `absent`, `short_decoy` (R vs React, C vs C#, Go vs Google), `substring_decoy` (Kafka vs Kafka
Streams), `section`, `style`, `scope` (the subject is the thing, not "for ML roles"), `negation`,
`multiple`, `and_in_name` ("Research and Development"), `doc_word_decoy` (a name holding a document
word that is not about the page: "Page Rank", "Style Transfer").

Three categories record misses on purpose, as findings and not gates:

- `alias_candidate`: what a user writes that the alias map does not know (`k8s`, `js`, `pg`, `tf`).
  They stay `pending`. Adding them to `SKILL_ALIASES` changes how every ingested skill is normalized,
  so it is a separate decision.
- `role_at_phrase`: a role written "Title at Company" or "Title, Company".
- `alias_overreach`: `torchvision`, which the alias map folds into PyTorch. It must stay pending: a
  ban on torchvision is not a ban on PyTorch.

## Extending it

Add rows by hand and label them from what the user means. Do not relabel a row to match the resolver:
a row it gets wrong is the finding. Keep each category above its floor in `CATEGORIES` in the test.
