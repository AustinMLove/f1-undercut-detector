"""
Validation diagnostic for the undercut detector.

Runs find_candidates() across a set of races and scores its break_even_laps
figure against what actually happened: for each quantified case, compares
how many laps the rival really stayed out (N_actual) to the predicted
break-even, then checks whether the undercut succeeded.

Findings as of Phase 0.7d, across 12 scoreable cases (2024-2025 Bahrain,
Saudi Arabia, Spain):

  - Treated as a binary predictor, break_even_laps scored 66.7%. A trivial
    always-predict-success baseline scored 83.3% on the same cases. The
    model does not beat guessing.

  - The errors are asymmetric. Where the rival stayed out at least
    break_even_laps, the undercut succeeded 7 times out of 8. Where they
    did not, it still succeeded 3 times out of 4. Positive indications
    carry information; negative ones do not.

  - Margin (N_actual minus break_even_laps) correlates with outcome
    magnitude at -0.26 across all cases, or -0.64 excluding 2024 Spain
    RUS/NOR, where a car-pace difference dominated tire age. The
    relationship holds where the model's assumptions hold.

These findings are why the tool reports break-even as context rather than
as a recommendation. This script is the record of how that was established.

Two races in the default set (2025 China, 2024 Miami) produce no quantified
cases — their degradation fits fail compute_relative_degradation()'s
thresholds. That is the intended behaviour, not a failure to run.
"""
from f1_pipeline.process import get_gap_at_lap
from candidate_search import load_race,  find_candidates


def check_race(year, race):
    """
    Scores the detector's break_even_laps against real outcomes for one race.

    For each quantified case, finds the rival's next pit stop, measures how
    many laps they actually stayed out, and checks whether the undercut
    succeeded. Prints a per-case line marked correct or incorrect.

    Cases are excluded rather than scored when the rival never pitted again
    (the model assumes both drivers pay a comparable pit-lane cost one after
    the other, which fails if the rival's stop never comes) or when lap data
    is missing after the stop.

    The outcome gap is read at rival_pit_lap + 2, not + 1. FastF1 records
    pit-lane transit on the out-lap, so at + 1 the rival is still absorbing
    their stop while the pitting driver is already a lap into clean running
    — which inflates the gap in the pitting driver's favour and made
    undercuts look more successful than they were. + 2 is the first lap
    where both cars are on green tires at temperature.

    Note the rival is still slightly off pace at + 2, since cold tires cost
    real time on a first flying lap beyond pit transit. Small positive gaps
    at this offset should not be read as conclusive.

    Args:
        year: Season year, e.g. 2024.
        race: Race name as FastF1 expects it, e.g. "Bahrain".
    """
    laps = load_race(year, race)
    insights = find_candidates(laps, f"{year} {race}")
    quantified = [i for i in insights if i["status"] == "quantified"]

    print(f"\n{year} {race}: {len(insights)} detected, {len(quantified)} quantified")

    for i in quantified:
        rival_pits = laps[
            (laps["Driver"] == i["rival"])
            & (laps["PitInTime"].notna())
            & (laps["LapNumber"] > i["pit_lap"])
        ].sort_values("LapNumber")

        if rival_pits.empty:
            print(f"  {i['driver']} vs {i['rival']}: rival never pitted again — excluded")
            continue

        rival_pit_lap = int(rival_pits.iloc[0]["LapNumber"])
        n_actual = rival_pit_lap - i["pit_lap"]

        try:
            gap_after = get_gap_at_lap(laps, i["driver"], i["rival"], rival_pit_lap + 2)
        except ValueError:
            print(f"  {i['driver']} vs {i['rival']}: no data after stop — excluded")
            continue

        worked = gap_after < 0
        predicted = n_actual >= i["break_even_laps"]
        mark = "✓" if worked == predicted else "✗"

        print(f"  {mark} {i['driver']} vs {i['rival']}: "
              f"N_actual {n_actual}, break_even {i['break_even_laps']}, "
              f"gap after {gap_after:+.3f}, {'worked' if worked else 'failed'}")


if __name__ == "__main__":
    for year, race in [(2024, "Bahrain"), (2025, "Saudi Arabia"), (2025, "China"),
                   (2024, "Miami"), (2025, "Spain"), (2024, "Spain")]:
        check_race(year, race)