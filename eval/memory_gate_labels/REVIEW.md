# Memory-gate labels for review (issue #202)

Each message is a synthetic user message and its user-confirmed label: whether it states a **lasting preference** about the resume (global, or for one named job), its **direction** (`emphasize`: feature or keep something; `suppress`: leave something out; `format_rule`: how the resume is written or laid out; none), its **strength** on #129's 1-5 scale (5 only for an absolute rule such as "never"), its **target** (a catalog item, or no item for a topic or a general rule) and whether it is **job-scoped**. A one-off edit, a question, a fact about the user's experience and small talk are not preferences. **These are the labels the user confirmed (every proposal kept as is), kept for audit.** To change one, correct it in `pairs.json` and re-run `python eval/fit_memory_gate_threshold.py analyze` to refit. Disagreements with Jev come first.

## Disagreements with Jev (18)

**1. `ep_ballot_over`** (explicit_positive)
- Message: "I'd prefer to feature the Ballot Tally pipeline over my other project."
- Label: emphasize proj:ballot tally, strength 3. A plain preference ("I'd prefer"), no insistence.
- Jev: p 0.48; emphasize proj:ballot tally (p 1.00); strength 3; differs on standing
- Prefilter: candidate, cues ['prefer']
- The gate: write

**2. `en_coursework`** (explicit_negative)
- Message: "Never include coursework projects."
- Label: suppress no item, strength 5. A topic, not a listed item; 'never' is absolute.
- Jev: p 0.93; suppress section:projects (p 0.63); strength 5; differs on target
- Prefilter: candidate, negated, cues ['never']
- The gate: host (hard_preference)

**3. `en_gpa`** (explicit_negative)
- Message: "Leave my GPA off every resume."
- Label: suppress no item, strength 4. No GPA item in the catalog: a topic, so no target.
- Jev: p 0.96; suppress section:education (p 0.92); strength 4; differs on target
- Prefilter: candidate, negated, cues ['every resume', 'leave out']
- The gate: host (hard_preference)

**4. `im_abtest_card`** (implicit)
- Message: "Hiring managers love A/B testing, I think that's my best card."
- Label: emphasize skill:a/b testing, strength 2. Implies featuring A/B testing.
- Jev: p 0.23; emphasize skill:a/b testing (p 0.99); strength 2; differs on standing
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**5. `im_ballot_weekend`** (implicit)
- Message: "Ballot Tally was a weekend thing, it doesn't really represent me."
- Label: suppress proj:ballot tally, strength 3. Implies they do not want the project shown.
- Jev: p 0.21; suppress proj:ballot tally (p 1.00); strength 3; differs on standing
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**6. `im_edu_top`** (implicit)
- Message: "I think the education section deserves the top spot."
- Label: emphasize section:education, strength 3. Implies leading with education.
- Jev: p 0.31; emphasize section:education (p 1.00); strength 2; differs on standing
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**7. `im_excel_junior`** (implicit)
- Message: "The Excel mention makes me look junior, honestly."
- Label: suppress skill:excel, strength 3. Implies they want Excel out; no directive.
- Jev: p 0.11; suppress skill:excel (p 1.00); strength 2; differs on standing
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**8. `im_halcyon_proud`** (implicit)
- Message: "My Halcyon role is the thing I'm proudest of."
- Label: emphasize exp:associate data scientist|halcyon health analytics, strength 2. Implies featuring the role; contestable (could be a fact).
- Jev: p 0.07; emphasize exp:associate data scientist|halcyon health analytics (p 0.97); strength 3; differs on standing
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**9. `im_looker_padding`** (implicit)
- Message: "Looker feels like padding on my skills list."
- Label: suppress skill:looker, strength 2. Implies they want it out.
- Jev: p 0.10; suppress skill:looker (p 1.00); strength 2; differs on standing
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**10. `im_r_dead`** (implicit)
- Message: "R is basically dead to me at this point."
- Label: suppress skill:r, strength 3. Implies they do not want R shown.
- Jev: p 0.20; none skill:r (p 0.98); strength 4; differs on standing, direction
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**11. `im_skills_long`** (implicit)
- Message: "I'm not a fan of how many tools my skills section lists."
- Label: format_rule section:skills, strength 2. About the section's length, not one item.
- Jev: p 0.37; suppress section:skills (p 1.00); strength 3; differs on standing, direction
- Prefilter: candidate, negated, cues ['i like']
- The gate: host (section_target)

**12. `fr_no_first_person`** (format_rule)
- Message: "Don't use first-person pronouns anywhere on the resume."
- Label: format_rule no item, strength 4. A wording rule phrased negatively.
- Jev: p 0.88; suppress no_match (p 1.00); strength 4; differs on direction
- Prefilter: candidate, negated, cues ["don't"]
- The gate: host (hard_preference)

