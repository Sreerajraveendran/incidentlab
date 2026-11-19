import os

# Set before the app is imported: these are read at import time.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("REDIS_URL", None)
os.environ["API_TOKEN"] = "test-token"
os.environ["SIMULATION_ENABLED"] = "true"
