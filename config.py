"""
Config via environment variables — even locally.

Lambda has no other way to pass configuration in, so building this habit
now means zero changes needed when this code moves into a Lambda handler:
env vars set in the Lambda console instead of a local shell, same
config.py reading them the same way.

Locally, export these in your shell before running, or set them in a
.env file if you're using one — this file itself doesn't load .env,
it just reads whatever's already in the environment.
"""
import os

# Where raw/processed data lives once this moves to S3. Locally this
# name is unused (local_run.py writes to plain folders), but keeping it
# defined now means handler.py can read it on day one without a new file.
DATA_BUCKET = os.environ.get("F1_DATA_BUCKET", "local-dev-bucket")

# Prefixes inside the bucket for each stage of the pipeline. These are
# also the local folder names local_run.py writes to, so what you
# verify locally is literally the same key layout handler.py will see.
RAW_PREFIX = os.environ.get("F1_RAW_PREFIX", "raw")
PROCESSED_PREFIX = os.environ.get("F1_PROCESSED_PREFIX", "processed")

# FastF1's local cache. Only relevant for local development — Lambda
# has no persistent disk between invocations, so this is meaningless
# there and handler.py won't reference it.
FASTF1_CACHE_DIR = os.environ.get("FASTF1_CACHE_DIR", ".fastf1_cache")