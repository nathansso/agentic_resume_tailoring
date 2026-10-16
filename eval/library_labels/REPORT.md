# Bullet library: Jev variant choice and track baseline (issue #199)

Recorded 2026-10-01 with jev-1.13.0 (84 decisions, 51119 input and 9197 output tokens); questions variant_choice@v1, track_baseline@v1; replay hit rate 100%. Labels: planner-reviewed, pending user spot-check.

Both thresholds are fitted on Jev's own answers alone, as if no code rule existed (the host's explicit `role_family` winning, the drift guard and the fallbacks): they only ever remove or soften a pick. Floor 0.50 for both. Shipped: `TAU_VARIANT` 0.50, `TAU_BASELINE` 0.50; the fit recommends 0.50 and 0.50.

## Variant choice

48 cases. Fitted threshold **0.50** (floor 0.50), gating the probability Jev puts on any variant (one minus its `no_match`). `correct` counts a right `no_match` as well as a right pick.

|  | correct | picks made | right picks | poor-fit picks | lost (said none though one fits) |
|---|---|---|---|---|---|
| Jev's own choice (argmax, no threshold) | 45/48 (94%) | 31 | 30 | 0 | 2 |
| **Jev at the threshold 0.50** | **47/48 (98%)** | 33 | 32 | 0 | 0 |
| The Jev-free fallback (concept fit) | 41/48 (85%) | 27 | 26 | 0 | 6 |

Wrong picks that are not poor-fit: Jev at the threshold 1, the fallback 1.

### By category

| category | cases | Jev's own choice correct | Jev@τ correct | Jev@τ picks | Jev@τ poor | fallback correct | fallback picks | fallback poor |
|---|---|---|---|---|---|---|---|---|
| clear_best | 8 | 7/8 | 8/8 | 8 | 0 | 6/8 | 6 | 0 |
| close_call | 8 | 8/8 | 8/8 | 8 | 0 | 6/8 | 7 | 0 |
| no_match | 8 | 8/8 | 8/8 | 0 | 0 | 8/8 | 0 | 0 |
| synonym | 8 | 6/8 | 7/8 | 8 | 0 | 7/8 | 7 | 0 |
| keyword_trap | 8 | 8/8 | 8/8 | 5 | 0 | 6/8 | 3 | 0 |
| single | 8 | 8/8 | 8/8 | 4 | 0 | 8/8 | 4 | 0 |

### The threshold grid

