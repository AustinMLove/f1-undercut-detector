import numpy as np
import pandas as pd

def compute_tire_degradation(laps: pd.DataFrame) -> dict:
    """
    This function computes per-driver, per-stint tire degradation rates from lap data.

    For each stint, it fits a linear regression of Laptime against TyreLife and
    returns the slope - seconds of lap time per lap of tire age.

    Args:
        laps: A DataFrame matching FastF1's session.laps structure, with at minimum
        Driver, Stint, LapNumber, LapTime, TyreLife, Compound, and TrackStatus columns.

    Returns:
        A dict keyed by driver code, each value a list of stint dicts:
        {"stint": int, "compound": str, "lap_count": int, "degradation_per_lap": float}
    """
    results = {}

    for driver, driver_laps in laps.groupby("Driver"):
        stints = []
        for stint_num, stint_laps in driver_laps.groupby("Stint"):
            stint_laps = stint_laps.sort_values("LapNumber")

            stint_laps = stint_laps[stint_laps["TrackStatus"] == "1"]
            stint_laps = stint_laps[stint_laps["PitInTime"].isna() & stint_laps["PitOutTime"].isna()]

            if len(stint_laps) < 3:
                continue

            tyre_life = stint_laps["TyreLife"].to_numpy()
            lap_times = stint_laps["LapTime"].dt.total_seconds().to_numpy()

            slope, intercept = np.polyfit(tyre_life, lap_times, deg=1)

            stints.append({
                "stint": int(stint_num), # type: ignore[arg-type]
                "compound": stint_laps["Compound"].iloc[0],
                "lap_count": len(stint_laps),
                "degradation_per_lap": round(float(slope), 3),
            })

        results[driver] = stints

    return results


def compute_average_pit_stop_loss(laps: pd.DataFrame) -> float:
    """
    Computes the race-wide average time lost during a pit stop.

    Uses the median across all real pit stops in the field, rather than the mean, 
    to reduce sensitivity to outlier stops (drive-through penalties, damage repairs,
    mechanical issues) that don't represent a typical, strategy-relevant stop.

    Args: 
        laps: A DataFrame matching FastF1's session.laps structure with
              PitInTime, PitOutTime, Driver, and LapNumber columns.
        
    Returns:
        Median pit stop duration in seconds
    """
    laps_sorted = laps.sort_values(["Driver", "LapNumber"]).copy()
    laps_sorted["NextPitOutTime"] = laps_sorted.groupby("Driver")["PitOutTime"].shift(-1)

    pit_stops = laps_sorted[laps_sorted["PitInTime"].notna()].copy()
    pit_stops["PitDuration"] = (pit_stops["NextPitOutTime"] - pit_stops["PitInTime"]).dt.total_seconds()

    pit_stops = pit_stops[pit_stops["PitDuration"].notna()]

    return float(pit_stops["PitDuration"].median())


