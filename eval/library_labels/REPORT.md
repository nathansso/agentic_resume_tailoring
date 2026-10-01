# Bullet library: Jev variant choice and track baseline (issue #199)

Recorded 2026-10-01 with jev-1.13.0 (84 decisions, 51119 input and 9197 output tokens); questions variant_choice@v1, track_baseline@v1; replay hit rate 100%. Labels: planner-reviewed, pending user spot-check.

Both thresholds are fitted on Jev's own answers alone, as if no code rule existed (the host's explicit `role_family` winning, the drift guard and the fallbacks): they only ever remove or soften a pick. Floor 0.50 for both. Shipped: `TAU_VARIANT` 0.50, `TAU_BASELINE` 0.50; the fit recommends 0.50 and 0.50.

## Variant choice

48 cases. Fitted threshold **0.50** (floor 0.50). `correct` counts a right `no_match` as well as a right pick.

|  | correct | picks made | right picks | poor-fit picks | lost (said none though one fits) |
|---|---|---|---|---|---|
| Jev, its own argmax (no threshold) | 44/48 (92%) | 31 | 29 | 0 | 2 |
| **Jev at the threshold 0.50** | **39/48 (81%)** | 25 | 24 | 0 | 8 |
| The #229 fallback (word overlap) | 18/48 (38%) | 18 | 7 | 7 | 19 |

Wrong picks that are not poor-fit: Jev at the threshold 1, the fallback 4.

### By category

| category | cases | Jev argmax correct | Jev@τ correct | Jev@τ picks | Jev@τ poor | fallback correct | fallback picks | fallback poor |
|---|---|---|---|---|---|---|---|---|
| clear_best | 8 | 7/8 | 7/8 | 7 | 0 | 1/8 | 3 | 1 |
| close_call | 8 | 8/8 | 4/8 | 4 | 0 | 4/8 | 5 | 1 |
| no_match | 8 | 8/8 | 8/8 | 0 | 0 | 6/8 | 2 | 2 |
| synonym | 8 | 5/8 | 4/8 | 5 | 0 | 0/8 | 2 | 0 |
| keyword_trap | 8 | 8/8 | 8/8 | 5 | 0 | 3/8 | 6 | 3 |
| single | 8 | 8/8 | 8/8 | 4 | 0 | 4/8 | 0 | 0 |

### The threshold grid

At each grid value (Jev's own argmax kept only when its probability is at least the value): the picks made, the right ones, the poor-fit ones (the rule's first key), and the cases left as none.

| τ | picks | right picks | poor-fit picks | other wrong picks | lost | correct overall |
|---|---|---|---|---|---|---|
| 0.50 ← | 25 | 24 | 0 | 1 | 8 | 39/48 |
| 0.55 | 23 | 22 | 0 | 1 | 10 | 37/48 |
| 0.60 | 18 | 18 | 0 | 0 | 15 | 33/48 |
| 0.65 | 18 | 18 | 0 | 0 | 15 | 33/48 |
| 0.70 | 18 | 18 | 0 | 0 | 15 | 33/48 |
| 0.75 | 14 | 14 | 0 | 0 | 19 | 29/48 |
| 0.80 | 12 | 12 | 0 | 0 | 21 | 27/48 |
| 0.85 | 10 | 10 | 0 | 0 | 23 | 25/48 |
| 0.90 | 7 | 7 | 0 | 0 | 26 | 22/48 |
| 0.95 | 4 | 4 | 0 | 0 | 29 | 19/48 |

Highest poor-fit pick by Jev: none; lowest right pick the fit keeps: 0.54; candidates with the same counts: 0.50; the fit takes the one nearest the middle of the gap, ties to the higher.

### Disagreements between the label and Jev (4)

