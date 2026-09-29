import pandas as pd
import pytest
from f1_pipeline.process import (
    quantify_undercut,
    get_gap_at_lap,
    get_tyre_age_at_lap,
    compute_relative_degradation,
)


@pytest.fixture
def two_driver_laps():
    """Minimal laps frame: two drivers, three laps. DRA is 2 seconds behind
    DRB throughout, on tires 5 laps older."""
    return pd.DataFrame({
        "Driver": ["DRA", "DRA", "DRA", "DRB", "DRB", "DRB"],
        "LapNumber": [1.0, 2.0, 3.0, 1.0, 2.0, 3.0],
        "Time": pd.to_timedelta([92.0, 184.0, 276.0, 90.0, 182.0, 274.0], unit="s"),
        "TyreLife": [10.0, 11.0, 12.0, 5.0, 6.0, 7.0],
    })


def build_degradation_laps(
    age_offsets=(1, 6, 11, 16),
    n_laps=10,
    rate=0.1,
    base_time=90.0,
    compound="HARD",
    track_status="1",
):
    """Synthetic laps where lap time is exactly base_time + rate * tyre_age.

    Four drivers with staggered tire ages at the same lap numbers, so the
    same-lap comparison has real age spread to work with. With a perfect
    linear relationship, compute_relative_degradation() should recover
    `rate` as the slope with R² of 1.0.
    """
    rows = []
    for i, offset in enumerate(age_offsets):
        for lap in range(1, n_laps + 1):
            age = offset + lap - 1
            rows.append({
                "Driver": f"D{i + 1}",
                "LapNumber": float(lap),
                "TyreLife": float(age),
                "LapTime": pd.to_timedelta(base_time + rate * age, unit="s"),
                "Compound": compound,
                "TrackStatus": track_status,
                "PitInTime": pd.NaT,
                "PitOutTime": pd.NaT,
            })
    return pd.DataFrame(rows)


def test_quantify_undercut_returns_break_even_for_normal_case():
    """A modest gap against a rival on well-worn tires should produce a
    break-even figure within the search window."""
    result = quantify_undercut(
        gap_to_rival_seconds=2.0,
        rival_tyre_age=15.0,
        degradation_per_lap_self=0.06,
        degradation_per_lap_rival=0.06,
    )

    assert result["status"] == "quantified"
    assert result["break_even_laps"] is not None
    assert result["break_even_laps"] >= 1


def test_quantify_undercut_reports_beyond_horizon_for_large_gap():
    """A gap far too large for the available tire advantage should report
    beyond_horizon rather than an arbitrary lap count."""
    result = quantify_undercut(
        gap_to_rival_seconds=60.0,
        rival_tyre_age=2.0,
        degradation_per_lap_self=0.06,
        degradation_per_lap_rival=0.06,
        max_laps=15,
    )

    assert result["status"] == "beyond_horizon"
    assert result["break_even_laps"] is None


def test_quantify_undercut_declines_on_negative_rival_degradation():
    """A negative degradation rate signals a confound the model cannot
    separate out, so the function should decline rather than compute a
    misleading answer. This fired on three real races (2026 Miami,
    2021 France, 2021 Monaco)."""
    result = quantify_undercut(
        gap_to_rival_seconds=2.0,
        rival_tyre_age=12.0,
        degradation_per_lap_self=0.06,
        degradation_per_lap_rival=-0.02,
    )

    assert result["status"] == "insufficient_data"
    assert result["break_even_laps"] is None
    assert "reason" in result


def test_quantify_undercut_declines_on_negative_self_degradation():
    """The guard rail covers both drivers' rates, not just the rival's"""
    result = quantify_undercut(
        gap_to_rival_seconds=2.0,
        rival_tyre_age=12.0,
        degradation_per_lap_self=-0.02,
        degradation_per_lap_rival=0.06,
    )

    assert result["status"] == "insufficient_data"


