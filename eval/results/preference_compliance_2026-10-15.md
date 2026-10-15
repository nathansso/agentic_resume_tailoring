# Preference compliance, 2026-10-15

5 profiles x 6 jobs x 15 seeded sets, each on a naive and an obedient host (1020 runs). Page count: compiled page count (rule_pages only).

| Metric | Result | Target |
|---|---|---|
| Naive host: hard violations rendered | 0 of 400 in-scope runs (306 armed traps) | 0 |
| Naive host: scope leak | 0 of 50 out-of-scope runs | 0 |
| Obedient host: hard violations rendered | 0 | 0 |
| Obedient host: scope leak | 0 of 50 | 0 |
| Obedient host: over-block rate | 0.00% (0 of 18462 units) | <= 2% |
| Obedient host: projects swapped out to make room for one the user asked to keep (not counted above: `replace` is the only way to add one) | 39 | report |
| Obedient host: refusals for a preference it was told about | 0 | 0 |
| Obedient host: runs that did not commit although the control did | 0 | 0 |
| Durability (obedient, after a supersede) | 60 of 60 | all |
| Durability (naive: stopped or compliant) | 60 of 60 | all |
| Soft preferences applied (obedient, reported) | 66 of 66 | report |

## By set

| Set | Group | Naive outcomes | Obedient outcomes |
|---|---|---|---|
| suppress_exp | item | refused 30 (armed 30) | held 30 |
| suppress_proj | item | held 2, refused 28 (armed 28) | held 30 |
| suppress_skill | item | refused 30 (armed 30) | held 30 |
| pending_ban | pending | held 30 (armed 30) | held 30 |
| family_pin | scope | held 25, refused 5 (armed 5) | held 30 |
| job_pin | scope | held 25, refused 5 (armed 5) | held 30 |
| soft | soft | held 30 (armed 0) | held 30 |
| rule_pages | rule | held 30 (armed 0) | held 30 |
| rule_exclude | rule | held 30 (armed 30) | held 30 |
| rule_order | rule | held 30 (armed 30) | held 30 |
| rule_bullets | rule | refused 30 (armed 30) | held 30 |
| rule_skills | rule | refused 30 (armed 30) | held 30 |
| rule_narrower | rule | refused 30 (armed 30) | held 30 |
| supersede_allow | durability | held 30, refused 30 (armed 30) | held 60 |
| supersede_forbid | durability | held 30, refused 28, unchanged 2 (armed 28) | held 60 |