| id | category | label | Jev (argmax, p) | Jev distribution | fallback |
|---|---|---|---|---|---|
| `a_mg_dashboard` | clear_best | ce_dash | no_match 0.49 | no_match 0.49, ce_dash 0.41, ce_surv 0.10 | ce_surv |
| `d_next_alerts` | synonym | t_drift_syn | t_ray 0.56 | t_ray 0.56, no_match 0.20, t_drift_syn 0.13 | t_kafka |
| `d_tebra_backfill` | synonym | n_kafka_syn, n_spark | n_dbt 0.44 | n_dbt 0.44, n_spark 0.33, no_match 0.17 | n_dbt |
| `d_rbi_scripted_env` | synonym | b_terraform_syn, b_airflow (poor: b_demand) | no_match 0.40 | no_match 0.40, b_airflow 0.33, b_terraform_syn 0.21 | no_match |

### Every case

| id | category | label | Jev (argmax, p) | Jev@τ | fallback | Jev@τ outcome | fallback outcome |
|---|---|---|---|---|---|---|---|
| `a_esri_churn` | clear_best | f_churn (poor: f_report) | f_churn 0.97 | f_churn | no_match | correct | lost |
| `a_mg_dashboard` | clear_best | ce_dash | no_match 0.49 | no_match | ce_surv | lost | wrong |
| `a_tebra_connector` | clear_best | fe_connector | fe_connector 0.81 | fe_connector | no_match | correct | lost |
| `a_milw_autoencoder` | clear_best | s_auto | s_auto 0.95 | s_auto | no_match | correct | lost |
| `a_fs_app` | clear_best | sp_app | sp_app 0.84 | sp_app | no_match | correct | lost |
| `a_fe_app` | clear_best | sp_app (poor: sp_deploy) | sp_app 0.56 | sp_app | sp_deploy | correct | poor |
| `a_asc_autograder` | clear_best | h_autograder (poor: h_grade, h_lab) | h_autograder 0.56 | h_autograder | no_match | correct | lost |
| `a_swbc_clean` | clear_best | c_clean (poor: c_mixed) | c_clean 0.98 | c_clean | c_clean | correct | correct |
| `b_esri_forecast` | close_call | hc_forecast, hc_forecast_ml (poor: hc_dbt) | hc_forecast 0.72 | hc_forecast | hc_forecast_ml | correct | correct |
| `b_next_forecast` | close_call | hc_forecast, hc_forecast_ml (poor: hc_dbt) | hc_forecast_ml 0.35 | no_match | hc_dbt | lost | poor |
| `b_mg_data` | close_call | c_clean, c_dedupe, c_mixed (poor: c_toolkit) | c_dedupe 0.38 | no_match | c_clean | lost | correct |
| `b_rbi_infra` | close_call | b_airflow, b_terraform (poor: b_demand) | b_terraform 0.71 | b_terraform | b_terraform | correct | correct |
| `b_asc_services` | close_call | w_go, w_graphql, w_go_de | w_go 0.58 | w_go | no_match | correct | lost |
| `b_fs_api_tests` | close_call | w_graphql, w_cypress | w_cypress 0.39 | no_match | w_cypress | lost | correct |
| `b_swbc_pipelines` | close_call | n_spark, n_dbt | n_spark 0.54 | n_spark | no_match | correct | lost |
| `b_mg_reports` | close_call | f_report, f_ab | f_report 0.42 | no_match | no_match | lost | lost |
| `c_fe_fairhaven` | no_match | no_match (poor: f_churn, f_ab, f_report) | no_match 1.00 | no_match | no_match | correct | correct |
| `c_hw_tess` | no_match | no_match (poor: t_ray, t_kafka, t_drift) | no_match 1.00 | no_match | no_match | correct | correct |
| `c_hw_north` | no_match | no_match (poor: n_spark, n_dbt, n_kafka) | no_match 1.00 | no_match | no_match | correct | correct |
| `c_fe_north` | no_match | no_match (poor: n_spark, n_dbt, n_kafka) | no_match 1.00 | no_match | n_dbt | correct | poor |
| `c_reactor_casc` | no_match | no_match (poor: c_clean, c_mixed, c_toolkit) | no_match 0.96 | no_match | no_match | correct | correct |
| `c_reactor_halc` | no_match | no_match (poor: hc_forecast, hc_dbt, hc_ab) | no_match 0.95 | no_match | no_match | correct | correct |
| `c_hw_halc` | no_match | no_match (poor: hc_forecast, hc_dbt, hc_ab) | no_match 0.99 | no_match | no_match | correct | correct |
| `c_esri_halden` | no_match | no_match (poor: h_grade, h_autograder, h_lab) | no_match 0.54 | no_match | h_autograder | correct | poor |
| `d_esri_classifier` | synonym | f_churn_syn (poor: f_report) | f_churn_syn 0.97 | f_churn_syn | no_match | correct | lost |
| `d_next_alerts` | synonym | t_drift_syn | t_ray 0.56 | t_ray | t_kafka | wrong | wrong |
| `d_tebra_backfill` | synonym | n_kafka_syn, n_spark | n_dbt 0.44 | no_match | n_dbt | lost | wrong |
| `d_rbi_scripted_env` | synonym | b_terraform_syn, b_airflow (poor: b_demand) | no_match 0.40 | no_match | no_match | lost | lost |
| `d_asc_query_layer` | synonym | w_graphql_syn, w_go | w_go 0.86 | w_go | no_match | correct | lost |
| `d_mg_reconcile` | synonym | c_clean_syn, c_mixed (poor: c_toolkit) | c_mixed 0.37 | no_match | no_match | lost | lost |
| `d_fs_webapp` | synonym | sp_app_syn | sp_app_syn 0.91 | sp_app_syn | no_match | correct | lost |
| `d_milw_unusual` | synonym | s_auto_syn | s_auto_syn 0.94 | s_auto_syn | no_match | correct | lost |
| `e_asc_grading` | keyword_trap | h_autograder (poor: h_grade) | h_autograder 0.58 | h_autograder | no_match | correct | lost |
| `e_fs_labs` | keyword_trap | h_autograder (poor: h_lab) | h_autograder 0.76 | h_autograder | h_autograder | correct | correct |
| `e_esri_terraform` | keyword_trap | b_demand (poor: b_terraform) | b_demand 0.89 | b_demand | b_terraform | correct | poor |
| `e_tebra_training` | keyword_trap | t_kafka | t_kafka 0.89 | t_kafka | t_drift | correct | wrong |
| `e_hw_autograder` | keyword_trap | no_match (poor: h_autograder, h_grade) | no_match 0.84 | no_match | h_autograder | correct | poor |
| `e_rbi_forecast` | keyword_trap | hc_dbt | hc_dbt 0.72 | hc_dbt | hc_dbt | correct | correct |
| `e_next_teaching` | keyword_trap | no_match (poor: h_grade, h_lab, h_autograder) | no_match 0.57 | no_match | h_autograder | correct | poor |
| `e_esri_go` | keyword_trap | no_match (poor: w_go, w_graphql, w_cypress) | no_match 0.87 | no_match | no_match | correct | correct |
| `f_esri_churn` | single | f_churn | f_churn 0.78 | f_churn | no_match | correct | lost |
| `f_tebra_connector` | single | fe_connector | fe_connector 0.54 | fe_connector | no_match | correct | lost |
| `f_fs_app` | single | sp_app | sp_app 0.71 | sp_app | no_match | correct | lost |
| `f_milw_auto` | single | s_auto | s_auto 0.92 | s_auto | no_match | correct | lost |
| `f_fe_churn` | single | no_match (poor: f_churn) | no_match 1.00 | no_match | no_match | correct | correct |
| `f_hw_connector` | single | no_match (poor: fe_connector) | no_match 1.00 | no_match | no_match | correct | correct |
| `f_reactor_chess` | single | no_match (poor: ch_parse) | no_match 0.98 | no_match | no_match | correct | correct |
| `f_hw_autoencoder` | single | no_match (poor: s_auto) | no_match 1.00 | no_match | no_match | correct | correct |

