"""What the "agent" and the "user" say in the landing-site demo replay (issues #275, #337).

Everything in this file is **written by hand** for the demo: the lines the user types,
the agent's narration, the records the agent extracts from the sample sources, the
requirements it reads out of the posting, and the plan program it drafts. None of it is ART
output. `scripts/build_site_demo.py` feeds it to the real `art` tools and records what ART
answers. The replay labels these lines as written for the demo and the tool results as
captured.

The plan obeys ART's gates except for one deliberate slip, marked below, so the replay can
show the consistency gate rejecting a figure the sources do not state.

The story (#337). Riley's usual one-page resume is a software resume: it leads with web
work. Riley's graph also holds two data projects that live only in repo READMEs, and a SQL
bullet that sits last on the usual page. The posting asks for data work. Tailoring brings the
data projects in, moves the SQL bullet up, cuts the web work, rewords a few bullets toward the
posting's own terms, and ranks the skills to the posting.
"""

from __future__ import annotations

PROFILE = {
    "name": "Riley Park",
    "email": "riley.park@example.com",
    "phone": "(555) 010-0142",
    "linkedin_url": "https://linkedin.com/in/riley-park-demo",
    "github_username": "riley-park-demo",
}

POSTING_TITLE = "Data Science Intern, Growth Analytics, Summer 2027"
POSTING_SHORT_TITLE = "Data Science Intern"
POSTING_COMPANY = "Brightwell Market"
POSTING_LOCATION = "Hybrid, 12 weeks"
ROLE_FAMILY = "data_science"

# --- the sources -------------------------------------------------------------------------
# The agent's reading of `sample_resume.md`: one record per item, bullets verbatim.
RESUME_RECORDS = [
    {"kind": "education", "data": {
        "institution": "Alder Valley State University",
        "degree": "B.S. Computer Science, Data Science minor",
        "end_date": "Expected June 2027", "gpa": "3.7"}},
    {"kind": "experience", "data": {
        "company": "Kestrel Print & Sign", "title": "Web Developer Intern",
        "start_date": "2026-06", "end_date": "2026-08", "bullets": [
            "Built a React storefront for custom print orders that replaced the shop's phone-and-email process.",
            "Added a Node and PostgreSQL order API with 12 endpoints and a staging environment.",
            "Fixed 30 accessibility issues from an audit, raising the Lighthouse accessibility score from 71 to 96.",
            "Set up GitHub Actions to run the test suite on every pull request.",
            "Wrote SQL queries that report weekly sales by product, which the owner used to decide what to stock."]}},
    {"kind": "experience", "data": {
        "company": "Alder Valley State University, Department of Computer Science",
        "title": "Teaching Assistant", "start_date": "2025-09", "end_date": "Present", "bullets": [
            "Held weekly office hours for a 120-student Data Structures course taught in Python.",
            "Graded labs and wrote pytest autograder tests for 4 assignments.",
            "Led review sessions on recursion and hash tables that about 40 students attended.",
            "Explained recursion and hash tables in plain language to students with no programming background."]}},
    {"kind": "experience", "data": {
        "company": "Fernhollow Coffee", "title": "Shift Lead",
        "start_date": "2024-06", "end_date": "2025-08", "bullets": [
            "Trained 6 new baristas on the register, the opening checklist and the espresso machine.",
            "Reconciled the till against the day's sales report at the end of every shift.",
            "Scheduled a team of 9 around class timetables using a shared spreadsheet.",
            "Opened the shop alone two mornings a week and handled the cash drop."]}},
    {"kind": "project", "data": {
        "name": "Recycling Sorter",
        "description": "A 24-hour hackathon project that sorts recycling from photos.", "blurbs": [
            "Fine-tuned a ResNet-18 in PyTorch on 5,200 labeled photos to sort 6 kinds of recycling, reaching 88% accuracy.",
            "Built the demo as a Flask app and presented it to the judges.",
]}},
    {"kind": "project", "data": {
        "name": "Budget Tracker",
        "repo_url": "https://github.com/riley-park-demo/budget-tracker",
        "description": "A command-line budget tracker.", "blurbs": [
            "Wrote a command-line budget tracker in Java with an SQLite store and CSV import.",
            "Added a monthly summary view and 20 unit tests with JUnit."]}},
    {"kind": "project", "data": {
        "name": "Portfolio Site",
        "repo_url": "https://github.com/riley-park-demo/portfolio",
        "description": "Riley's personal portfolio site.", "blurbs": [
            "Built a personal portfolio in React and TypeScript and deployed it with GitHub Pages.",
            "Scored 98 on Lighthouse performance by lazy-loading images and trimming the bundle to 140 KB.",
            "Added a dark-mode toggle and a contact form that sends email through a serverless function."]}},
    {"kind": "project", "data": {
        "name": "Campus Dining Recommender",
        "repo_url": "https://github.com/riley-park-demo/dining-recs",
        "description": "Recommends campus dining hall dishes.", "blurbs": [
            "Built a content-based recommender over 600 menu items using TF-IDF and cosine similarity in Python.",
            "Surveyed 45 classmates to evaluate it; 62% of its top-5 picks were rated 4 stars or higher."]}},
]
RESUME_SKILLS = [
    ("Python", "Language"), ("Java", "Language"), ("JavaScript", "Language"),
    ("TypeScript", "Language"), ("React", "Framework"), ("Node.js", "Framework"),
    ("SQL", "Language"), ("PostgreSQL", "Database"), ("Flask", "Framework"),
    ("FastAPI", "Framework"), ("pandas", "Library"), ("NumPy", "Library"),
    ("PyTorch", "Library"), ("Docker", "Tool"), ("Git", "Tool"), ("GitHub Actions", "Tool"),
    ("pytest", "Tool"), ("Jupyter", "Tool"),
]