def compute_relative_degradation(
        laps: pd.DataFrame,
        min_laps_per_compound: int = 100,
        outlier_trim: float = 0.1,
        min_age_spread: float = 1.0,
        min_r_squared: float = 0.05,
) -> dict:
    """
    Estimates per-compound tire degradation using same-lap comparison,
    which cancels the fuel-burn confound that contaminates within-stint
    regression.

    compute_tire_degradation() fits lap time against tire age inside a
    single stint. Within a stint, fuel burn makes the car faster while
    tires make it slower, so the fitted slope is the net of the two and
    systematically understates real degradation — confirmed against the
    2024 Bahrain STR/TSU case, where it produced 0.013-0.036 s/lap against
    a measured on-track difference of roughly 1.6 s/lap.

    This function compares each driver against the field median at the
    same lap number. Fuel load is roughly equal across the field at any
    given lap, so it cancels. What remains is the driver's pace relative
    to the field (the regression intercept) and the effect of their tire
    age relative to the field (the slope).

    Args:
        laps: A DataFrame matching FastF1's session.laps structure.
        min_laps_per_compound: Minimum clean laps before a compound gets
                               a fit. Defaults to 30.
        outlier_trim: Fraction trimmed from each tail of the lap-time
                      delta distribution before fitting, to limit the
                      effect of traffic and damage. Defaults to 0.1.
        min_age_spread: Minimum standard deviation of the tire-age delta
                        required to fit. Without spread the regression is
                        degenerate. Defaults to 1.0.
        min_r_squared: Minimum R² before a compound's fit is returned at
                       all. Defaults to 0.05 — deliberately permissive.
                       This is a floor against numerically meaningless
                       fits, not a quality bar: it exists to drop cases
                       like Bahrain 2024's SOFT compound (R² 0.001, a
                       slope indistinguishable from noise) while keeping
                       weak-but-real fits like that race's HARD (R²
                       0.176). Callers should still read r_squared and
                       weigh it rather than treating any returned rate as
                       equally trustworthy.

    Returns:
        A dict keyed by compound, each value containing
        degradation_per_lap (the slope), pace_offset (the intercept, kept
        as a diagnostic), r_squared, and sample_laps. Compounds failing
        the filters are omitted.
    """
    clean = laps[
        (laps["TrackStatus"] == "1")
        & laps["PitInTime"].isna()
        & laps["PitOutTime"].isna()
        & laps["LapNumber"].notna()
    ].copy()

    if clean.empty:
        return {}

    clean["LapTimeSeconds"] = clean["LapTime"].dt.total_seconds()

    lap_medians = clean.groupby("LapNumber").agg(
        median_time = ("LapTimeSeconds", "median"),
        median_age = ("TyreLife", "median"),
    )
    clean = clean.join(lap_medians, on="LapNumber")

    clean["time_delta"] = clean["LapTimeSeconds"] - clean["median_time"]
    clean["age_delta"] = clean["TyreLife"] - clean["median_age"]

    results = {}

    for compound, group in clean.groupby("Compound"):
        if len(group) < min_laps_per_compound:
            continue

        low = group["time_delta"].quantile(outlier_trim)
        high = group["time_delta"].quantile(1 - outlier_trim)
        trimmed = group[
            (group["time_delta"] >= low) & (group["time_delta"] <= high)
        ]

        if len(trimmed) < min_laps_per_compound:
            continue
        if trimmed["age_delta"].std() < min_age_spread:
            continue

        x = trimmed["age_delta"].to_numpy()
        y = trimmed["time_delta"].to_numpy()

        slope, intercept = np.polyfit(x, y, deg=1)

        predicted = slope * x + intercept
        ss_res = ((y - predicted) ** 2).sum()
        ss_tot = ((y - y.mean()) ** 2).sum()
        r_squared = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

        if r_squared < min_r_squared:
            continue

        results[str(compound)] = {
            "degradation_per_lap": round(float(slope), 4),
            "pace_offset": round(float(intercept), 4),
            "r_squared": round(r_squared, 3),
            "sample_laps": len(trimmed),
        }

    return results


def get_gap_at_lap(laps: pd.DataFrame, driver_a: str, driver_b: str, lap_number: int) -> float:
    """
    Returns the time gap, in seconds, between driver_a and driver_b at a given lap number.
    Positive means driver_a is behind driver_b.

    Args:
        laps: A DataFrame matching FastF1's session.laps structure.
        driver_a: Three-letter code of the first driver.
        driver_b: Three-letter code of the second driver.
        lap_number: the lap to compare at.

    Returns:
        Gap in seconds (positive = driver_a behind driver_b).
    """
    lap_a = laps[(laps["Driver"] == driver_a) & (laps["LapNumber"] == lap_number)]
    lap_b = laps[(laps["Driver"] == driver_b) & (laps["LapNumber"] == lap_number)]

    if lap_a.empty or lap_b.empty:
        raise ValueError(f"Missing lap data for lap {lap_number}")

    time_a = lap_a["Time"].iloc[0].total_seconds()
    time_b = lap_b["Time"].iloc[0].total_seconds()

    return time_a - time_b


def get_tyre_age_at_lap(laps: pd.DataFrame, driver: str, lap_number: int) -> float:
    """
    Returns a driver's tire age (laps on the current set) at a given lap.

    Args:
        laps: A DataFrame matching FastF1's session.laps structure.
        driver: Three-letter criver code.
        lap_number: The lap to read tire age at.

    Returns:
        Tire age in laps, from FastF1's TyreLife column.
    """
    row = laps[(laps["Driver"] == driver) & (laps["LapNumber"] == lap_number)]

    if row.empty:
        raise ValueError(f"No lap data for {driver} at lap {lap_number}")

    return float(row["TyreLife"].iloc[0])