### An alternative rule, not shipped

The shipped rule picks Jev's argmax when it is a variant and its own probability is at least the threshold. When two good phrasings of one bullet are on offer Jev splits its mass between them, so neither reaches the threshold alone while `no_match` stays low. Gating on the mass Jev puts on *any* variant (one minus its `no_match`) instead, and picking its best variant, on the same answers:

| mass off no_match at least | correct | picks | right picks | poor-fit picks | other wrong picks | lost |
|---|---|---|---|---|---|---|
| 0.50 | 46/48 | 33 | 31 | 0 | 2 | 0 |
| 0.60 | 43/48 | 30 | 28 | 0 | 2 | 3 |
| 0.70 | 40/48 | 27 | 25 | 0 | 2 | 6 |
| 0.80 | 33/48 | 20 | 18 | 0 | 2 | 13 |

## Track baseline

36 cases. Fitted threshold **0.50** (floor 0.50). `correct` counts a right `none` as well as a right pick.

|  | correct | picks made | right picks | wrong picks | lost (said none though one fits) |
|---|---|---|---|---|---|
| Jev, its own argmax (no threshold) | 36/36 (100%) | 22 | 22 | 0 | 0 |
| **Jev at the threshold 0.50** | **35/36 (97%)** | 21 | 21 | 0 | 1 |
| The #229 fallback (role-family lookup) | 27/36 (75%) | 23 | 15 | 8 | 1 |

