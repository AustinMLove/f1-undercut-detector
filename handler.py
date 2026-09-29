"""
Lambda entry point for the undercut detector pipeline.

Triggered by an S3 event notification on new objects under raw/. Mirrors
local_run.py's logic exactly, with S3 reads/writes in place of local
filesystem I/O — find_candidates() itself is called completely unchanged.
"""
import json
from io import StringIO

import boto3
import pandas as pd

from candidate_search import find_candidates

s3 = boto3.client("s3")

# Columns FastF1 represents as Timedelta. CSV round-tripping loses that
# type info - read_csv() returns these as plain strings unless explicitly
# converted back, and find_candidates() depends on Timedelta behavior
# (.total_seconds(), comparisons) throughout.
TIMEDELTA_COLUMNS = [
    "Time", "LapTime", "PitInTime", "PitOutTime",
    "Sector1Time", "Sector2Time", "Sector3Time",
    "Sector1SessionTime", "Sector2SessionTime", "Sector3SessionTime",
    "LapStartTime",
]


def lambda_handler(event, context):
    record = event["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    key = record["s3"]["object"]["key"]  # e.g. "raw/2024_Bahrain/laps.csv"

    # key layout is raw/<year>_<race>/laps.csv - pull the middle segment
    # to recover the race label and to mirror it in the processed/ key.
    race_dir = key.split("/")[1]  # "2024_Bahrain"
    race_label = race_dir.replace("_", " ")  # "2024 Bahrain"

    obj = s3.get_object(Bucket=bucket, Key=key)
    csv_text = obj["Body"].read().decode("utf-8")
    laps = pd.read_csv(StringIO(csv_text))

    for col in TIMEDELTA_COLUMNS:
        if col in laps.columns:
            laps[col] = pd.to_timedelta(laps[col])

    laps["TrackStatus"] = laps["TrackStatus"].astype(str)
    
    candidates = find_candidates(laps, race_label)

    processed_key = f"processed/{race_dir}/candidates.json"
    s3.put_object(
        Bucket=bucket,
        Key=processed_key,
        Body=json.dumps(candidates, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    return {
        "statusCode": 200,
        "body": f"Wrote {len(candidates)} candidate(s) to {processed_key}",
    }