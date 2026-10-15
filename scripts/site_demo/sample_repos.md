# Riley's repositories that are not on the resume

Three project READMEs from github.com/riley-park-demo, pasted together. None of the three projects is on Riley's resume.

---

## checkout-ab-test (README)

**Checkout A/B Test** (github.com/riley-park-demo/checkout-ab-test)

A class project for a statistics course. It compares a one-page checkout flow with the standard three-step flow on a mock online store.

- Designed an A/B test of two checkout flows on a mock store, choosing the sample size of 8,000 sessions per variant with a power calculation before running it.
- Analyzed the results with a two-proportion z-test in Python (statsmodels); the one-page flow raised conversion from 3.1% to 3.7% (p = 0.03).
- Built a Streamlit dashboard that shows daily conversion and a 95% confidence interval for each variant.
- Wrote a one-page memo with a recommendation for classmates who had not taken the course.

---

## subscription-churn (README)

**Subscription Churn Model** (github.com/riley-park-demo/subscription-churn)

A personal project on a public customer dataset of 7,000 subscribers.

- Engineered 12 features from the customer table in SQL, such as tenure and support-call counts.
- Trained logistic regression and gradient-boosted models in scikit-learn, reaching 0.84 AUC on a held-out set.
- Built a Tableau dashboard that ranks customers by churn risk and shows churn by plan type.
- Wrote the analysis up in a Jupyter notebook with a plain-language summary for readers who do not work with data.

---

## retail-sales-dashboard (README)

**Retail Sales Dashboard** (github.com/riley-park-demo/retail-sales-dashboard)

A personal project on a public retail dataset.

- Loaded 1.2 million order rows into SQLite and wrote 8 SQL queries on sales, returns and repeat purchases.
- Charted weekly revenue by region and product category in Tableau.
- Found that 4 of the 31 product categories brought in 58% of revenue.