def quantify_undercut(
        gap_to_rival_seconds: float,
        rival_tyre_age: float,
        degradation_per_lap_self: float,
        degradation_per_lap_rival: float,
        max_laps: int = 15,
        out_lap_penalty_seconds: float = 0.0,
) -> dict:
    """
    Quantifies what an undercut opportunity depends on: how many laps the
    rival must stay out before the pitting driver's fresh-tire advantage
    overcomes the current gap.

    Reports a break-even figure as context, not a recommendation. Whether
    the undercut actually succeeds depends on when the rival's team decides
    to pit — unknowable at decision time — plus traffic at pit exit and the
    relative pace of the two cars, neither of which this model sees.

    Method: after the pitting driver stops, each subsequent lap they run on
    fresh rubber while the rival's tires age further. The per-lap pace
    difference at lap i is:

        degradation_per_lap_rival * (rival_tyre_age + i)
        - degradation_per_lap_self * i

    accumulated until it exceeds the gap. Both drivers pay roughly the same
    pit-lane cost, one after the other, so it cancels and does not appear
    here.

    Validation (2024-2025, 12 scoreable cases across Bahrain, Saudi Arabia,
    and Spain):
      - Treating break_even_laps as a binary predictor scored 66.7%, against
        83.3% for a trivial always-predict-success baseline. It is not a
        reliable predictor of outcome.
      - Asymmetric by direction: where the rival stayed out at least
        break_even_laps, the undercut succeeded 7 times out of 8. Where they
        did not, the undercut still succeeded 3 times out of 4. Positive
        indications are informative; negative ones are not.
      - Margin (actual laps minus break_even_laps) correlates with outcome
        magnitude at -0.26 across all cases, or -0.64 excluding one case
        where a car-pace difference dominated tire age (2024 Spain,
        RUS/NOR). The relationship holds where the model's assumptions hold
        and breaks where they do not.

    Known limitations:
      - Degradation rates come from a linear fit and understate true wear by
        roughly 2x at large tire-age deltas, since tire falloff is
        non-linear. This biases break_even_laps upward, which is the likely
        source of the asymmetry above.
      - Assumes both pit stops take comparable time.
      - Does not model traffic at pit exit, which can negate a
        mathematically sound undercut entirely.
      - Does not model the relative pace of the two cars. A rival in a
        faster car can absorb a large tire-age disadvantage.
      - A negative degradation rate signals a confound the model cannot
        separate out (fuel burn-off dominating a short or low-degradation
        stint). The function declines rather than computing a misleading
        answer.

    Args:
        gap_to_rival_seconds: How far the pitting driver is behind the
                              rival, before the stop. Must be positive.
        rival_tyre_age: Rival's tire age in laps at the decision point.
        degradation_per_lap_self: Degradation rate for the tires the
                                  pitting driver will be on after stopping.
        degradation_per_lap_rival: Rival's current degradation rate.
        max_laps: How far ahead to search before giving up. Default 15.
        out_lap_penalty_seconds: Optional one-off cost for the pitting
                                 driver's cold-tire out-lap, which falls
                                 inside the comparison window while the
                                 rival's falls after it. Defaults to 0.0 —
                                 measuring it cleanly proved difficult, as
                                 it overlaps almost entirely with pit-lane
                                 transit time.

    Returns:
        A dict with status, break_even_laps, and every component value that
        produced them. status is one of:
          "quantified"        - break_even_laps holds a lap count
          "beyond_horizon"    - no break-even within max_laps; note this
                                reflects the search window and the model's
                                conservative degradation estimate as much
                                as the situation itself
          "insufficient_data" - degradation input unreliable, declined
    """
    if degradation_per_lap_rival < 0 or degradation_per_lap_self < 0:
        return {
            "status": "insufficient_data",
            "break_even_laps": None,
            "reason": "negative degradation rate - likely confounded by "
                      "fuel burn-off or a low-degradation track, not "
                      "reliable for thsi calculation",
            "gap_to_rival_seconds": round(gap_to_rival_seconds, 3),
            "rival_tyre_age": rival_tyre_age,
            "degradation_per_lap_self": degradation_per_lap_self,
            "degradation_per_lap_rival": degradation_per_lap_rival,
        }

    cumulative_advantage = -out_lap_penalty_seconds
    break_even_laps = None

    for n in range(1, max_laps + 1):
        rival_loss = degradation_per_lap_rival * (rival_tyre_age + n)
        self_loss = degradation_per_lap_self * n
        cumulative_advantage += rival_loss - self_loss

        if cumulative_advantage >= gap_to_rival_seconds:
            break_even_laps = n
            break

    first_lap_advantage = (
        degradation_per_lap_rival * (rival_tyre_age + 1)
        - degradation_per_lap_self
    )

    return {
                "status": "quantified" if break_even_laps is not None else "beyond_horizon",
                "break_even_laps": break_even_laps,
                "gap_to_rival_seconds": round(gap_to_rival_seconds, 3),
                "rival_tyre_age": rival_tyre_age,
                "degradation_per_lap_self": degradation_per_lap_self,
                "degradation_per_lap_rival": degradation_per_lap_rival,
                "advantage_first_lap_seconds": round(first_lap_advantage, 3),
                "max_laps": max_laps,
            }