def test_quantify_undercut_reproduces_verified_bahrain_case():
    """Regression test against the one case verified independently three
    ways: 2024 Bahrain, Stroll pitting on lap 27 with Tsunoda 2.643s ahead
    on 12-lap-old hards. Tsunoda stayed out 7 more laps and the undercut
    succeeded decisively — the gap swung from +2.643 to -11.761.

    The 0.0616 s/lap rate is compute_relative_degradation()'s field-wide
    HARD fit for that race (R² 0.176, 583 sample laps). Both rates are
    equal because the pitting driver's new tires have not turned a lap, so
    the compound-typical rate stands in for their unmeasurable one.

    This case has survived every refactor since the 0.7a break-even
    rewrite. If it changes, something material changed with it.
    """
    result = quantify_undercut(
        gap_to_rival_seconds=2.643,
        rival_tyre_age=12.0,
        degradation_per_lap_self=0.0616,
        degradation_per_lap_rival=0.0616,
    )

    assert result["status"] == "quantified"
    assert result["break_even_laps"] == 4
    assert result["advantage_first_lap_seconds"] == 0.739


def test_get_gap_at_lap_returns_positive_when_driver_a_is_behind(two_driver_laps):
    """Sign convention: positive means driver_a is behind driver_b. Every
    filter and threshold downstream depends on this holding."""
    gap = get_gap_at_lap(two_driver_laps, "DRA", "DRB", 2)
    assert gap == pytest.approx(2.0)


def test_get_gap_at_lap_returns_negative_when_driver_a_is_ahead(two_driver_laps):
    """The same comparison reversed should flip sign, not magnitude."""
    gap = get_gap_at_lap(two_driver_laps, "DRB", "DRA", 2)
    assert gap == pytest.approx(-2.0)


def test_get_gap_at_lap_raises_on_missing_lap(two_driver_laps):
    """Missing lap data is a loud failure by design — a silent None would
    propagate into gap arithmetic as a wrong answer."""
    with pytest.raises(ValueError):
        get_gap_at_lap(two_driver_laps, "DRA", "DRB", 99)


def test_get_tyre_age_at_lap_returns_tyre_life(two_driver_laps):
    age = get_tyre_age_at_lap(two_driver_laps, "DRB", 3)
    assert age == pytest.approx(7.0)


def test_get_tyre_age_at_lap_raises_on_missing_driver(two_driver_laps):
    with pytest.raises(ValueError):
        get_tyre_age_at_lap(two_driver_laps, "DRZ", 1)


def test_compute_relative_degradation_recovers_known_rate():
    """With lap time an exact linear function of tyre age, the fit should
    return that rate and a near-perfect R²."""
    laps = build_degradation_laps(rate=0.1)
    result = compute_relative_degradation(laps, min_laps_per_compound=20)

    assert "HARD" in result
    assert result["HARD"]["degradation_per_lap"] == pytest.approx(0.1, abs=0.001)
    assert result["HARD"]["r_squared"] == pytest.approx(1.0, abs=0.01)


def test_compute_relative_degradation_rejects_small_sample():
    """Too few laps for a compound should produce no fit at all. This
    threshold was raised from 30 to 100 after a 40-sample fit in 2026 Miami
    produced an implausible pace_offset of 1.54s."""
    laps = build_degradation_laps(n_laps=3)
    result = compute_relative_degradation(laps, min_laps_per_compound=20)

    assert result == {}


def test_compute_relative_degradation_rejects_uniform_tyre_age():
    """Without spread in tyre age relative to the field, the regression is
    degenerate — every age_delta is zero and there is no line to fit."""
    laps = build_degradation_laps(age_offsets=(5, 5, 5, 5))
    result = compute_relative_degradation(laps, min_laps_per_compound=20)

    assert result == {}


def test_compute_relative_degradation_rejects_when_age_explains_nothing():
    """With rate 0, every driver posts an identical lap time, so tyre age
    explains none of the variation and R² falls below the floor. This is
    the filter that excluded 2024 Bahrain's SOFT compound, where R² came
    back at 0.001 despite ample age spread."""
    laps = build_degradation_laps(rate=0.0)
    result = compute_relative_degradation(laps, min_laps_per_compound=20)

    assert result == {}


def test_compute_relative_degradation_excludes_non_green_laps():
    """Safety Car and VSC laps are excluded from the fit. Lap time under a
    Safety Car reflects queue position, not tyre condition."""
    laps = build_degradation_laps(track_status="4")
    result = compute_relative_degradation(laps, min_laps_per_compound=20)

    assert result == {}


def test_compute_relative_degradation_excludes_pit_laps():
    """In-laps and out-laps carry pit-lane transit time, which would
    contaminate the fit."""
    laps = build_degradation_laps()
    laps["PitInTime"] = pd.to_timedelta(60.0, unit="s")
    result = compute_relative_degradation(laps, min_laps_per_compound=20)

    assert result == {}