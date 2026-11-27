import os

# Before app.main is imported: never start the real engine (it would act on
# whatever cluster the local kubeconfig points at), and use a known token.
os.environ["SCENARIO_ENGINE"] = "off"
os.environ.setdefault("API_TOKEN", "test-token")