At each grid value (the likeliest variant, kept only when the mass off `no_match` is at least the value): the picks made, the right ones, the poor-fit ones (the rule's first key), and the cases left as none.

| τ | picks | right picks | poor-fit picks | other wrong picks | lost | correct overall |
|---|---|---|---|---|---|---|
| 0.50 ← | 33 | 32 | 0 | 1 | 0 | 47/48 |
| 0.55 | 31 | 30 | 0 | 1 | 2 | 45/48 |
| 0.60 | 30 | 29 | 0 | 1 | 3 | 44/48 |
| 0.65 | 27 | 26 | 0 | 1 | 6 | 41/48 |
| 0.70 | 27 | 26 | 0 | 1 | 6 | 41/48 |
| 0.75 | 22 | 21 | 0 | 1 | 11 | 36/48 |
| 0.80 | 20 | 19 | 0 | 1 | 13 | 34/48 |
| 0.85 | 14 | 14 | 0 | 0 | 19 | 29/48 |
| 0.90 | 11 | 11 | 0 | 0 | 22 | 26/48 |
| 0.95 | 5 | 5 | 0 | 0 | 28 | 20/48 |

Highest poor-fit pick by Jev: `c_esri_halden` at 0.46; lowest right pick the fit keeps: 0.51; candidates with the same counts: 0.50; the fit takes the one nearest the middle of the gap, ties to the higher.

### Disagreements between the label and Jev (1)

Jev's answer here is its pick at the floor: the likeliest variant when the mass off `no_match` is at least 0.50.

| id | category | label | Jev (own choice, p) | Jev pick (mass) | Jev distribution | fallback |
|---|---|---|---|---|---|---|
| `d_tebra_backfill` | synonym | n_kafka_syn, n_spark | n_dbt 0.44 | n_dbt (0.83) | n_dbt 0.44, n_spark 0.33, no_match 0.17 | n_spark |

### Every case

| id | category | label | Jev (own choice, p) | mass off no_match | Jev@τ | fallback | Jev@τ outcome | fallback outcome |
|---|---|---|---|---|---|---|---|---|
| `a_esri_churn` | clear_best | f_churn (poor: f_report) | f_churn 0.97 | 0.97 | f_churn | f_churn | correct | correct |
| `a_mg_dashboard` | clear_best | ce_dash | no_match 0.49 | 0.51 | ce_dash | ce_dash | correct | correct |
| `a_tebra_connector` | clear_best | fe_connector | fe_connector 0.81 | 0.82 | fe_connector | fe_connector | correct | correct |
| `a_milw_autoencoder` | clear_best | s_auto | s_auto 0.95 | 0.95 | s_auto | s_auto | correct | correct |
| `a_fs_app` | clear_best | sp_app | sp_app 0.84 | 0.85 | sp_app | sp_app | correct | correct |
| `a_fe_app` | clear_best | sp_app (poor: sp_deploy) | sp_app 0.56 | 0.57 | sp_app | sp_app | correct | correct |
| `a_asc_autograder` | clear_best | h_autograder (poor: h_grade, h_lab) | h_autograder 0.56 | 0.61 | h_autograder | no_match | correct | lost |
| `a_swbc_clean` | clear_best | c_clean (poor: c_mixed) | c_clean 0.98 | 0.98 | c_clean | no_match | correct | lost |
| `b_esri_forecast` | close_call | hc_forecast, hc_forecast_ml (poor: hc_dbt) | hc_forecast 0.72 | 0.97 | hc_forecast | hc_forecast_ml | correct | correct |
| `b_next_forecast` | close_call | hc_forecast, hc_forecast_ml (poor: hc_dbt) | hc_forecast_ml 0.35 | 0.79 | hc_forecast_ml | hc_forecast_ml | correct | correct |
| `b_mg_data` | close_call | c_clean, c_dedupe, c_mixed (poor: c_toolkit) | c_dedupe 0.38 | 0.74 | c_dedupe | no_match | correct | lost |
| `b_rbi_infra` | close_call | b_airflow, b_terraform (poor: b_demand) | b_terraform 0.71 | 0.83 | b_terraform | b_airflow | correct | correct |
| `b_asc_services` | close_call | w_go, w_graphql, w_go_de | w_go 0.58 | 0.88 | w_go | w_graphql | correct | correct |
| `b_fs_api_tests` | close_call | w_graphql, w_cypress | w_cypress 0.39 | 0.73 | w_cypress | w_graphql | correct | correct |
| `b_swbc_pipelines` | close_call | n_spark, n_dbt | n_spark 0.54 | 0.89 | n_spark | n_kafka | correct | wrong |
| `b_mg_reports` | close_call | f_report, f_ab | f_report 0.42 | 0.71 | f_report | f_report | correct | correct |
| `c_fe_fairhaven` | no_match | no_match (poor: f_churn, f_ab, f_report) | no_match 1.00 | 0.00 | no_match | no_match | correct | correct |
| `c_hw_tess` | no_match | no_match (poor: t_ray, t_kafka, t_drift) | no_match 1.00 | 0.00 | no_match | no_match | correct | correct |
| `c_hw_north` | no_match | no_match (poor: n_spark, n_dbt, n_kafka) | no_match 1.00 | 0.00 | no_match | no_match | correct | correct |
| `c_fe_north` | no_match | no_match (poor: n_spark, n_dbt, n_kafka) | no_match 1.00 | 0.00 | no_match | no_match | correct | correct |
| `c_reactor_casc` | no_match | no_match (poor: c_clean, c_mixed, c_toolkit) | no_match 0.96 | 0.04 | no_match | no_match | correct | correct |
| `c_reactor_halc` | no_match | no_match (poor: hc_forecast, hc_dbt, hc_ab) | no_match 0.95 | 0.05 | no_match | no_match | correct | correct |
| `c_hw_halc` | no_match | no_match (poor: hc_forecast, hc_dbt, hc_ab) | no_match 0.99 | 0.01 | no_match | no_match | correct | correct |
| `c_esri_halden` | no_match | no_match (poor: h_grade, h_autograder, h_lab) | no_match 0.54 | 0.46 | no_match | no_match | correct | correct |
| `d_esri_classifier` | synonym | f_churn_syn (poor: f_report) | f_churn_syn 0.97 | 0.97 | f_churn_syn | f_churn_syn | correct | correct |
| `d_next_alerts` | synonym | t_drift_syn, t_ray | t_ray 0.56 | 0.80 | t_ray | t_drift_syn | correct | correct |
| `d_tebra_backfill` | synonym | n_kafka_syn, n_spark | n_dbt 0.44 | 0.83 | n_dbt | n_spark | wrong | correct |
| `d_rbi_scripted_env` | synonym | b_terraform_syn, b_airflow (poor: b_demand) | no_match 0.40 | 0.60 | b_airflow | b_airflow | correct | correct |
| `d_asc_query_layer` | synonym | w_graphql_syn, w_go | w_go 0.86 | 0.93 | w_go | w_graphql_syn | correct | correct |
| `d_mg_reconcile` | synonym | c_clean_syn, c_mixed (poor: c_toolkit) | c_mixed 0.37 | 0.74 | c_mixed | no_match | correct | lost |
| `d_fs_webapp` | synonym | sp_app_syn | sp_app_syn 0.91 | 0.91 | sp_app_syn | sp_app_syn | correct | correct |
| `d_milw_unusual` | synonym | s_auto_syn | s_auto_syn 0.94 | 0.94 | s_auto_syn | s_auto_syn | correct | correct |
| `e_asc_grading` | keyword_trap | h_autograder (poor: h_grade) | h_autograder 0.58 | 0.62 | h_autograder | no_match | correct | lost |
| `e_fs_labs` | keyword_trap | h_autograder (poor: h_lab) | h_autograder 0.76 | 0.80 | h_autograder | no_match | correct | lost |
| `e_esri_terraform` | keyword_trap | b_demand (poor: b_terraform) | b_demand 0.89 | 0.90 | b_demand | b_demand | correct | correct |
| `e_tebra_training` | keyword_trap | t_kafka | t_kafka 0.89 | 0.90 | t_kafka | t_kafka | correct | correct |
| `e_hw_autograder` | keyword_trap | no_match (poor: h_autograder, h_grade) | no_match 0.84 | 0.16 | no_match | no_match | correct | correct |
| `e_rbi_forecast` | keyword_trap | hc_dbt | hc_dbt 0.72 | 0.82 | hc_dbt | hc_dbt | correct | correct |
| `e_next_teaching` | keyword_trap | no_match (poor: h_grade, h_lab, h_autograder) | no_match 0.57 | 0.43 | no_match | no_match | correct | correct |
| `e_esri_go` | keyword_trap | no_match (poor: w_go, w_graphql, w_cypress) | no_match 0.87 | 0.13 | no_match | no_match | correct | correct |
| `f_esri_churn` | single | f_churn | f_churn 0.78 | 0.78 | f_churn | f_churn | correct | correct |
| `f_tebra_connector` | single | fe_connector | fe_connector 0.54 | 0.54 | fe_connector | fe_connector | correct | correct |
| `f_fs_app` | single | sp_app | sp_app 0.71 | 0.71 | sp_app | sp_app | correct | correct |
| `f_milw_auto` | single | s_auto | s_auto 0.92 | 0.92 | s_auto | s_auto | correct | correct |
| `f_fe_churn` | single | no_match (poor: f_churn) | no_match 1.00 | 0.00 | no_match | no_match | correct | correct |
| `f_hw_connector` | single | no_match (poor: fe_connector) | no_match 1.00 | 0.00 | no_match | no_match | correct | correct |
| `f_reactor_chess` | single | no_match (poor: ch_parse) | no_match 0.98 | 0.02 | no_match | no_match | correct | correct |
| `f_hw_autoencoder` | single | no_match (poor: s_auto) | no_match 1.00 | 0.00 | no_match | no_match | correct | correct |

### The first design (gating Jev's own choice), not shipped

The first design picked Jev's own choice when it was a variant and its own probability reached the threshold. When two good phrasings of one bullet are on offer Jev splits its mass between them, so neither reached the threshold alone while `no_match` stayed low, and the close calls were lost. The shipped rule gates the mass off `no_match` instead. Both on the same answers and labels:

| τ | first design: correct | picks | right picks | poor-fit picks | shipped: correct | picks | right picks | poor-fit picks |
|---|---|---|---|---|---|---|---|---|
| 0.50 | 40/48 | 25 | 25 | 0 | 47/48 | 33 | 32 | 0 |
| 0.60 | 33/48 | 18 | 18 | 0 | 44/48 | 30 | 29 | 0 |
| 0.70 | 33/48 | 18 | 18 | 0 | 41/48 | 27 | 26 | 0 |
| 0.80 | 27/48 | 12 | 12 | 0 | 34/48 | 20 | 19 | 0 |

## Track baseline

36 cases. Fitted threshold **0.50** (floor 0.50), gating the probability of Jev's own choice. `correct` counts a right `none` as well as a right pick.

|  | correct | picks made | right picks | wrong picks | lost (said none though one fits) |
|---|---|---|---|---|---|
| Jev's own choice (argmax, no threshold) | 36/36 (100%) | 22 | 22 | 0 | 0 |
| **Jev at the threshold 0.50** | **35/36 (97%)** | 21 | 21 | 0 | 1 |
| The Jev-free fallback (duties fit) | 36/36 (100%) | 22 | 22 | 0 | 0 |

### By category

| category | cases | Jev's own choice correct | Jev@τ correct | Jev@τ picks | Jev@τ wrong | fallback correct | fallback picks | fallback wrong |
|---|---|---|---|---|---|---|---|---|
| clear_match | 8 | 8/8 | 8/8 | 8 | 0 | 8/8 | 8 | 0 |
| hybrid_title | 7 | 7/7 | 6/7 | 6 | 0 | 7/7 | 7 | 0 |
| title_mismatch | 7 | 7/7 | 7/7 | 7 | 0 | 7/7 | 7 | 0 |
| no_track | 7 | 7/7 | 7/7 | 0 | 0 | 7/7 | 0 | 0 |
| near_miss | 7 | 7/7 | 7/7 | 0 | 0 | 7/7 | 0 | 0 |

### The threshold grid

At each grid value (Jev's own choice, kept only when its probability is at least the value): the picks made, the right ones, the wrong ones (the rule's first key), and the cases left as none.

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

Jev's answer here is its own choice, whatever its probability.


### Every case

| id | category | label | Jev (own choice, p) | - | Jev@τ | fallback | Jev@τ outcome | fallback outcome |
|---|---|---|---|---|---|---|---|---|
| `c_ds_mastercard` | clear_match | data_science | data_science 0.99 | - | data_science | data_science | correct | correct |
| `c_de_tebra` | clear_match | data_engineering | data_engineering 0.99 | - | data_engineering | data_engineering | correct | correct |
| `c_ml_nextdoor` | clear_match | machine_learning | machine_learning 0.99 | - | machine_learning | machine_learning | correct | correct |
| `c_swe_ascend` | clear_match | software_engineering | software_engineering 1.00 | - | software_engineering | software_engineering | correct | correct |
| `c_ds_mg` | clear_match | data_science | data_science 0.99 | - | data_science | data_science | correct | correct |
| `c_de_rbi` | clear_match | data_engineering | data_engineering 1.00 | - | data_engineering | data_engineering | correct | correct |
| `c_ml_milwaukee` | clear_match | machine_learning | machine_learning 0.98 | - | machine_learning | machine_learning | correct | correct |
| `c_swe_fullstack` | clear_match | software_engineering | software_engineering 1.00 | - | software_engineering | software_engineering | correct | correct |
| `h_ds_ml_cathexis` | hybrid_title | data_science or machine_learning | machine_learning 0.77 | - | machine_learning | data_science | correct | correct |
| `h_de_ds_capgemini` | hybrid_title | data_engineering or data_science | data_engineering 0.48 | - | none | data_science | lost | correct |
| `h_analytics_eng_doordash` | hybrid_title | data_science or data_engineering | data_engineering 0.90 | - | data_engineering | data_science | correct | correct |
| `h_data_ai_arizent` | hybrid_title | data_engineering or machine_learning | data_engineering 0.84 | - | data_engineering | data_engineering | correct | correct |
| `h_swe_data_roblox` | hybrid_title | data_engineering or software_engineering | data_engineering 0.99 | - | data_engineering | data_engineering | correct | correct |
| `h_ml_ds` | hybrid_title | data_science or machine_learning | data_science 0.66 | - | data_science | data_science | correct | correct |
| `h_swe_ml_platform` | hybrid_title | software_engineering or machine_learning | software_engineering 0.75 | - | software_engineering | software_engineering | correct | correct |
| `m_analyst_pipelines` | title_mismatch | data_engineering | data_engineering 1.00 | - | data_engineering | data_engineering | correct | correct |
| `m_swe_models` | title_mismatch | machine_learning | machine_learning 0.93 | - | machine_learning | machine_learning | correct | correct |
| `m_mle_webapps` | title_mismatch | software_engineering | software_engineering 0.97 | - | software_engineering | software_engineering | correct | correct |
| `m_scientist_etl` | title_mismatch | data_engineering | data_engineering 1.00 | - | data_engineering | data_engineering | correct | correct |
| `m_research_ranking` | title_mismatch | machine_learning | machine_learning 0.90 | - | machine_learning | machine_learning | correct | correct |
| `m_analytics_eng_stats` | title_mismatch | data_science | data_science 0.98 | - | data_science | data_science | correct | correct |
| `m_data_engineer_services` | title_mismatch | software_engineering | software_engineering 0.94 | - | software_engineering | software_engineering | correct | correct |
| `n_product_manager` | no_track | none | none 1.00 | - | none | none | correct | correct |
| `n_ux_designer` | no_track | none | none 1.00 | - | none | none | correct | correct |
| `n_timing_analysis` | no_track | none | none 1.00 | - | none | none | correct | correct |
| `n_security_analyst` | no_track | none | none 1.00 | - | none | none | correct | correct |
| `n_account_exec` | no_track | none | none 1.00 | - | none | none | correct | correct |
| `n_mechanical` | no_track | none | none 1.00 | - | none | none | correct | correct |
| `n_recruiter` | no_track | none | none 0.98 | - | none | none | correct | correct |
| `x_data_annotation` | near_miss | none | none 0.97 | - | none | none | correct | correct |
| `x_data_governance` | near_miss | none | none 0.92 | - | none | none | correct | correct |
| `x_solutions_engineer` | near_miss | none | none 0.82 | - | none | none | correct | correct |
| `x_tech_writer_ml` | near_miss | none | none 0.78 | - | none | none | correct | correct |
| `x_it_support` | near_miss | none | none 1.00 | - | none | none | correct | correct |
| `x_qa_manual` | near_miss | none | none 0.99 | - | none | none | correct | correct |
| `x_financial_analyst` | near_miss | none | none 0.99 | - | none | none | correct | correct |

## What the comparison does and does not say

The fallbacks (#352) are run on the same cases, with the job's title and requirements as the production picks read them. Their vocabulary was written by hand with these labels in view and their floors and margin were fitted on them, so the fallback rows are a fit and not a held-out figure; `FALLBACK_FIT.md` sets leave-one-out and a separate author-written holdout beside them. The track names here are the role families (`data_science`, `machine_learning`, ...), the best case for the pick. The cases are synthetic and the labels are one planner's, pending the user's spot-check: read the numbers as a comparison between two methods on the same cases, not as an accuracy to expect.