**13. `fr_past_tense`** (format_rule)
- Message: "Use past tense for every bullet, even in my current role."
- Label: format_rule no item, strength 4. A wording rule.
- Jev: p 0.93; format_rule section:experience (p 0.87); strength 4; differs on target
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**14. `js_aws`** (job_scoped)
- Message: "For this posting I want AWS front and center."
- Label: emphasize skill:aws, strength 4, job-scoped. Job-scoped emphasis.
- Jev: p 0.49; emphasize skill:aws (p 1.00); strength 4; differs on standing
- Prefilter: candidate, cues ['i want', 'for this job']
- The gate: write

**15. `js_edu_first`** (job_scoped)
- Message: "On this resume, keep the education section first."
- Label: format_rule section:education, strength 3, job-scoped. Job-scoped layout.
- Jev: p 0.63; emphasize section:education (p 1.00); strength 3; differs on direction
- Prefilter: candidate, cues ['keep', 'for this job']
- The gate: host (section_target)

**16. `js_halcyon`** (job_scoped)
- Message: "For this specific application, highlight the forecasting work at Halcyon."
- Label: emphasize exp:associate data scientist|halcyon health analytics, strength 3, job-scoped. Job-scoped emphasis.
- Jev: p 0.45; emphasize exp:associate data scientist|halcyon health analytics (p 0.97); strength 3; differs on standing
- Prefilter: candidate, cues ['lead with', 'for this job']
- The gate: write

**17. `js_rivermount`** (job_scoped)
- Message: "Skip the Rivermount internship for this one."
- Label: suppress exp:data science intern|rivermount college office of institutional research, strength 4, job-scoped. Job-scoped suppression.
- Jev: p 0.31; suppress exp:data science intern|rivermount college office of institutional research (p 1.00); strength 3; differs on standing
- Prefilter: candidate, negated, cues ['omit', 'for this job']
- The gate: write

**18. `js_skills_above`** (job_scoped)
- Message: "Only for this job: put my skills section above my projects."
- Label: format_rule section:skills, strength 3, job-scoped. Job-scoped layout.
- Jev: p 0.47; format_rule section:skills (p 0.96); strength 4; differs on standing
- Prefilter: candidate, cues ['for this job']
- The gate: host (section_target)

## Agreements (93)

**19. `ep_transit_first`** (explicit_positive)
- Message: "Always lead with the Transit Pulse project when you list my projects."
- Label: emphasize proj:transit pulse, strength 4. 'Always' is a firm, standing rule to feature one project.
- Jev: p 0.95; emphasize proj:transit pulse (p 0.99); strength 4
- Prefilter: candidate, cues ['always', 'lead with']
- The gate: write

**20. `ep_halcyon_first`** (explicit_positive)
- Message: "From now on, put my Halcyon Health Analytics role first in the experience section."
- Label: emphasize exp:associate data scientist|halcyon health analytics, strength 4. 'From now on' makes it standing; it asks to lead with one role.
- Jev: p 0.97; emphasize exp:associate data scientist|halcyon health analytics (p 0.86); strength 4
- Prefilter: candidate, cues ['from now on']
- The gate: write

**21. `ep_python_first`** (explicit_positive)
- Message: "I want Python listed first in my skills section on every resume."
- Label: emphasize skill:python, strength 4. Standing ('every resume'), firm ('I want').
- Jev: p 0.98; emphasize skill:python (p 0.97); strength 4
- Prefilter: candidate, cues ['i want', 'every resume']
- The gate: write

**22. `ep_tableau_keep`** (explicit_positive)
- Message: "Please keep Tableau on all my resumes, it's the tool I'm proudest of."
- Label: emphasize skill:tableau, strength 4. Keep a skill on all resumes.
- Jev: p 0.96; emphasize skill:tableau (p 1.00); strength 4
- Prefilter: candidate, cues ['keep']
- The gate: write

**23. `ep_abtest_top`** (explicit_positive)
- Message: "Make sure A/B testing always shows up near the top of my skills."
- Label: emphasize skill:a/b testing, strength 4. 'Make sure ... always'.
- Jev: p 0.94; emphasize skill:a/b testing (p 1.00); strength 4
- Prefilter: candidate, cues ['always', 'make sure']
- The gate: write

**24. `ep_dbt_fits`** (explicit_positive)
- Message: "I like highlighting my dbt work whenever it fits."
- Label: emphasize skill:dbt, strength 2. Hedged ('whenever it fits'): a mild preference.
- Jev: p 0.72; emphasize skill:dbt (p 0.99); strength 2
- Prefilter: candidate, cues ['lead with', 'i like']
- The gate: write

**25. `ep_rivermount_room`** (explicit_positive)
- Message: "Going forward, give the Rivermount internship more room, it shows my research side."
- Label: emphasize exp:data science intern|rivermount college office of institutional research, strength 3. 'Going forward' makes it standing.
- Jev: p 0.93; emphasize exp:data science intern|rivermount college office of institutional research (p 0.99); strength 3
- Prefilter: candidate, cues ['from now on']
- The gate: write

**26. `ep_edu_first`** (explicit_positive)
- Message: "I'd rather you led with my education section since I'm a recent grad."
- Label: emphasize section:education, strength 3. A plain preference to lead with a section.
- Jev: p 0.63; emphasize section:education (p 1.00); strength 3
- Prefilter: candidate, cues ["i'd rather"]
- The gate: host (section_target)