# The agent's reading of `sample_repos.md`: two projects that are not on the resume.
REPO_RECORDS = [
    {"kind": "project", "data": {
        "name": "Checkout A/B Test",
        "repo_url": "https://github.com/riley-park-demo/checkout-ab-test",
        "description": "A class project that compares two checkout flows on a mock store.", "blurbs": [
            "Designed an A/B test of two checkout flows on a mock store, choosing the sample size of 8,000 sessions per variant with a power calculation before running it.",
            "Analyzed the results with a two-proportion z-test in Python (statsmodels); the one-page flow raised conversion from 3.1% to 3.7% (p = 0.03).",
            "Built a Streamlit dashboard that shows daily conversion and a 95% confidence interval for each variant.",
            "Wrote a one-page memo with a recommendation for classmates who had not taken the course."]}},
    {"kind": "project", "data": {
        "name": "Subscription Churn Model",
        "repo_url": "https://github.com/riley-park-demo/subscription-churn",
        "description": "Predicts which subscribers cancel, from a public customer dataset.", "blurbs": [
            "Engineered 12 features from the customer table in SQL, such as tenure and support-call counts.",
            "Trained logistic regression and gradient-boosted models in scikit-learn, reaching 0.84 AUC on a held-out set.",
            "Built a Tableau dashboard that ranks customers by churn risk and shows churn by plan type.",
            "Wrote the analysis up in a Jupyter notebook with a plain-language summary for readers who do not work with data."]}},
]
REPO_RECORDS.append({"kind": "project", "data": {
    "name": "Retail Sales Dashboard",
    "repo_url": "https://github.com/riley-park-demo/retail-sales-dashboard",
    "description": "A dashboard over a public retail dataset.", "blurbs": [
        "Loaded 1.2 million order rows into SQLite and wrote 8 SQL queries on sales, returns and repeat purchases.",
        "Charted weekly revenue by region and product category in Tableau.",
        "Found that 4 of the 31 product categories brought in 58% of revenue."]}})
