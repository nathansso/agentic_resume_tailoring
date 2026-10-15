"""What the "agent" and the "user" say in the landing-site demo replay (issue #275).

Everything in this file is **written by hand** for the demo: the lines the user types,
the agent's narration, the records the agent extracts from `sample_resume.md`, and the
plan program it drafts. None of it is ART output. `scripts/build_site_demo.py` feeds it to
the real `art` tools and records what ART answers. The replay labels these lines as
written for the demo and the tool results as captured.

The plan obeys ART's gates except for one deliberate slip, marked below, so the replay can
show the consistency gate rejecting a figure the sources do not state.
"""

from __future__ import annotations

PROFILE = {
    "name": "Riley Park",
    "email": "riley.park@example.com",
    "phone": "(555) 010-0142",
    "linkedin_url": "https://linkedin.com/in/riley-park-demo",
    "github_username": "riley-park-demo",
}

POSTING_TITLE = "Data Science Intern, Summer 2027"
POSTING_COMPANY = "Cedarline Freight"
ROLE_FAMILY = "data_science"

# The agent's reading of the resume: one record per item, bullets verbatim.
RECORDS = [
    {"kind": "education", "data": {
        "institution": "Alder Valley State University",
        "degree": "B.S. Computer Science, Data Science minor",
        "end_date": "Expected June 2027", "gpa": "3.7"}},
    {"kind": "experience", "data": {
        "company": "Tamarack Logistics", "title": "Data Analyst Intern",
        "start_date": "2026-06", "end_date": "2026-09", "bullets": [
            "Built a weekly delivery-delay report in pandas and SQL that replaced a manual spreadsheet for the operations team.",
            "Cleaned 14 months of shipment records, about 380,000 rows, and documented 9 data-quality checks.",
            "Trained a gradient-boosted model in scikit-learn to flag late shipments, reaching 0.81 AUC on a held-out month.",
            "Walked 12 operations staff through the findings and wrote the handoff notes the team still uses."]}},
    {"kind": "experience", "data": {
        "company": "Alder Valley State University, Department of Computer Science",
        "title": "Teaching Assistant", "start_date": "2025-09", "end_date": "Present", "bullets": [
            "Held weekly office hours for a 120-student Data Structures course taught in Python.",
            "Graded labs and wrote pytest autograder tests for 4 assignments.",
            "Led review sessions on recursion and hash tables that about 40 students attended."]}},
    {"kind": "project", "data": {
        "name": "Transit Delay Predictor",
        "repo_url": "https://github.com/riley-park-demo/transit-delay",
        "description": "Predicts bus delays from public transit data.", "blurbs": [
            "Predicted bus delays with a scikit-learn random forest trained on 2 years of public transit data, reaching a mean absolute error of 3.1 minutes.",
            "Served predictions through a FastAPI endpoint packaged with Docker and covered by 14 pytest tests.",
            "Built a Streamlit dashboard that shows delay hot spots by route and hour."]}},
    {"kind": "project", "data": {
        "name": "Campus Dining Recommender",
        "repo_url": "https://github.com/riley-park-demo/dining-recs",
        "description": "Recommends campus dining hall dishes.", "blurbs": [
            "Built a content-based recommender over 600 menu items using TF-IDF and cosine similarity in Python.",
            "Surveyed 45 classmates to evaluate it; 62% of its top-5 picks were rated 4 stars or higher."]}},
    {"kind": "project", "data": {
        "name": "Recycling Sorter",
        "description": "A 24-hour hackathon project that sorts recycling from photos.", "blurbs": [
            "Fine-tuned a ResNet-18 in PyTorch on 5,200 labeled photos to sort 6 kinds of recycling, reaching 88% accuracy.",
            "Built the demo as a Flask app and presented it to the judges."]}},
    {"kind": "project", "data": {
        "name": "Budget Tracker",
        "repo_url": "https://github.com/riley-park-demo/budget-tracker",
        "description": "A command-line budget tracker.", "blurbs": [
            "Wrote a command-line budget tracker in Java with an SQLite store and CSV import."]}},
]
SKILLS = [
    ("Python", "Language"), ("SQL", "Language"), ("pandas", "Library"), ("NumPy", "Library"),
    ("scikit-learn", "Library"), ("PyTorch", "Library"), ("FastAPI", "Framework"),
    ("Flask", "Framework"), ("Streamlit", "Framework"), ("Docker", "Tool"), ("Git", "Tool"),
    ("pytest", "Tool"), ("Jupyter", "Tool"), ("Tableau", "Tool"), ("Java", "Language"),
    ("TypeScript", "Language"), ("React", "Framework"),
]