**27. `ep_snowflake`** (explicit_positive)
- Message: "Always include the Snowflake work, it's central to how I describe myself."
- Label: emphasize skill:snowflake, strength 4. 'Always include'.
- Jev: p 0.97; emphasize skill:snowflake (p 1.00); strength 4
- Prefilter: candidate, cues ['always']
- The gate: write

**28. `ep_airflow_every`** (explicit_positive)
- Message: "It would be great if my Airflow experience got a spot in every version."
- Label: emphasize skill:airflow, strength 2. A soft wish, but about every version.
- Jev: p 0.94; emphasize skill:airflow (p 0.89); strength 3
- Prefilter: candidate, cues ['every resume']
- The gate: write

**29. `en_excel`** (explicit_negative)
- Message: "Never mention Excel on my resume."
- Label: suppress skill:excel, strength 5. 'Never' is the absolute case: a negative pin.
- Jev: p 0.92; suppress skill:excel (p 1.00); strength 5
- Prefilter: candidate, negated, cues ['never']
- The gate: host (hard_preference)

**30. `en_looker`** (explicit_negative)
- Message: "Don't ever list Looker, I barely used it."
- Label: suppress skill:looker, strength 5. 'Don't ever' is absolute.
- Jev: p 0.90; suppress skill:looker (p 1.00); strength 5
- Prefilter: candidate, negated, cues ["don't"]
- The gate: host (hard_preference)

**31. `en_ballot_off`** (explicit_negative)
- Message: "Leave the Ballot Tally project off my resume from now on."
- Label: suppress proj:ballot tally, strength 4. 'From now on', a firm instruction to drop a project.
- Jev: p 0.97; suppress proj:ballot tally (p 1.00); strength 4
- Prefilter: candidate, negated, cues ['from now on', 'leave out']
- The gate: write

**32. `en_streamlit`** (explicit_negative)
- Message: "Please avoid putting Streamlit in my skills."
- Label: suppress skill:streamlit, strength 3. 'Avoid' is a clear but softer ask.
- Jev: p 0.81; suppress skill:streamlit (p 1.00); strength 3
- Prefilter: candidate, negated, cues ['avoid']
- The gate: write

**33. `en_rivermount`** (explicit_negative)
- Message: "I'd rather not have the Rivermount internship on my resume."
- Label: suppress exp:data science intern|rivermount college office of institutional research, strength 3. A plain preference to omit a role.
- Jev: p 0.50; suppress exp:data science intern|rivermount college office of institutional research (p 1.00); strength 3
- Prefilter: candidate, negated, cues ["i'd rather"]
- The gate: write

**34. `en_scipy`** (explicit_negative)
- Message: "Stop including SciPy in the skills section."
- Label: suppress skill:scipy, strength 4. A firm instruction about a standing behaviour.
- Jev: p 0.81; suppress skill:scipy (p 0.99); strength 4
- Prefilter: candidate, negated, cues ['stop']
- The gate: write

**35. `en_seaborn`** (explicit_negative)
- Message: "Under no circumstances should my Seaborn experience be mentioned."
- Label: suppress skill:seaborn, strength 5. 'Under no circumstances' is the scale's own example of 5.
- Jev: p 0.86; suppress skill:seaborn (p 0.85); strength 5
- Prefilter: candidate, negated, cues ['should']
- The gate: host (hard_preference)

**36. `en_achievements`** (explicit_negative)
- Message: "Skip the achievements section on all my resumes."
- Label: suppress section:achievements, strength 4. Leave out a section everywhere.
- Jev: p 0.97; suppress section:achievements (p 1.00); strength 4
- Prefilter: candidate, negated, cues ['omit']
- The gate: host (section_target)

**37. `en_r`** (explicit_negative)
- Message: "I don't want R on my resume; I haven't used it in years."
- Label: suppress skill:r, strength 4. Firm: 'I don't want'.
- Jev: p 0.78; suppress skill:r (p 1.00); strength 3
- Prefilter: candidate, negated, cues ["don't"]
- The gate: write

**38. `dn_docker_stop`** (double_negation)
- Message: "Don't stop mentioning Docker, I want it on every resume."
- Label: emphasize skill:docker, strength 4. Double negation: 'don't stop mentioning' means keep it.
- Jev: p 0.95; emphasize skill:docker (p 1.00); strength 4
- Prefilter: candidate, negated, cues ["don't", 'stop', 'i want', 'every resume']
- The gate: host (negation_disagrees)

**39. `dn_postgres_leave`** (double_negation)
- Message: "I don't want you to leave out Postgres."
- Label: emphasize skill:postgres, strength 4. Embedded negation: do not leave out means include.
- Jev: p 0.61; emphasize skill:postgres (p 1.00); strength 4
- Prefilter: candidate, negated, cues ["don't", 'leave out']
- The gate: host (negation_disagrees)