REPO_SKILLS = [("scikit-learn", "Machine Learning"), ("Streamlit", "Framework"), ("Tableau", "Tool"),
               ("statsmodels", "Library")]

# The agent's reading of the posting, as atomic requirements for open_job. `id` is not an ART
# field: it names the requirement on the page's posting card.
REQUIREMENTS = [
    {"id": "degree", "short": "Pursuing a B.S. or M.S. in a quantitative field",
     "text": "The candidate is pursuing a B.S. or M.S. in computer science, statistics, data science or a related field.",
     "type": "required", "criticality": 2, "terms": ["computer science", "data science"],
     "source_section": "Required qualifications"},
    {"id": "sql", "short": "SQL and Python, including pandas",
     "text": "The candidate has experience with SQL and Python, including pandas.", "type": "required",
     "criticality": 5, "terms": ["SQL", "Python", "pandas"], "source_section": "Required qualifications"},
    {"id": "ab", "short": "Design and analyze A/B tests",
     "text": "The candidate has designed or analyzed experiments, for example A/B tests and significance testing.",
     "type": "required", "criticality": 5,
     "terms": ["A/B test", "experiments", "significance", "statsmodels"],
     "source_section": "Required qualifications"},
    {"id": "ml", "short": "Machine learning with scikit-learn",
     "text": "The candidate is familiar with machine learning libraries such as scikit-learn.",
     "type": "required", "criticality": 4, "terms": ["scikit-learn", "machine learning", "predict", "cancel"],
     "source_section": "Required qualifications"},
    {"id": "explain", "short": "Explain results to a non-technical audience",
     "text": "The candidate can explain results clearly to a non-technical audience.",
     "type": "required", "criticality": 3, "terms": ["explain", "non-technical", "plain language"],
     "source_section": "Required qualifications"},
    {"id": "dash", "short": "Dashboards, for example in Streamlit or Tableau",
     "text": "The candidate has built dashboards, for example with Streamlit or Tableau.",
     "type": "preferred", "criticality": 2, "terms": ["dashboards", "Streamlit", "Tableau"],
     "source_section": "Preferred qualifications"},
]

# Item keys, as `list_items` returns them.
KESTREL = "exp:web developer intern|kestrel print & sign"
TA = "exp:teaching assistant|alder valley state university, department of computer science"
FERNHOLLOW = "exp:shift lead|fernhollow coffee"
RECYCLING = "proj:recycling sorter"
DINING = "proj:campus dining recommender"
BUDGET = "proj:budget tracker"
PORTFOLIO = "proj:portfolio site"
ABTEST = "proj:checkout a/b test"
CHURN = "proj:subscription churn model"
RETAIL = "proj:retail sales dashboard"

# The usual page: the resume's own content, in the resume's own skill order, with the three
# repo projects left off (the user's instruction in the replay).
GENERIC_SKILLS = [n for n, _ in RESUME_SKILLS]
LEAVE_OFF_REASON = "user:my usual resume is resume.md, so leave the three repo projects off"

# The deliberate slip. The agent adds a figure it worked out itself, a relative lift that no
# source states. ART's consistency gate must catch it, because every number on the page has to
# be in the bullet it cites. The replay marks this draft as written to show the gate.
AB_RESULT = "the one-page flow raised conversion from 3.1% to 3.7% (p = 0.03)"
AB_RESULT_SLIP = "the one-page flow raised conversion from 3.1% to 3.7%, a 19% relative lift (p = 0.03)"
SLIP_FIGURE = "19%"
SQL_BULLET = ("Wrote SQL queries that report weekly sales by product, which the owner used to "
              "decide what to stock")

SQL_REWORDED = ("Wrote SQL queries that turn order data into a weekly sales report by product, "
                "which the owner used to decide what to stock.")

