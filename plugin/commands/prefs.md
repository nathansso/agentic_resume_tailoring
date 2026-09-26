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

Preferences can't be recorded from here yet; ART's memory gate is coming. If I state
a new standing preference, follow it for this session and say so. For a job-scoped
rule, record it with `upsert_items` (kind `rule`), as the `art-setup` skill describes.