**40. `dn_transit_never_drop`** (double_negation)
- Message: "Please never drop the Transit Pulse project from my resumes."
- Label: emphasize proj:transit pulse, strength 5. 'Never drop' is an absolute rule to keep it.
- Jev: p 0.97; emphasize proj:transit pulse (p 1.00); strength 5
- Prefilter: candidate, negated, cues ['never']
- The gate: host (hard_preference)

**41. `dn_spark_remove`** (double_negation)
- Message: "Don't ever remove Spark from my skills line."
- Label: emphasize skill:spark, strength 5. 'Don't ever remove' is an absolute keep.
- Jev: p 0.96; emphasize skill:spark (p 1.00); strength 5
- Prefilter: candidate, negated, cues ["don't"]
- The gate: host (hard_preference)

**42. `dn_git_off`** (double_negation)
- Message: "I can't stand it when Git gets left off, so don't do that."
- Label: emphasize skill:git, strength 4. Embedded negation: never leave Git off means include it.
- Jev: p 0.85; emphasize skill:git (p 1.00); strength 4
- Prefilter: candidate, negated, cues ["don't"]
- The gate: host (hard_preference)

**43. `dn_bigquery_reason`** (double_negation)
- Message: "There's no reason to avoid mentioning BigQuery, so keep it in."
- Label: emphasize skill:bigquery, strength 3. Negated 'avoid' with 'keep it in': include it.
- Jev: p 0.50; emphasize skill:bigquery (p 1.00); strength 4
- Prefilter: candidate, negated, cues ['avoid', 'keep']
- The gate: host (negation_disagrees)

**44. `dn_dbt_never_leave`** (double_negation)
- Message: "Never leave out dbt."
- Label: emphasize skill:dbt, strength 5. 'Never leave out' is an absolute keep.
- Jev: p 0.86; emphasize skill:dbt (p 1.00); strength 5
- Prefilter: candidate, negated, cues ['never', 'leave out']
- The gate: host (hard_preference)

**45. `dn_sql_omit`** (double_negation)
- Message: "I don't mind SQL being listed, in fact please don't omit it."
- Label: emphasize skill:sql, strength 3. 'Don't omit' means include.
- Jev: p 0.69; emphasize skill:sql (p 1.00); strength 4
- Prefilter: candidate, negated, cues ["don't", 'omit']
- The gate: host (negation_disagrees)

**46. `dn_halcyon_hide`** (double_negation)
- Message: "Don't hide the Halcyon role even when space is tight."
- Label: emphasize exp:associate data scientist|halcyon health analytics, strength 4. Do not hide means keep it.
- Jev: p 0.79; emphasize exp:associate data scientist|halcyon health analytics (p 0.99); strength 4
- Prefilter: candidate, negated, cues ["don't"]
- The gate: host (negation_disagrees)

**47. `dn_matplotlib_keep`** (double_negation)
- Message: "Don't keep Matplotlib in the skills list."
- Label: suppress skill:matplotlib, strength 4. Negation on a 'keep' verb: leave it out.
- Jev: p 0.68; suppress skill:matplotlib (p 1.00); strength 3
- Prefilter: candidate, negated, cues ["don't", 'keep']
- The gate: write

**48. `dn_education_skip`** (double_negation)
- Message: "You shouldn't skip the education section, ever."
- Label: emphasize section:education, strength 5. 'Shouldn't skip ... ever': keep it, absolutely.
- Jev: p 0.92; emphasize section:education (p 1.00); strength 5
- Prefilter: candidate, negated, cues ['omit', 'should']
- The gate: host (hard_preference)

**49. `dn_pandas_not`** (double_negation)
- Message: "Not mentioning pandas would be a mistake, so always include it."
- Label: emphasize skill:pandas, strength 4. Negated gerund framed as a mistake: include it.
- Jev: p 0.94; emphasize skill:pandas (p 1.00); strength 4
- Prefilter: candidate, negated, cues ['always']
- The gate: host (negation_disagrees)

**50. `im_transit_notice`** (implicit)
- Message: "Transit Pulse is really what I want recruiters to notice."
- Label: emphasize proj:transit pulse, strength 3. Implies featuring the project.
- Jev: p 0.63; emphasize proj:transit pulse (p 1.00); strength 3
- Prefilter: candidate, cues ['i want']
- The gate: write

**51. `im_streamlit_known`** (implicit)
- Message: "Streamlit isn't something I want to be known for."
- Label: suppress skill:streamlit, strength 3. Implies leaving it out.
- Jev: p 0.56; suppress skill:streamlit (p 1.00); strength 3
- Prefilter: candidate, negated, cues ['i want']
- The gate: write

**52. `im_forecast_first`** (implicit)
- Message: "I'd love for people to see my forecasting work first."
- Label: emphasize no item, strength 3. Forecasting is a topic, not a listed item.
- Jev: p 0.57; emphasize no_match (p 0.59); strength 3
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**53. `fr_one_line`** (format_rule)
- Message: "Keep every bullet to one line."
- Label: format_rule no item, strength 4. A layout rule for bullets.
- Jev: p 0.73; format_rule no_match (p 0.90); strength 4
- Prefilter: candidate, cues ['keep']
- The gate: host (format_rule)