# The agent's reading of the posting, as atomic requirements for open_job.
REQUIREMENTS = [
    {"text": "The candidate is pursuing a B.S. or M.S. in computer science, statistics, data science or a related field.",
     "type": "required", "criticality": 3, "terms": ["computer science", "data science"],
     "source_section": "Required qualifications"},
    {"text": "The candidate has experience with Python and SQL.", "type": "required",
     "criticality": 5, "terms": ["Python", "SQL"], "source_section": "Required qualifications"},
    {"text": "The candidate is familiar with machine learning libraries such as scikit-learn.",
     "type": "required", "criticality": 4, "terms": ["scikit-learn", "machine learning"],
     "source_section": "Required qualifications"},
    {"text": "The candidate can explain results clearly to a non-technical audience.",
     "type": "required", "criticality": 3, "terms": ["explain results", "non-technical"],
     "source_section": "Required qualifications"},
    {"text": "The candidate has built models that predict delays.", "type": "required",
     "criticality": 4, "terms": ["predict", "delays", "models"], "source_section": "What you'll do"},
    {"text": "The candidate has built dashboards, for example with Streamlit or Tableau.",
     "type": "preferred", "criticality": 2, "terms": ["dashboards", "Streamlit", "Tableau"],
     "source_section": "Preferred qualifications"},
    {"text": "The candidate has served a model behind an API.", "type": "preferred",
     "criticality": 2, "terms": ["API", "FastAPI", "model"], "source_section": "Preferred qualifications"},
    {"text": "The candidate has exposure to Docker.", "type": "preferred", "criticality": 1,
     "terms": ["Docker"], "source_section": "Preferred qualifications"},
]

# Item keys, as `list_items` returns them.
TAMARACK = "exp:data analyst intern|tamarack logistics"
TA = "exp:teaching assistant|alder valley state university, department of computer science"
TRANSIT = "proj:transit delay predictor"
DINING = "proj:campus dining recommender"
RECYCLING = "proj:recycling sorter"
BUDGET = "proj:budget tracker"

# The deliberate slip. The agent "quantifies" the report bullet with a figure that no source
# states. ART's consistency gate must catch it. The replay marks this draft as written to
# show the gate.
SLIP_SUFFIX = ", cutting its weekly preparation from 3 hours to 20 minutes"
ORIGINAL_REPORT = ("Built a weekly delivery-delay report in pandas and SQL that replaced a "
                   "manual spreadsheet for the operations team")

# Bullet text -> the source bullet it rests on (matched against get_item at run time, so the
# evidence id `<key>#b<n>` is read from ART's own listing, not assumed).
TAMARACK_BULLETS = [
    ("Trained a gradient-boosted scikit-learn model to predict late shipments, reaching 0.81 AUC on a held-out month.",
     "Trained a gradient-boosted model in scikit-learn to flag late shipments"),
    (ORIGINAL_REPORT + SLIP_SUFFIX + ".", ORIGINAL_REPORT),
    ("Cleaned 14 months of shipment records, about 380,000 rows, and documented 9 data-quality checks.",
     "Cleaned 14 months of shipment records"),
    ("Explained the findings to 12 operations staff and wrote the handoff notes the team still uses.",
     "Walked 12 operations staff through the findings"),
]
TRANSIT_BULLETS = [
    ("Predicted bus delays with a scikit-learn random forest trained on 2 years of public transit data, reaching a mean absolute error of 3.1 minutes.",
     "Predicted bus delays with a scikit-learn random forest"),
    ("Built a Streamlit dashboard that shows delay hot spots by route and hour.",
     "Built a Streamlit dashboard"),
    ("Served delay predictions through a FastAPI endpoint packaged with Docker and covered by 14 pytest tests.",
     "Served predictions through a FastAPI endpoint"),
]
SKILL_ORDER = ["Python", "SQL", "scikit-learn", "pandas", "Streamlit", "Tableau", "FastAPI",
               "Docker", "NumPy", "PyTorch", "pytest", "Git", "Jupyter"]

