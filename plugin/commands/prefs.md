---
description: Show the preferences and pinned rules ART applies to your resumes
argument-hint: [role family, e.g. data_science]
---

Show me my ART preferences. Call `art_briefing`, passing the role family `$ARGUMENTS`
if one is given, and list:

1. **Pins**: the rules applied to every resume, verbatim.
2. **Preferences**: grouped by scope (global, or a role family), each with its
   polarity (emphasize or suppress) and strength.
3. **Job-scoped rules**: each question, the field it sets, and its values.

If I state a new standing preference, confirm it with me and record it with
`record_preference`, as the `art-tailor` skill describes. For a posting-dependent fact
(such as a graduation date), record a job-scoped rule with `upsert_items` (kind `rule`),
as the `art-setup` skill describes. Call `art_pins` if you need the pins on their own.