**54. `fr_one_page`** (format_rule)
- Message: "I prefer a one-page resume, always."
- Label: format_rule no item, strength 4. A length rule.
- Jev: p 0.98; format_rule no_match (p 1.00); strength 4
- Prefilter: candidate, cues ['always', 'prefer']
- The gate: host (format_rule)

**55. `fr_skills_bottom`** (format_rule)
- Message: "Put the skills section at the bottom of every resume."
- Label: format_rule section:skills, strength 4. A layout rule about one section.
- Jev: p 0.95; format_rule section:skills (p 1.00); strength 4
- Prefilter: candidate, cues ['every resume']
- The gate: host (section_target)

**56. `fr_concise`** (format_rule)
- Message: "I prefer concise bullet points over long paragraphs."
- Label: format_rule no item, strength 3. Seeded from tests/memory_evals/user_preference_recall.yaml.
- Jev: p 0.74; format_rule no_match (p 0.99); strength 3
- Prefilter: candidate, cues ['prefer']
- The gate: host (format_rule)

**57. `fr_edu_above`** (format_rule)
- Message: "Keep the education section above experience, please."
- Label: format_rule section:education, strength 3. Section order.
- Jev: p 0.55; format_rule section:education (p 0.98); strength 3
- Prefilter: candidate, cues ['keep']
- The gate: host (section_target)

**58. `fr_four_bullets`** (format_rule)
- Message: "No more than four bullets per role from now on."
- Label: format_rule section:experience, strength 4. A count rule on roles.
- Jev: p 0.96; format_rule section:experience (p 0.82); strength 4
- Prefilter: candidate, negated, cues ['from now on', 'omit']
- The gate: host (section_target)

**59. `fr_dates`** (format_rule)
- Message: "Make sure dates are written like Jan 2025, not 01/2025."
- Label: format_rule no item, strength 4. A date format rule.
- Jev: p 0.82; format_rule no_match (p 0.94); strength 4
- Prefilter: candidate, negated, cues ['make sure']
- The gate: host (format_rule)

**60. `fr_acronyms`** (format_rule)
- Message: "Spell out acronyms the first time they appear."
- Label: format_rule no item, strength 3. A wording rule with no cue word.
- Jev: p 0.80; format_rule no_match (p 0.99); strength 3
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**61. `fr_numerals`** (format_rule)
- Message: "Always use numerals for numbers, not words."
- Label: format_rule no item, strength 4. A wording rule.
- Jev: p 0.89; format_rule no_match (p 1.00); strength 4
- Prefilter: candidate, negated, cues ['always']
- The gate: host (format_rule)

**62. `oo_shorten`** (one_off_edit)
- Message: "Shorten the second Halcyon bullet so it fits on one line."
- Label: not a preference. A request about one bullet now.
- Jev: p 0.03; format_rule exp:associate data scientist|halcyon health analytics (p 0.98); strength 3
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**63. `oo_reword`** (one_off_edit)
- Message: "Can you reword the Transit Pulse bullet to mention FastAPI?"
- Label: not a preference. A request about one bullet now.
- Jev: p 0.04; emphasize proj:transit pulse (p 0.94); strength 3
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**64. `oo_punchier`** (one_off_edit)
- Message: "Please make the summary a little punchier."
- Label: not a preference. A one-time rewrite.
- Jev: p 0.08; format_rule no_match (p 0.98); strength 2
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**65. `oo_swap`** (one_off_edit)
- Message: "Swap the order of my two projects for this draft."
- Label: not a preference. 'For this draft' is this edit only, not a rule.
- Jev: p 0.05; format_rule section:projects (p 0.95); strength 3
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**66. `oo_move_tableau`** (one_off_edit)
- Message: "Move Tableau up to the front of the skills line."
- Label: not a preference. One edit, no standing rule.
- Jev: p 0.18; emphasize skill:tableau (p 1.00); strength 3
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**67. `oo_typo`** (one_off_edit)
- Message: "Fix the typo in the Rivermount bullet about the retention model."
- Label: not a preference. A one-off fix.
- Jev: p 0.03; format_rule exp:data science intern|rivermount college office of institutional research (p 0.97); strength 3
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**68. `oo_add`** (one_off_edit)
- Message: "Add the 40-table migration to the intern section."
- Label: not a preference. A one-off addition.
- Jev: p 0.07; emphasize exp:data science intern|rivermount college office of institutional research (p 0.59); strength 3
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**69. `oo_leveraged`** (one_off_edit)
- Message: "Don't use the word leveraged in that last bullet."
- Label: not a preference. Negated, but about one bullet now.
- Jev: p 0.13; suppress no_match (p 0.83); strength 3
- Prefilter: candidate, negated, cues ["don't"]
- The gate: host (uncertain)

**70. `oo_remove_second`** (one_off_edit)
- Message: "Remove the second bullet under Ballot Tally, it's too long."
- Label: not a preference. A one-off removal.
- Jev: p 0.04; suppress proj:ballot tally (p 1.00); strength 3
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**71. `oo_stop_long`** (one_off_edit)
- Message: "Stop, that's too long, try again."
- Label: not a preference. A reaction to one draft.
- Jev: p 0.07; format_rule no_match (p 1.00); strength 4
- Prefilter: candidate, negated, cues ['stop']
- The gate: drop

