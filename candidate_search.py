"""
Standalone tool for finding undercut/overcut backtest candidates in any
race session. Not a part of the f1_pipeline.

Usage:
    python candidate_search.py

Or import find_candidates() to use interactively.
"""
import os
from f1_pipeline.process import (
    compute_relative_degradation,
    get_gap_at_lap,
    get_tyre_age_at_lap,
    quantify_undercut,
)

def load_race(year, race, session_type="R"):
    """
    Loads a FastF1 session and returns its laps DataFrame

    The only I/O in this module's detection path. Everything downstream
    takes a DataFrame, so Phase 1's Lambda can build one from an S3
    object and call find_candidates() unchanged.
    """
    import fastf1
    
    os.makedirs(".fastf1_cache", exist_ok=True)
    fastf1.Cache.enable_cache(".fastf1_cache")
    session = fastf1.get_session(year, race, session_type)
    session.load()
    return session.laps


def find_candidates(
        laps,
        race_label,
        min_lap=15,
        max_gap=3.0,
        stability_offset=3,
        stability_max_gap=8.0,
        max_fresher_tyre_delta=-8
        ):
    """
    Detects undercut opportunities in a race and quantifies what each one
    depends on.

    For every pit stop in the race, checks whether a rival was close enough
    at the decision point to make the stop a strategic response rather than
    a routine tire change. Surviving candidates are passed to
    quantify_undercut(), which reports how many laps the rival must stay out
    before the pitting driver's fresh-tire advantage overcomes the gap.

    Takes an already-loaded DataFrame rather than fetching a session, so the
    detection path has no FastF1 dependency and can be fed from any source
    (see load_race() for local use).

    Filters, in order applied:
      1. Pit stops before min_lap are skipped.
      2. The rival must not be on their own in-lap or out-lap at the
         decision lap — their cumulative Time would be distorted by
         pit-lane transit, producing false proximity.
      3. The pitting driver must be *behind* the rival. If they are already
         ahead, this is a cover stop or a routine change, not an undercut.
      4. The gap must be under max_gap.
      5. The gap must also have been reasonably small stability_offset laps
         earlier, ruling out one-lap coincidences.
      6. The rival must not be on substantially fresher tires, per
         max_fresher_tyre_delta. Candidates failing this are still returned,
         marked "not_applicable" rather than dropped.

    Args:
        laps: A DataFrame matching FastF1's session.laps structure.
        race_label: Human-readable race identifier for the output, e.g.
                    "2024 Bahrain". The detector itself is season-agnostic.
        min_lap: Skip pit stops before this lap. Defaults to 15. Early-race
                 stops are usually reactive (damage, a bad start, an early
                 Safety Car) rather than strategic, and the field is still
                 bunched enough that proximity carries little meaning.
                 Note: this parameter originally existed to avoid the
                 negative degradation values that within-stint regression
                 produced in opening stints. That confound is now handled
                 at its source by compute_relative_degradation(), so the
                 justification above is the one that still applies.
        max_gap: Maximum gap, in seconds, for a rival to count as a genuine
                 strategic threat. Defaults to 3.0.
        stability_offset: How many laps before the decision lap to take a
                          second, independent gap reading. Defaults to 3.
        stability_max_gap: Looser threshold for that earlier reading, since
                           gaps drift over a few laps even in a real
                           battle. Defaults to 8.0.
        max_fresher_tyre_delta: Tire age delta (rival minus driver, in laps)
                                below which a case is marked
                                "not_applicable". Defaults to -8.0.
                                The model assumes both drivers pay a
                                comparable pit-lane cost, one after the
                                other, so it cancels — but a rival who has
                                already made their final stop never pays it
                                back, and the arithmetic breaks. A rival on
                                much fresher tires has almost certainly
                                already pitted, making the case a reaction
                                to an undercut rather than an undercut
                                opportunity. This is a heuristic standing in
                                for "has the rival finished pitting," which
                                cannot be known directly without a pit
                                window predictor. The default was chosen
                                from the separation observed in Bahrain
                                2024 between rivals with a stop still
                                pending (-5) and rivals already done
                                (-14, -19).

    Conventions and known simplifications:
        Rival tire age is read at the decision lap, while quantify_undercut()
        counts laps from after the stop. The out-lap sits between them, so
        the rival has actually aged roughly one lap more than the model
        assumes. Left uncorrected deliberately: it biases the model away
        from claiming an undercut works, which is the safe direction.

        Degradation rates come from compute_relative_degradation(), pooled
        field-wide per compound. The pitting driver's new tires have not
        turned a lap, so their rate cannot be measured; the compound-typical
        rate is used, assuming they fit the same compound the rival is on.

    Returns:
        A list of insight dicts, each containing: race, lap (the decision
        lap), pit_lap, driver, rival, gap_seconds, gap_seconds_earlier,
        driver_tyre_age, rival_tyre_age, tyre_age_delta, compound, status,
        break_even_laps, advantage_per_lap_seconds, and a confidence block
        holding the degradation fit's r_squared and sample_laps.

        status is one of:
          "quantified"        - break_even_laps holds a lap count
          "beyond_horizon"    - no break-even within the search window
          "not_applicable"    - rival already on much fresher tires
          "insufficient_data" - no usable degradation fit for the compound
    """
    relative = compute_relative_degradation(laps)


    def gap_at_lap(driver_a, driver_b, lap_number):
        """Thin wrapper around get_gap_at_lap() that returns None instead of
        raising when lap data is missing - appropriate here since a missing
        earlier lap just means "skip this candidate," not a real error.
        Positive means driver_a is behind driver_b, matching get_gap_at_lap()'s
        convention."""
        try:
            return get_gap_at_lap(laps, driver_a, driver_b, lap_number)
        except ValueError:
            return None


    def is_pit_affected(driver_code, lap_number):
        """True if this driver's Time at this lap is potentially distorted by
        their own pit stop (in-lap or out-lap)."""
        row = laps[(laps["Driver"] == driver_code) & (laps["LapNumber"] == lap_number)]
        if row.empty:
            return True
        return row["PitInTime"].notna().iloc[0] or row["PitOutTime"].notna().iloc[0]

     
    pit_laps = laps[laps["PitInTime"].notna()]
    candidates = []

    for _, row in pit_laps.iterrows():
        lap_num = row["LapNumber"]
        driver = row["Driver"]
        decision_lap = lap_num - 1

        if lap_num < min_lap:
            continue

        if row["TrackStatus"] != "1":
            continue

        same_lap = laps[laps["LapNumber"] == decision_lap]
        for _, other in same_lap.iterrows():
            if other["Driver"] == driver:
                continue

            if is_pit_affected(other["Driver"], decision_lap):
                continue

            if other["TrackStatus"] != "1":
                continue

            signed_gap = gap_at_lap(driver, other["Driver"], decision_lap)
            if signed_gap is None or signed_gap <= 0:
                continue
            gap = abs(signed_gap)
            if gap >= max_gap:
                continue

            earlier_gap = gap_at_lap(driver, other["Driver"], decision_lap - stability_offset)
            if earlier_gap is None or abs(earlier_gap) >= stability_max_gap:
                continue

            rival_tyre_age = float(other["TyreLife"])
            rival_compound = str(other["Compound"])
            driver_tyre_age = get_tyre_age_at_lap(laps, driver, decision_lap)

            compound_stats = relative.get(rival_compound)
            age_delta = rival_tyre_age - driver_tyre_age

            if age_delta < max_fresher_tyre_delta:
                evaluation = {
                    "status": "not_applicable",
                    "break_even_laps": None,
                    "reason": "rival is on substantially fresher tires — they "
                              "have likely already made their stop, so the "
                              "pit-cost cancellation this model assumes does "
                              "not hold. This is a reaction to an undercut, "
                              "not an undercut opportunity.",
                }
                confidence = {"r_squared": None, "sample_laps": None}
            elif compound_stats is None:
                evaluation = {
                    "status": "insufficient_data",
                    "break_even_laps": None,
                    "reason": f"no usable degradation fit for {rival_compound} "
                              "in this race — sample size, age spread, or R² "
                              "below threshold",
                }
                confidence = {"r_squared": None, "sample_laps": None}
            else:
                compound_rate = compound_stats["degradation_per_lap"]
                evaluation = quantify_undercut(
                    gap_to_rival_seconds = gap,
                    rival_tyre_age = rival_tyre_age,
                    degradation_per_lap_self = compound_rate,
                    degradation_per_lap_rival = compound_rate,
                    max_laps = 15,
                )
                confidence = {
                    "r_squared": compound_stats["r_squared"],
                    "sample_laps": compound_stats["sample_laps"],
                }

            candidates.append({
                "race": race_label,
                "lap": int(decision_lap),
                "pit_lap": int(lap_num),
                "driver": driver,
                "rival": other["Driver"],
                "gap_seconds": round(gap, 3),
                "gap_seconds_earlier": round(earlier_gap, 3),
                "driver_tyre_age": driver_tyre_age,
                "rival_tyre_age": rival_tyre_age,
                "tyre_age_delta": round(age_delta, 1),
                "compound": rival_compound,
                "status": evaluation["status"],
                "break_even_laps": evaluation["break_even_laps"],
                "advantage_per_lap_seconds": evaluation.get(
                    "advantage_first_lap_seconds"
                ),
                "confidence": confidence,
            })

    return candidates



if __name__ == "__main__":
    laps = load_race(2024, "Bahrain")
    results = find_candidates(laps, "Bahrain")
    for c in results:
        print(c)
    print(f"\n{len(results)} candidate(s) found.")