### By category

| category | cases | Jev argmax correct | Jev@τ correct | Jev@τ picks | Jev@τ wrong | fallback correct | fallback picks | fallback wrong |
|---|---|---|---|---|---|---|---|---|
| clear_match | 8 | 8/8 | 8/8 | 8 | 0 | 8/8 | 8 | 0 |
| hybrid_title | 7 | 7/7 | 6/7 | 6 | 0 | 7/7 | 7 | 0 |
| title_mismatch | 7 | 7/7 | 7/7 | 7 | 0 | 0/7 | 6 | 6 |
| no_track | 7 | 7/7 | 7/7 | 0 | 0 | 7/7 | 0 | 0 |
| near_miss | 7 | 7/7 | 7/7 | 0 | 0 | 5/7 | 2 | 2 |

### The threshold grid

At each grid value (Jev's own argmax kept only when its probability is at least the value): the picks made, the right ones, the wrong ones (the rule's first key), and the cases left as none.

| τ | picks | right picks | wrong picks | lost | correct overall |
|---|---|---|---|---|---|
| 0.50 ← | 21 | 21 | 0 | 1 | 35/36 |
| 0.55 | 21 | 21 | 0 | 1 | 35/36 |
| 0.60 | 21 | 21 | 0 | 1 | 35/36 |
| 0.65 | 21 | 21 | 0 | 1 | 35/36 |
| 0.70 | 20 | 20 | 0 | 2 | 34/36 |
| 0.75 | 20 | 20 | 0 | 2 | 34/36 |
| 0.80 | 18 | 18 | 0 | 4 | 32/36 |
| 0.85 | 17 | 17 | 0 | 5 | 31/36 |
| 0.90 | 17 | 17 | 0 | 5 | 31/36 |
| 0.95 | 13 | 13 | 0 | 9 | 27/36 |

Highest wrong pick by Jev: none; lowest right pick the fit keeps: 0.66; candidates with the same counts: 0.50, 0.55, 0.60, 0.65; the fit takes the one nearest the middle of the gap, ties to the higher.

### Disagreements between the label and Jev (0)


### Every case