**72. `oo_keep_first`** (one_off_edit)
- Message: "Keep the first bullet as is and tighten the rest."
- Label: not a preference. An instruction for this edit only.
- Jev: p 0.14; emphasize no_match (p 0.89); strength 3
- Prefilter: candidate, cues ['keep']
- The gate: host (uncertain)

**73. `q_looker`** (question)
- Message: "What do you think of my Looker experience?"
- Label: not a preference. A question.
- Jev: p 0.02; none skill:looker (p 0.90); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**74. `q_keep_excel`** (question)
- Message: "Should I keep Excel on my resume?"
- Label: not a preference. A question, not a statement of preference.
- Jev: p 0.04; none skill:excel (p 1.00); strength 1
- Prefilter: candidate, cues ['keep', 'should']
- The gate: drop

**75. `q_always_edu`** (question)
- Message: "Do you always put education first for new grads?"
- Label: not a preference. A question about the assistant.
- Jev: p 0.14; emphasize section:education (p 1.00); strength 3
- Prefilter: candidate, cues ['always']
- The gate: host (uncertain)

**76. `q_why_dropped`** (question)
- Message: "Why did you drop the Ballot Tally project?"
- Label: not a preference. A question.
- Jev: p 0.02; none proj:ballot tally (p 0.99); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**77. `q_how_many`** (question)
- Message: "How many bullets are on my Halcyon entry right now?"
- Label: not a preference. A question.
- Jev: p 0.02; none exp:associate data scientist|halcyon health analytics (p 0.97); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**78. `q_gpa_avoid`** (question)
- Message: "Is it bad to avoid listing a GPA below 3.5?"
- Label: not a preference. A question.
- Jev: p 0.06; suppress section:education (p 0.99); strength 2
- Prefilter: candidate, negated, cues ['avoid']
- The gate: drop

**79. `q_which_project`** (question)
- Message: "Which of my projects is stronger for a data engineering role?"
- Label: not a preference. A question.
- Jev: p 0.02; none section:projects (p 0.90); strength 2
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**80. `q_wording`** (question)
- Message: "What's the best way to word a forecasting bullet?"
- Label: not a preference. A question.
- Jev: p 0.04; format_rule no_match (p 0.75); strength 2
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**81. `q_why_spark`** (question)
- Message: "Can you explain why Spark ended up in the skills line?"
- Label: not a preference. A question.
- Jev: p 0.03; none skill:spark (p 1.00); strength 2
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**82. `q_never_excel`** (question)
- Message: "Would you recommend never listing Excel?"
- Label: not a preference. A question about advice, not the user's own rule.
- Jev: p 0.17; suppress skill:excel (p 1.00); strength 3
- Prefilter: candidate, negated, cues ['never']
- The gate: host (hard_preference)

**83. `q_gpa_keep`** (question)
- Message: "Do I need to keep my GPA on there?"
- Label: not a preference. A question.
- Jev: p 0.05; none section:education (p 0.98); strength 2
- Prefilter: candidate, cues ['keep']
- The gate: drop

**84. `xf_led_rollout`** (experience_fact)
- Message: "I led the Halcyon scheduling forecast rollout across 34 clinics."
- Label: not a preference. A fact about their work: knowledge-graph material.
- Jev: p 0.02; none exp:associate data scientist|halcyon health analytics (p 0.65); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**85. `xf_spark`** (experience_fact)
- Message: "I used Spark at Rivermount to process the retention data."
- Label: not a preference. A fact.
- Jev: p 0.02; none skill:spark (p 0.69); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**86. `xf_gpa`** (experience_fact)
- Message: "My GPA at Rivermount was 3.6."
- Label: not a preference. A fact.
- Jev: p 0.02; none section:education (p 0.98); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**87. `xf_looker`** (experience_fact)
- Message: "I also built a dashboard in Looker during my internship."
- Label: not a preference. A fact.
- Jev: p 0.02; none skill:looker (p 0.72); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**88. `xf_transit_class`** (experience_fact)
- Message: "Transit Pulse started as a class assignment but I kept maintaining it."
- Label: not a preference. A fact about the project.
- Jev: p 0.02; none proj:transit pulse (p 1.00); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**89. `xf_never_shipped`** (experience_fact)
- Message: "I never actually shipped the Ballot Tally workbook to production."
- Label: not a preference. Contains 'never' but states a fact.
- Jev: p 0.02; none proj:ballot tally (p 0.99); strength 4
- Prefilter: candidate, negated, cues ['never']
- The gate: drop

**90. `xf_no_k8s`** (experience_fact)
- Message: "I don't have any experience with Kubernetes."
- Label: not a preference. A fact about skills, not an instruction.
- Jev: p 0.02; none no_match (p 0.96); strength 1
- Prefilter: candidate, negated, cues ["don't"]
- The gate: drop

