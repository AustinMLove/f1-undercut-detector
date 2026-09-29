"""
Local development entry point for the undercut detector pipeline.

Mirrors what handler.py will do in Lambda:
    1. Load a race (locally via FastF1's load_race(); in Lambda, read
       the raw object from S3 instead).
    2. Run find_candidates() completely unchanged.
    3. Write the result out (locally to a folder mirroring the S3 key
       layout; in Lambda, to the processed/ prefix in S3).

Writing locally using the exact same raw/processed prefix layout as S3
means what we verify here — does the raw CSV look right, does the
processed JSON look right — is testing the real shape handler.py will
produce, not a stand-in for it.

Requires: pip install fastf1 pandas
"""
import json
import os

import config
from candidate_search import load_race, find_candidates


def save_raw_locally(laps, year: int, race: str) -> str:
    """
    Saves raw laps to a local path mirroring the S3 key layout:
    raw/<year>_<race>/laps.csv. Once uploaded to S3 at that same key,
    handler.py's event trigger fires on it — that's the whole reason
    the layout matches now instead of later.
    """
    out_dir = os.path.join(config.RAW_PREFIX, f"{year}_{race.replace(' ', '_')}")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "laps.csv")
    laps.to_csv(out_path, index=False)
    return out_path


def save_processed_locally(candidates: list, year: int, race: str) -> str:
    """
    Saves find_candidates() output to a local path mirroring the S3 key
    layout: processed/<year>_<race>/candidates.json.
    """
    out_dir = os.path.join(config.PROCESSED_PREFIX, f"{year}_{race.replace(' ', '_')}")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "candidates.json")
    with open(out_path, "w") as f:
        json.dump(candidates, f, indent=2)
    return out_path


if __name__ == "__main__":
    YEAR = 2025
    RACE = "Spain"
    RACE_LABEL = f"{YEAR} {RACE}"

    print(f"Loading {RACE_LABEL} via FastF1...")
    laps = load_race(YEAR, RACE)

    raw_path = save_raw_locally(laps, YEAR, RACE)
    print(f"Saved raw laps to {raw_path}")

    print("Running find_candidates()...")
    candidates = find_candidates(laps, RACE_LABEL)

    processed_path = save_processed_locally(candidates, YEAR, RACE)
    print(f"Saved {len(candidates)} candidate(s) to {processed_path}")