DROP_REASON = "user:approved dropping Budget Tracker, a Java project the posting does not ask for"

# --- What is said -----------------------------------------------------------------------

USER_SETUP = "/art:setup resume.md"
USER_TAILOR_TEMPLATE = "/art:tailor {title}, {company} (sample posting, {lines} lines pasted)"
USER_APPROVE = "Looks good. Go ahead, and yes, drop Budget Tracker."

AGENT_SETUP_1 = ("I'll read resume.md and store what it says: your degree, roles, projects and "
                 "skills, with the bullets exactly as written. I won't add anything the file "
                 "doesn't state.")
AGENT_SETUP_2 = "Stored {created} records. Now your resume header."
AGENT_SETUP_3 = ("Your graph holds {n_exp} roles, {n_proj} projects, {n_skill} skills and your "
                 "degree. Paste a posting whenever you're ready.")

AGENT_POSTING_1 = "Reading the posting, then checking whether you've saved any preferences."
AGENT_POSTING_2 = ("No saved preferences yet. Opening the job with the {n_req} requirements I "
                   "pulled out of the posting.")
AGENT_POSTING_3 = ("ART matched {n_match} of the posting's terms to skills you already have. It "
                   "doesn't guess at the ones it couldn't match, like \"machine learning\", so "
                   "I'll only use those words where your own bullets support them.")
AGENT_BASE = ("First I'll save your graph as version 1, so there's something to compare the "
              "tailored resume against.")
AGENT_READ = "Reading your internship in full, so I cite its bullets by id."

AGENT_PLAN_INTRO = ("No approved bullet variants yet, so I'll write from your source bullets. "
                    "Here's the plan. Nothing changes until you say so.")
AGENT_PLAN_ITEMS = [
    "Data Analyst Intern: lead with the late-shipment model, and say \"predict\", the "
    "posting's word, instead of \"flag\".",
    "Same role: quantify the report bullet, \"cutting its weekly preparation from 3 hours to "
    "20 minutes\".",
    "Transit Delay Predictor: put the dashboard before the API, since the posting names "
    "dashboards.",
    "Drop Budget Tracker. It's a Java project and the posting doesn't ask for Java.",
    "Skills: Python, SQL and scikit-learn first; Flask, Java, TypeScript and React come off.",
]
AGENT_PLAN_OUTRO = "Everything else stays as you wrote it. Go ahead?"
SLIP_NOTE = ("Written for this demo: the second item adds a figure that no source states, so "
             "the replay can show the check that catches it.")

AGENT_GATES_1 = "Running the plan through ART's checks."
AGENT_GATES_2 = ("ART reverted two of my changes. The report bullet: \"3 hours\" and \"20 minutes\" "
                 "aren't in the bullet it cites, I made those numbers up, and that sank the rest of "
                 "the role's edits with it. And the Transit Delay reorder improved nothing the "
                 "checks measure, so that project stays as it was. Everything else went in as "
                 "version 2. I'll resubmit the role with your original wording on the report "
                 "bullet and leave the projects alone.")
AGENT_GATES_3 = "Committed as version 3. Now I'll render version 1, then version 3, as PDFs."

AGENT_DONE = ("Done. The tailored resume fits on one page and is at {pdf}. I rendered version 1 "
              "first for the comparison, so that file now holds the tailored one. Read it "
              "through before you send it.")