| id | category | label | Jev (argmax, p) | Jev@τ | fallback | Jev@τ outcome | fallback outcome |
|---|---|---|---|---|---|---|---|
| `c_ds_mastercard` | clear_match | data_science | data_science 0.99 | data_science | data_science | correct | correct |
| `c_de_tebra` | clear_match | data_engineering | data_engineering 0.99 | data_engineering | data_engineering | correct | correct |
| `c_ml_nextdoor` | clear_match | machine_learning | machine_learning 0.99 | machine_learning | machine_learning | correct | correct |
| `c_swe_ascend` | clear_match | software_engineering | software_engineering 1.00 | software_engineering | software_engineering | correct | correct |
| `c_ds_mg` | clear_match | data_science | data_science 0.99 | data_science | data_science | correct | correct |
| `c_de_rbi` | clear_match | data_engineering | data_engineering 1.00 | data_engineering | data_engineering | correct | correct |
| `c_ml_milwaukee` | clear_match | machine_learning | machine_learning 0.98 | machine_learning | machine_learning | correct | correct |
| `c_swe_fullstack` | clear_match | software_engineering | software_engineering 1.00 | software_engineering | software_engineering | correct | correct |
| `h_ds_ml_cathexis` | hybrid_title | data_science or machine_learning | machine_learning 0.77 | machine_learning | machine_learning | correct | correct |
| `h_de_ds_capgemini` | hybrid_title | data_engineering or data_science | data_engineering 0.48 | none | data_engineering | lost | correct |
| `h_analytics_eng_doordash` | hybrid_title | data_science or data_engineering | data_engineering 0.90 | data_engineering | data_engineering | correct | correct |
| `h_data_ai_arizent` | hybrid_title | data_engineering or machine_learning | data_engineering 0.84 | data_engineering | machine_learning | correct | correct |
| `h_swe_data_roblox` | hybrid_title | data_engineering or software_engineering | data_engineering 0.99 | data_engineering | software_engineering | correct | correct |
| `h_ml_ds` | hybrid_title | data_science or machine_learning | data_science 0.66 | data_science | machine_learning | correct | correct |
| `h_swe_ml_platform` | hybrid_title | software_engineering or machine_learning | software_engineering 0.75 | software_engineering | machine_learning | correct | correct |
| `m_analyst_pipelines` | title_mismatch | data_engineering | data_engineering 1.00 | data_engineering | data_science | correct | wrong |
| `m_swe_models` | title_mismatch | machine_learning | machine_learning 0.93 | machine_learning | software_engineering | correct | wrong |
| `m_mle_webapps` | title_mismatch | software_engineering | software_engineering 0.97 | software_engineering | machine_learning | correct | wrong |
| `m_scientist_etl` | title_mismatch | data_engineering | data_engineering 1.00 | data_engineering | data_science | correct | wrong |
| `m_research_ranking` | title_mismatch | machine_learning | machine_learning 0.90 | machine_learning | none | correct | lost |
| `m_analytics_eng_stats` | title_mismatch | data_science | data_science 0.98 | data_science | data_engineering | correct | wrong |
| `m_data_engineer_services` | title_mismatch | software_engineering | software_engineering 0.94 | software_engineering | data_engineering | correct | wrong |
| `n_product_manager` | no_track | none | none 1.00 | none | none | correct | correct |
| `n_ux_designer` | no_track | none | none 1.00 | none | none | correct | correct |
| `n_timing_analysis` | no_track | none | none 1.00 | none | none | correct | correct |
| `n_security_analyst` | no_track | none | none 1.00 | none | none | correct | correct |
| `n_account_exec` | no_track | none | none 1.00 | none | none | correct | correct |
| `n_mechanical` | no_track | none | none 1.00 | none | none | correct | correct |
| `n_recruiter` | no_track | none | none 0.98 | none | none | correct | correct |
| `x_data_annotation` | near_miss | none | none 0.97 | none | none | correct | correct |
| `x_data_governance` | near_miss | none | none 0.92 | none | none | correct | correct |
| `x_solutions_engineer` | near_miss | none | none 0.82 | none | data_engineering | correct | wrong |
| `x_tech_writer_ml` | near_miss | none | none 0.78 | none | machine_learning | correct | wrong |
| `x_it_support` | near_miss | none | none 1.00 | none | none | correct | correct |
| `x_qa_manual` | near_miss | none | none 0.99 | none | none | correct | correct |
| `x_financial_analyst` | near_miss | none | none 0.99 | none | none | correct | correct |

## What the comparison does and does not say

The fallback is run on the same cases, with the job's text as its title and requirements (a real posting has more words, and the overlap pick sees all of them). The role-family lookup sees only the title, which is what it uses in production; its track names here are the role families (`data_science`, `machine_learning`, ...), the best case for it. The cases are synthetic and the labels are one planner's, pending the user's spot-check: read the numbers as a comparison between two methods on the same cases, not as an accuracy to expect.