# Bullet text -> the source bullet it rests on (matched against get_item at run time, so the
# evidence id `<key>#b<n>` is read from ART's own listing, not assumed). An unchanged bullet
# starts with the same words as its source.
KESTREL_BULLETS = [
    (SQL_REWORDED, [SQL_BULLET, "Added a Node and PostgreSQL order API"]),
    ("Built a React storefront for custom print orders that replaced the shop's phone-and-email process.",
     "Built a React storefront"),
    ("Fixed 30 accessibility issues from an audit, raising the Lighthouse accessibility score from 71 to 96.",
     "Fixed 30 accessibility issues"),
]
TA_BULLETS = [
    ("Explained recursion and hash tables in plain language to a non-technical audience of students with no programming background.",
     "Explained recursion and hash tables in plain language"),
    ("Held weekly office hours for a 120-student Data Structures course taught in Python.",
     "Held weekly office hours"),
    ("Graded labs and wrote pytest autograder tests for 4 assignments.", "Graded labs"),
    ("Led review sessions on recursion and hash tables that about 40 students attended.", "Led review sessions"),
]
AB_BULLETS = [
    ("Designed an A/B test of two checkout flows on a mock store, choosing the sample size of 8,000 sessions per variant with a power calculation before running it.",
     "Designed an A/B test"),
    ("Analyzed the A/B test in Python with a two-proportion z-test (statsmodels) to check significance; " + AB_RESULT + ".",
     "Analyzed the results"),
    ("Built a Streamlit dashboard that shows daily conversion and a 95% confidence interval for each variant.",
     "Built a Streamlit dashboard"),
    ("Wrote a one-page memo with a recommendation for a non-technical audience of classmates who had not taken the course.",
     "Wrote a one-page memo"),
]
CHURN_BULLETS = [
    ("Engineered 12 features from the customer table in SQL, such as tenure and support-call counts.",
     "Engineered 12 features"),
    ("Trained logistic regression and gradient-boosted models in scikit-learn to predict which subscribers cancel, reaching 0.84 AUC on a held-out set.",
     "Trained logistic regression"),
    ("Built a Tableau dashboard that ranks customers by churn risk and shows churn by plan type.",
     "Built a Tableau dashboard"),
    ("Wrote the analysis up in a Jupyter notebook with a plain-language summary for readers who do not work with data.",
     "Wrote the analysis up"),
]
RETAIL_BULLETS = [
    ("Loaded 1.2 million order rows into SQLite and wrote 8 SQL queries on sales, returns and repeat purchases.",
     "Loaded 1.2 million order rows"),
    ("Charted weekly revenue by region and product category in Tableau.",
     "Charted weekly revenue"),
    ("Found that 4 of the 31 product categories brought in 58% of revenue.", "Found that 4 of the 31"),
]
SECTION_ORDER = ["education", "projects", "experience", "skills"]
SKILL_ORDER = ["Python", "SQL", "pandas", "statsmodels", "scikit-learn", "Streamlit", "Tableau",
               "Git", "Jupyter", "NumPy"]

# Why each node is in the plan: the posting terms it is aimed at. ART's executor takes these
# as `keywords`; the page maps them to requirements by the terms each requirement lists.
NODE_KEYWORDS = {
    "n1": ["SQL", "pandas"],
    "n2": ["explain", "non-technical"],
    "n3": ["A/B test", "experiments", "significance", "dashboards", "Streamlit", "non-technical"],
    "n4": ["predict", "scikit-learn", "machine learning", "Tableau", "dashboards", "plain language"],
    "n6": ["SQL", "dashboards", "Tableau"],
}
DROP_REASON = "user:approved dropping the Fernhollow Coffee job, which the posting does not ask for"

# Why a cut is a cut, for the page's "Cut" group. Written for the demo; the cuts themselves
# are ART's own diff. A project that was swapped for another is described by the swap.
CUT_REASONS = {
    "default": "No requirement in the posting asks for it.",
    KESTREL: "The posting asks for data work, not web infrastructure.",
    FERNHOLLOW: "The posting asks for data work, and the page is full without it.",
}