**91. `xf_start_date`** (experience_fact)
- Message: "I started at Halcyon in July and I'm still there."
- Label: not a preference. A fact.
- Jev: p 0.02; none exp:associate data scientist|halcyon health analytics (p 0.90); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**92. `xf_dbt`** (experience_fact)
- Message: "I taught myself dbt on the side while working."
- Label: not a preference. A fact.
- Jev: p 0.02; none skill:dbt (p 1.00); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**93. `xf_tests`** (experience_fact)
- Message: "I always wrote the tests myself on the forecasting project."
- Label: not a preference. Contains 'always' but states a fact.
- Jev: p 0.03; none no_match (p 0.59); strength 4
- Prefilter: candidate, cues ['always']
- The gate: drop

**94. `xf_hackathon`** (experience_fact)
- Message: "I want to mention that I also did a hackathon last spring."
- Label: not a preference. Shares a fact; not a rule for the resume.
- Jev: p 0.04; emphasize no_match (p 0.50); strength 2
- Prefilter: candidate, cues ['i want']
- The gate: drop

**95. `cc_thanks`** (chit_chat)
- Message: "Thanks, that looks great!"
- Label: not a preference. Small talk.
- Jev: p 0.02; none no_match (p 1.00); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**96. `cc_hello`** (chit_chat)
- Message: "hello again"
- Label: not a preference. Small talk.
- Jev: p 0.01; none no_match (p 1.00); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**97. `cc_ok`** (chit_chat)
- Message: "ok"
- Label: not a preference. Small talk.
- Jev: p 0.02; none no_match (p 1.00); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**98. `cc_morning`** (chit_chat)
- Message: "Good morning! Ready to work on my resume?"
- Label: not a preference. Small talk.
- Jev: p 0.02; none no_match (p 1.00); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**99. `cc_sounds_good`** (chit_chat)
- Message: "Sounds good, let's go ahead."
- Label: not a preference. Small talk.
- Jev: p 0.02; none no_match (p 1.00); strength 2
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**100. `cc_lol`** (chit_chat)
- Message: "lol that bullet was a mess"
- Label: not a preference. Small talk.
- Jev: p 0.02; none no_match (p 0.95); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**101. `cc_laptop`** (chit_chat)
- Message: "Hang on, my laptop is about to die."
- Label: not a preference. Small talk.
- Jev: p 0.01; none no_match (p 1.00); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**102. `cc_perfect`** (chit_chat)
- Message: "Perfect, thank you so much."
- Label: not a preference. Small talk.
- Jev: p 0.02; none no_match (p 1.00); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**103. `cc_back`** (chit_chat)
- Message: "I'll be back in ten minutes."
- Label: not a preference. Small talk.
- Jev: p 0.01; none no_match (p 1.00); strength 1
- Prefilter: not a candidate (never asked in production)
- The gate: drop

**104. `cc_worry`** (chit_chat)
- Message: "Don't worry about it, we can do that later."
- Label: not a preference. 'Don't' in small talk.
- Jev: p 0.02; none no_match (p 1.00); strength 1
- Prefilter: candidate, negated, cues ["don't"]
- The gate: drop

**105. `cc_keep_going`** (chit_chat)
- Message: "Keep going, I'm following."
- Label: not a preference. 'Keep' in small talk.
- Jev: p 0.02; none no_match (p 1.00); strength 1
- Prefilter: candidate, cues ['keep']
- The gate: drop

**106. `js_transit`** (job_scoped)
- Message: "For this job, lead with the Transit Pulse project."
- Label: emphasize proj:transit pulse, strength 3, job-scoped. Job-scoped emphasis.
- Jev: p 0.63; emphasize proj:transit pulse (p 1.00); strength 3
- Prefilter: candidate, cues ['lead with', 'for this job']
- The gate: write

**107. `js_excel`** (job_scoped)
- Message: "For this application, leave out Excel."
- Label: suppress skill:excel, strength 4, job-scoped. Job-scoped suppression.
- Jev: p 0.60; suppress skill:excel (p 1.00); strength 3
- Prefilter: candidate, negated, cues ['leave out', 'for this job']
- The gate: write

**108. `js_ballot`** (job_scoped)
- Message: "Just for this role, drop the Ballot Tally project."
- Label: suppress proj:ballot tally, strength 4, job-scoped. Job-scoped suppression.
- Jev: p 0.61; suppress proj:ballot tally (p 1.00); strength 4
- Prefilter: candidate, negated, cues ['for this job']
- The gate: write

**109. `js_streamlit`** (job_scoped)
- Message: "This time, don't mention Streamlit."
- Label: suppress skill:streamlit, strength 4, job-scoped. Job-scoped suppression.
- Jev: p 0.50; suppress skill:streamlit (p 0.99); strength 4
- Prefilter: candidate, negated, cues ["don't", 'for this job']
- The gate: write

**110. `js_dbt`** (job_scoped)
- Message: "For this job please always include dbt in the skills."
- Label: emphasize skill:dbt, strength 4, job-scoped. Job-scoped emphasis.
- Jev: p 0.91; emphasize skill:dbt (p 0.97); strength 4
- Prefilter: candidate, cues ['always', 'for this job']
- The gate: write

