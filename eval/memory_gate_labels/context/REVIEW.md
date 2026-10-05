# Memory-gate context labels for review (issue #244)

Each message is a synthetic user message with the synthetic assistant turn it answers, and its label: whether it states a **lasting preference** about the resume, its **direction** (`emphasize`, `suppress`, `format_rule`, none), its **strength** on #129's 1-5 scale (5 only for an absolute rule such as "never"), its **target** (a catalog item, or no item) and whether it is **job-scoped**. **The label assumes the previous turn is known.** The 12 messages from #202 keep the labels you confirmed there, and **you confirmed the 28 new ones as proposed (every proposal kept as is)**; all 40 are user-confirmed, kept for audit. To change a label, correct it in `pairs.json` and re-run `python eval/fit_memory_gate_context.py analyze`. Disagreements with v2 come first.

## Disagreements with v2 (8)

**1. `cx_looker_stop`** (referent_item; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I put Looker at the end of your skills line."
- Message: "Please don't list that, it isn't a real skill of mine."
- Label: suppress skill:looker, strength 4. 'That' is Looker.
- v2 (with the turn): p 0.32; suppress skill:looker (p 0.95); s3; differs on standing
- v1 (alone): p 0.26; suppress no_match (p 0.89); s3
- The turn is sent in production: yes (that, it)
- The gate with v2: host (uncertain)

**2. `cx_past_tense`** (referent_format; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I rewrote the bullets in past tense."
- Message: "Always write them that way."
- Label: format_rule no item, strength 4. 'That way' is past tense; a wording rule with no item.
- v2 (with the turn): p 0.91; format_rule section:experience (p 0.51); s4; differs on target
- v1 (alone): p 0.80; format_rule no_match (p 0.99); s4
- The turn is sent in production: yes (that way, them, that)
- The gate with v2: host (section_target)

**3. `cx_projects_first`** (referent_format; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I moved Projects above Experience."
- Message: "Do the same on every resume."
- Label: emphasize section:projects, strength 4. 'The same' is Projects above Experience: lead with the projects section.
- v2 (with the turn): p 0.92; format_rule section:projects (p 0.61); s4; differs on direction
- v1 (alone): p 0.73; format_rule no_match (p 1.00); s4
- The turn is sent in production: yes (the same)
- The gate with v2: host (section_target)

**4. `cx_three_bullets`** (referent_format; label confirmed in #202, user-confirmed)
- Previous assistant turn: "I trimmed the Rivermount bullets to three."
- Message: "Never go above three per role."
- Label: format_rule section:experience, strength 5. A count rule, absolute.
- v2 (with the turn): p 0.92; format_rule exp:data science intern|rivermount college office of institutional research (p 0.76); s5; differs on target
- v1 (alone): p 0.90; format_rule section:experience (p 0.77); s5
- The turn is sent in production: no (no unresolved reference found)
- The gate with v2: host (hard_preference)

**5. `cx_ballot_keepin`** (negated_referent; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I was going to remove the Ballot Tally project."
- Message: "Don't, keep it in from now on."
- Label: emphasize proj:ballot tally, strength 4. Do not remove it: keep Ballot Tally.
- v2 (with the turn): p 0.84; suppress proj:ballot tally (p 0.93); s4; differs on direction
- v1 (alone): p 0.85; suppress no_match (p 1.00); s4
- The turn is sent in production: yes (it)
- The gate with v2: host (no_target)

**6. `cx_mis_last`** (misleading_previous; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I removed Excel, Looker and Tableau from the skills line."
- Message: "Never remove the last one."
- Label: emphasize skill:tableau, strength 5. The last one mentioned is Tableau.
- v2 (with the turn): p 0.79; emphasize section:skills (p 0.55); s5; differs on target
- v1 (alone): p 0.70; emphasize no_match (p 0.98); s5
- The turn is sent in production: yes (the last one)
- The gate with v2: host (hard_preference)

**7. `cx_ans_no_never`** (short_answer; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "Should I include your GPA on the education line?"
- Message: "No, never."
- Label: suppress no item, strength 5. Never include the GPA; a topic, not a catalog item.
- v2 (with the turn): p 0.75; suppress section:education (p 0.85); s5; differs on target
- v1 (alone): p 0.13; none no_match (p 1.00); s5
- The turn is sent in production: yes (short reply)
- The gate with v2: host (hard_preference)

**8. `cx_gpa_never`** (short_answer; label confirmed in #202, user-confirmed)
- Previous assistant turn: "I can add your GPA to the education line."
- Message: "Don't, ever."
- Label: suppress no item, strength 5. The GPA.
- v2 (with the turn): p 0.77; suppress section:education (p 0.86); s5; differs on target
- v1 (alone): p 0.33; none no_match (p 1.00); s5
- The turn is sent in production: yes (short reply)
- The gate with v2: host (hard_preference)

## Agreements (32)

**9. `cx_excel_never`** (referent_item; label confirmed in #202, user-confirmed)
- Previous assistant turn: "I put Excel at the end of your skills line."
- Message: "Actually never list that again."
- Label: suppress skill:excel, strength 5. 'That' is Excel.
- v2 (with the turn): p 0.84; suppress skill:excel (p 0.96); s5
- v1 (alone): p 0.78; suppress no_match (p 1.00); s5
- The turn is sent in production: yes (again, that)
- The gate with v2: host (hard_preference)

**10. `cx_ballot_drop`** (referent_item; label confirmed in #202, user-confirmed)
- Previous assistant turn: "Here is the Ballot Tally project, it takes four lines."
- Message: "Drop it from now on."
- Label: suppress proj:ballot tally, strength 4. 'It' is the project.
- v2 (with the turn): p 0.59; suppress proj:ballot tally (p 0.90); s4
- v1 (alone): p 0.71; suppress no_match (p 1.00); s4
- The turn is sent in production: yes (it)
- The gate with v2: host (uncertain)

**11. `cx_edu_always`** (referent_format; label confirmed in #202, user-confirmed)
- Previous assistant turn: "I led your resume with the education section."
- Message: "Good, always do it that way."
- Label: emphasize section:education, strength 3. 'That way' is education first.
- v2 (with the turn): p 0.90; emphasize section:education (p 0.84); s4
- v1 (alone): p 0.50; none no_match (p 1.00); s4
- The turn is sent in production: yes (that way, it, that)
- The gate with v2: host (section_target)

**12. `cx_shorten_one`** (one_off; label confirmed in #202, user-confirmed)
- Previous assistant turn: "I reworded the Transit Pulse bullet to be shorter."
- Message: "Shorten that one a bit more."
- Label: not a preference. A one-off edit.
- v2 (with the turn): p 0.04; format_rule proj:transit pulse (p 0.98); s3
- v1 (alone): p 0.04; format_rule no_match (p 0.99); s3
- The turn is sent in production: yes (that one)
- The gate with v2: drop

**13. `cx_one_page`** (short_answer; label confirmed in #202, user-confirmed)
- Previous assistant turn: "Do you want a one-page resume?"
- Message: "Yes, always."
- Label: format_rule no item, strength 4. 'Always' answers the page question.
- v2 (with the turn): p 0.83; format_rule no_match (p 1.00); s4
- v1 (alone): p 0.41; emphasize no_match (p 1.00); s4
- The turn is sent in production: yes (short reply)
- The gate with v2: host (format_rule)

**14. `cx_spark_every`** (short_answer; label confirmed in #202, user-confirmed)
- Previous assistant turn: "Should I mention the Spark work?"
- Message: "Yes, make sure you do on every resume."
- Label: emphasize skill:spark, strength 4. Spark.
- v2 (with the turn): p 0.88; emphasize skill:spark (p 0.95); s4
- v1 (alone): p 0.87; emphasize no_match (p 1.00); s4
- The turn is sent in production: yes (short reply)
- The gate with v2: host (no_target)

**15. `cx_streamlit_keep_off`** (negated_referent; label confirmed in #202, user-confirmed)
- Previous assistant turn: "I removed Streamlit from this version."
- Message: "Good, keep it that way."
- Label: suppress skill:streamlit, strength 3. Keep Streamlit out.
- v2 (with the turn): p 0.65; suppress skill:streamlit (p 0.80); s3
- v1 (alone): p 0.22; none no_match (p 1.00); s3
- The turn is sent in production: yes (that way, it, that)
- The gate with v2: host (no_target)

**16. `cx_no_answer`** (short_answer; label confirmed in #202, user-confirmed)
- Previous assistant turn: "Want me to move the skills section up?"
- Message: "No."
- Label: not a preference. An answer to a question.
- v2 (with the turn): p 0.11; format_rule section:skills (p 0.88); s3
- v1 (alone): p 0.03; none no_match (p 1.00); s4
- The turn is sent in production: yes (short reply)
- The gate with v2: drop

**17. `cx_thanks`** (one_off; label confirmed in #202, user-confirmed)
- Previous assistant turn: "Here is the new summary."
- Message: "Thanks, that works."
- Label: not a preference. Small talk.
- v2 (with the turn): p 0.03; none no_match (p 1.00); s2
- v1 (alone): p 0.02; none no_match (p 1.00); s1
- The turn is sent in production: yes (that)
- The gate with v2: drop

**18. `cx_which_looker`** (one_off; label confirmed in #202, user-confirmed)
- Previous assistant turn: "Your skills line currently includes Looker."
- Message: "Why is that in there?"
- Label: not a preference. A question.
- v2 (with the turn): p 0.04; none skill:looker (p 0.93); s2
- v1 (alone): p 0.03; none no_match (p 1.00); s1
- The turn is sent in production: yes (that)
- The gate with v2: drop

**19. `cx_hadoop_never`** (referent_item; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I added Hadoop to your skills line."
- Message: "Never list that again."
- Label: suppress no item, strength 5. 'That' is Hadoop, which is not on the resume's catalog: a standing suppression with no item to bind.
- v2 (with the turn): p 0.83; suppress no_match (p 0.58); s5
- v1 (alone): p 0.77; suppress no_match (p 1.00); s5
- The turn is sent in production: yes (again, that)
- The gate with v2: host (hard_preference)

**20. `cx_transit_never`** (referent_item; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "Here is the Transit Pulse project, it takes five lines."
- Message: "Never list that again."
- Label: suppress proj:transit pulse, strength 5. 'That' is the Transit Pulse project.
- v2 (with the turn): p 0.74; suppress proj:transit pulse (p 0.89); s5
- v1 (alone): p 0.77; suppress no_match (p 1.00); s5
- The turn is sent in production: yes (again, that)
- The gate with v2: host (hard_preference)

**21. `cx_tableau_front`** (referent_item; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I moved Tableau to the front of your skills line."
- Message: "Always put it there."
- Label: emphasize skill:tableau, strength 4. 'It' is Tableau, 'there' the front of the skills line.
- v2 (with the turn): p 0.91; emphasize skill:tableau (p 0.94); s4
- v1 (alone): p 0.69; emphasize no_match (p 1.00); s4
- The turn is sent in production: yes (it)
- The gate with v2: host (no_target)

**22. `cx_r_drop`** (referent_item; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I removed R from the skills line."
- Message: "Good, leave it out from now on."
- Label: suppress skill:r, strength 4. 'It' is R.
- v2 (with the turn): p 0.95; suppress skill:r (p 0.95); s4
- v1 (alone): p 0.88; suppress no_match (p 1.00); s4
- The turn is sent in production: yes (it)
- The gate with v2: host (no_target)

**23. `cx_one_line_skills`** (referent_format; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I put your skills on a single line."
- Message: "Keep it like that."
- Label: format_rule section:skills, strength 3. 'That' is the skills section on one line.
- v2 (with the turn): p 0.54; format_rule section:skills (p 0.82); s3
- v1 (alone): p 0.36; format_rule no_match (p 1.00); s3
- The turn is sent in production: yes (like that, it, that)
- The gate with v2: host (uncertain)

**24. `cx_no_summary`** (referent_format; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I took the summary paragraph off the top."
- Message: "Never add it back."
- Label: suppress no item, strength 5. 'It' is the summary paragraph, which is not a catalog section.
- v2 (with the turn): p 0.90; suppress no_match (p 0.97); s5
- v1 (alone): p 0.78; suppress no_match (p 1.00); s5
- The turn is sent in production: yes (it)
- The gate with v2: host (hard_preference)

**25. `cx_keep_that_line`** (one_off; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "Here is the new first bullet for Halcyon."
- Message: "Keep that line for now, I'll think about it."
- Label: not a preference. A decision about one bullet, now.
- v2 (with the turn): p 0.11; emphasize no_match (p 0.52); s2
- v1 (alone): p 0.09; emphasize no_match (p 1.00); s2
- The turn is sent in production: yes (that, it)
- The gate with v2: drop

**26. `cx_stop_shorter`** (one_off; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I trimmed the Transit Pulse bullet."
- Message: "Stop, that's too short now, add the numbers back."
- Label: not a preference. A correction to one bullet.
- v2 (with the turn): p 0.06; emphasize proj:transit pulse (p 1.00); s4
- v1 (alone): p 0.08; emphasize no_match (p 0.78); s4
- The turn is sent in production: yes (that)
- The gate with v2: drop

**27. `cx_snowflake_noleaveoff`** (negated_referent; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I left Snowflake off the skills line this time."
- Message: "Don't leave it off again."
- Label: emphasize skill:snowflake, strength 4. Not leaving it off means keeping Snowflake: emphasize, and the negation reads as suppress to a cue.
- v2 (with the turn): p 0.86; emphasize skill:snowflake (p 0.97); s4
- v1 (alone): p 0.62; emphasize no_match (p 1.00); s4
- The turn is sent in production: yes (again, it)
- The gate with v2: host (no_target)

**28. `cx_tableau_nodrop`** (negated_referent; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I dropped Tableau from the skills line."
- Message: "Don't drop that, ever."
- Label: emphasize skill:tableau, strength 5. Never drop Tableau: emphasize at an absolute strength.
- v2 (with the turn): p 0.92; emphasize skill:tableau (p 1.00); s5
- v1 (alone): p 0.51; emphasize no_match (p 0.97); s5
- The turn is sent in production: yes (that)
- The gate with v2: host (hard_preference)

**29. `cx_edu_never_without`** (negated_referent; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I generated your resume without the education section."
- Message: "Never do that again."
- Label: emphasize section:education, strength 5. Never leave the education section out.
- v2 (with the turn): p 0.63; emphasize section:education (p 0.86); s5
- v1 (alone): p 0.27; none no_match (p 1.00); s5
- The turn is sent in production: yes (again, that)
- The gate with v2: host (uncertain)

**30. `cx_mis_first`** (misleading_previous; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "Your skills line now reads Python, SQL, Docker, Git."
- Message: "Always lead with the first one."
- Label: emphasize skill:python, strength 4. The first one is Python.
- v2 (with the turn): p 0.89; emphasize skill:python (p 0.84); s4
- v1 (alone): p 0.74; emphasize no_match (p 0.92); s4
- The turn is sent in production: yes (the first one)
- The gate with v2: host (no_target)

**31. `cx_mis_airflow`** (misleading_previous; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I mentioned Spark, Airflow and dbt in the Halcyon bullets."
- Message: "Never mention Airflow in that role again."
- Label: suppress skill:airflow, strength 5. The message names Airflow; Spark and dbt in the previous turn are not part of it.
- v2 (with the turn): p 0.88; suppress skill:airflow (p 0.85); s5
- v1 (alone): p 0.86; suppress skill:airflow (p 0.91); s5
- The turn is sent in production: yes (again)
- The gate with v2: host (hard_preference)

**32. `cx_mis_second`** (misleading_previous; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I added Pandas and Matplotlib to the skills line."
- Message: "Never list the second one again."
- Label: suppress skill:matplotlib, strength 5. The second one is Matplotlib.
- v2 (with the turn): p 0.88; suppress skill:matplotlib (p 0.96); s5
- v1 (alone): p 0.80; suppress no_match (p 0.92); s5
- The turn is sent in production: yes (again, the second one)
- The gate with v2: host (hard_preference)

**33. `cx_mis_ranked`** (misleading_previous; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I ranked Python, R and SQL as your top three skills."
- Message: "Never put the middle one in the top three."
- Label: suppress skill:r, strength 5. The middle one is R.
- v2 (with the turn): p 0.75; suppress skill:r (p 0.85); s5
- v1 (alone): p 0.76; suppress no_match (p 0.92); s5
- The turn is sent in production: yes (the middle one)
- The gate with v2: host (hard_preference)

**34. `cx_irr_pdf`** (irrelevant_previous; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "The PDF compiled cleanly to one page."
- Message: "Never mention Excel on it."
- Label: suppress skill:excel, strength 5. 'It' is the resume; the compile message has no bearing.
- v2 (with the turn): p 0.84; suppress skill:excel (p 0.83); s5
- v1 (alone): p 0.83; suppress skill:excel (p 0.97); s5
- The turn is sent in production: no (no unresolved reference found)
- The gate with v2: host (hard_preference)

**35. `cx_irr_cover`** (irrelevant_previous; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "Here is the cover-letter draft you asked for."
- Message: "Always keep it to one page."
- Label: format_rule no item, strength 4. 'It' is the resume, not the cover letter: a length rule.
- v2 (with the turn): p 0.50; format_rule no_match (p 1.00); s4
- v1 (alone): p 0.88; format_rule no_match (p 1.00); s4
- The turn is sent in production: yes (it)
- The gate with v2: host (uncertain)

**36. `cx_irr_that`** (irrelevant_previous; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "The render finished in two seconds."
- Message: "Never list that again."
- Label: suppress no item, strength 5. Nothing in the previous turn is listable: a standing suppression with no item to bind.
- v2 (with the turn): p 0.61; suppress no_match (p 1.00); s5
- v1 (alone): p 0.77; suppress no_match (p 1.00); s5
- The turn is sent in production: yes (again, that)
- The gate with v2: host (uncertain)

**37. `cx_irr_ok`** (irrelevant_previous; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I've saved the job description."
- Message: "Yes, always."
- Label: not a preference. Nothing was asked, so there is nothing to confirm: not a preference to record.
- v2 (with the turn): p 0.37; none no_match (p 0.94); s4
- v1 (alone): p 0.41; emphasize no_match (p 1.00); s4
- The turn is sent in production: yes (short reply)
- The gate with v2: host (uncertain)

**38. `cx_irr_spark`** (irrelevant_previous; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "I added Excel to your skills line."
- Message: "Never mention Spark again."
- Label: suppress skill:spark, strength 5. The message names Spark; Excel in the previous turn is not what it is about.
- v2 (with the turn): p 0.91; suppress skill:spark (p 1.00); s5
- v1 (alone): p 0.75; suppress skill:spark (p 0.99); s5
- The turn is sent in production: yes (again)
- The gate with v2: host (hard_preference)

**39. `cx_ans_docker`** (short_answer; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "Do you want Docker mentioned in the Halcyon bullets?"
- Message: "Yes, always."
- Label: emphasize skill:docker, strength 4. 'Always' answers the Docker question.
- v2 (with the turn): p 0.89; emphasize skill:docker (p 0.96); s4
- v1 (alone): p 0.41; emphasize no_match (p 1.00); s4
- The turn is sent in production: yes (short reply)
- The gate with v2: host (no_target)

**40. `cx_ans_skills_up`** (short_answer; label confirmed in review of this issue, user-confirmed)
- Previous assistant turn: "Should I put the skills section above Experience?"
- Message: "Yes, always."
- Label: emphasize section:skills, strength 4. 'Always' answers the skills-section question.
- v2 (with the turn): p 0.91; emphasize section:skills (p 0.93); s4
- v1 (alone): p 0.41; emphasize no_match (p 1.00); s4
- The turn is sent in production: yes (short reply)
- The gate with v2: host (section_target)