# --- What is said -----------------------------------------------------------------------

USER_SETUP = "/art:setup resume.md, plus the READMEs of three repos that aren't on my resume"
USER_TAILOR_TEMPLATE = ("/art:tailor {company}, {title} (sample posting, {lines} lines pasted). "
                        "Compare it with my usual resume: resume.md alone.")
USER_APPROVE = "Looks good. Go ahead, and yes, drop the coffee shop job."

AGENT_SETUP_1 = ("I'll read resume.md and the three READMEs and store what they say: your degree, "
                 "roles, projects and skills, with the bullets exactly as written. I won't add "
                 "anything the files don't state.")
AGENT_SETUP_2 = "Stored {created} records from resume.md. Now the three repos."
AGENT_SETUP_3 = ("Stored {created} more: the A/B test, churn and retail projects, and the skills "
                 "they name. Now your header.")
AGENT_SETUP_4 = ("Your graph holds {n_exp} roles, {n_proj} projects, {n_skill} skills and your "
                 "degree: more than fits on one page. Paste a posting whenever you're ready.")

AGENT_POSTING_1 = "Reading the posting, then checking whether you've saved any preferences."
AGENT_POSTING_2 = ("No saved preferences yet. Opening the job with the {n_req} requirements I "
                   "pulled out of the posting.")
AGENT_POSTING_3 = ("ART matched {n_match} of the posting's terms to skills you already have. It "
                   "doesn't guess at the ones it couldn't match, so I'll only use those words "
                   "where your own bullets support them.")
AGENT_BASE = ("First, your usual resume as version 1, so there's something to compare against: "
              "what resume.md says, with the three repo projects left off. Nothing reworded.")
AGENT_READ = "Reading your internship in full, so I cite its bullets by id."

AGENT_PLAN_INTRO = ("No approved bullet variants yet, so I'll write from your source bullets. "
                    "Here's the plan. Nothing changes until you say so.")
AGENT_PLAN_ITEMS = [
    "Projects first, above Experience: your best matches for this posting are projects.",
    "Projects: swap Checkout A/B Test, Subscription Churn Model and Retail Sales Dashboard in "
    "for Recycling Sorter, Budget Tracker and Portfolio Site. For the A/B result I'll add the "
    "19% relative lift I worked out from your 3.1% and 3.7%.",
    "Web Developer Intern: lead with the SQL bullet, which the posting asks for, and cut the "
    "Node API and GitHub Actions bullets.",
    "Teaching Assistant: lead with the plain-language bullet and call it a \"non-technical "
    "audience\", the posting's words.",
    "Drop the Fernhollow Coffee job; the posting doesn't ask for it.",
    "Skills: Python, SQL, pandas, statsmodels and scikit-learn first; the web skills come off.",
]
AGENT_PLAN_OUTRO = "Campus Dining Recommender and everything else stays as you wrote it. Go ahead?"
SLIP_NOTE = ("Written for this demo: the second item adds a figure that no source states, so "
             "the replay can show the check that catches it.")

AGENT_GATES_1 = "Running the plan through ART's checks."
AGENT_GATES_2 = ("ART reverted one of my changes: the Checkout A/B Test swap. \"19%\" isn't in the "
                 "bullet it cites. I worked that figure out myself, and ART only accepts numbers "
                 "your own bullet states. Everything else went in as version 2. I'll resubmit the "
                 "swap with your original 3.1% and 3.7%.")
AGENT_GATES_3 = "Committed as version 3. Here is what changed from version 1."
AGENT_GATES_4 = "Now I'll render version 1, then version 3, as PDFs."

AGENT_DONE = ("Done. The tailored resume fits on one page and is at {pdf}. I rendered version 1 "
              "first for the comparison, so that file now holds the tailored one. Read it "
              "through before you send it.")