**111. `js_looker_never`** (job_scoped)
- Message: "Never mention Looker on this particular resume."
- Label: suppress skill:looker, strength 5, job-scoped. Job-scoped, absolute.
- Jev: p 0.68; suppress skill:looker (p 0.99); strength 5
- Prefilter: candidate, negated, cues ['never', 'for this job']
- The gate: host (hard_preference)

## Context messages (12)

Asked alone and with the previous assistant turn; the label assumes the turn is known.

**112. `cx_excel_never`** (context)
- Previous assistant turn: "I put Excel at the end of your skills line."
- Message: "Actually never list that again."
- Label: suppress skill:excel, strength 5. 'That' is Excel.
- Jev alone: p 0.78; suppress no_match (p 1.00); strength 5
- Jev with the turn: p 0.83; suppress skill:excel (p 1.00); strength 5

**113. `cx_ballot_drop`** (context)
- Previous assistant turn: "Here is the Ballot Tally project, it takes four lines."
- Message: "Drop it from now on."
- Label: suppress proj:ballot tally, strength 4. 'It' is the project.
- Jev alone: p 0.71; suppress no_match (p 1.00); strength 4
- Jev with the turn: p 0.68; suppress proj:ballot tally (p 1.00); strength 4

**114. `cx_edu_always`** (context)
- Previous assistant turn: "I led your resume with the education section."
- Message: "Good, always do it that way."
- Label: emphasize section:education, strength 3. 'That way' is education first.
- Jev alone: p 0.50; none no_match (p 1.00); strength 4
- Jev with the turn: p 0.89; emphasize section:education (p 0.98); strength 4

**115. `cx_shorten_one`** (context)
- Previous assistant turn: "I reworded the Transit Pulse bullet to be shorter."
- Message: "Shorten that one a bit more."
- Label: not a preference. A one-off edit.
- Jev alone: p 0.04; format_rule no_match (p 0.99); strength 3
- Jev with the turn: p 0.04; format_rule proj:transit pulse (p 0.99); strength 3

**116. `cx_one_page`** (context)
- Previous assistant turn: "Do you want a one-page resume?"
- Message: "Yes, always."
- Label: format_rule no item, strength 4. 'Always' answers the page question.
- Jev alone: p 0.41; emphasize no_match (p 1.00); strength 4
- Jev with the turn: p 0.82; format_rule no_match (p 1.00); strength 4

**117. `cx_gpa_never`** (context)
- Previous assistant turn: "I can add your GPA to the education line."
- Message: "Don't, ever."
- Label: suppress no item, strength 5. The GPA.
- Jev alone: p 0.33; none no_match (p 1.00); strength 5
- Jev with the turn: p 0.76; suppress section:education (p 0.94); strength 5

**118. `cx_spark_every`** (context)
- Previous assistant turn: "Should I mention the Spark work?"
- Message: "Yes, make sure you do on every resume."
- Label: emphasize skill:spark, strength 4. Spark.
- Jev alone: p 0.87; emphasize no_match (p 1.00); strength 4
- Jev with the turn: p 0.88; emphasize skill:spark (p 0.91); strength 4

**119. `cx_streamlit_keep_off`** (context)
- Previous assistant turn: "I removed Streamlit from this version."
- Message: "Good, keep it that way."
- Label: suppress skill:streamlit, strength 3. Keep Streamlit out.
- Jev alone: p 0.22; none no_match (p 1.00); strength 3
- Jev with the turn: p 0.52; suppress skill:streamlit (p 0.98); strength 3

**120. `cx_no_answer`** (context)
- Previous assistant turn: "Want me to move the skills section up?"
- Message: "No."
- Label: not a preference. An answer to a question.
- Jev alone: p 0.03; none no_match (p 1.00); strength 4
- Jev with the turn: p 0.11; suppress section:skills (p 0.92); strength 3

**121. `cx_thanks`** (context)
- Previous assistant turn: "Here is the new summary."
- Message: "Thanks, that works."
- Label: not a preference. Small talk.
- Jev alone: p 0.02; none no_match (p 1.00); strength 1
- Jev with the turn: p 0.03; none no_match (p 1.00); strength 1

**122. `cx_three_bullets`** (context)
- Previous assistant turn: "I trimmed the Rivermount bullets to three."
- Message: "Never go above three per role."
- Label: format_rule section:experience, strength 5. A count rule, absolute.
- Jev alone: p 0.90; format_rule section:experience (p 0.77); strength 5
- Jev with the turn: p 0.92; format_rule exp:data science intern|rivermount college office of institutional research (p 0.89); strength 5

**123. `cx_which_looker`** (context)
- Previous assistant turn: "Your skills line currently includes Looker."
- Message: "Why is that in there?"
- Label: not a preference. A question.
- Jev alone: p 0.03; none no_match (p 1.00); strength 1
- Jev with the turn: p 0.04; suppress skill:looker (p 0.96); strength 2

