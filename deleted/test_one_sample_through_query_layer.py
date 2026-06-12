from scripts.app_queries import list_pipeline_runs, list_run_samples

run = list_pipeline_runs(limit=1)[0]
print(run["experiment_pipeline_run_id"], run["experiment_slug"], run["status"])

samples = list_run_samples(run["experiment_pipeline_run_id"])
print("samples:", len(samples))
print(samples[0]["sample_id"])
print(samples[0]["final_response"][:500])