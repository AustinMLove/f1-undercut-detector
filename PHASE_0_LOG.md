# Phase 0 Engineering Log

A record of what was built, what broke, how each failure was diagnosed, and
what was decided as a result. Entries are roughly chronological within each
section.

This is the source material for the architecture document. Where a finding
also lives in a docstring, the docstring is the operational note and this is
the narrative.

---

## 1. Data quirks found during exploration

**FastF1 splits pit timing across two rows.** `PitInTime` appears on the lap
a driver enters the pits; the matching `PitOutTime` appears on the *next*
row. Calculating stop duration therefore needs a per-driver `shift(-1)`, not
a same-row subtraction. Found by inspecting Verstappen's 2024 Bahrain laps
directly rather than assuming the columns aligned.

**`TyreLife` does not always start at 1.** Verstappen opened the 2024
Bahrain race on tires already 4 laps old — sets used earlier in the weekend
carry their history. Harmless for this project, since the model uses the
*difference* in tire age between two cars, but confusing on first sight.

**Out-laps are dramatically slower and must be excluded.** Verstappen's lap
38 ran 1:56.445 against a stint average near 1:34 — roughly 22 seconds of
pit-lane transit landing on the out-lap. Any pace or degradation
calculation that includes them is contaminated.

**Missing data is mostly explainable.** Across 1,129 laps, `PitInTime` and
`PitOutTime` were null on 1,086 — expected, since only 43 laps are pit laps.
`SpeedFL` was null on exactly 43, which matches: a car in the pit lane never
crosses the finish-line speed trap at racing speed. `Sector1Time` had ~21
gaps, likely out-lap timing beacon issues. Everything the model depends on
(`Driver`, `Compound`, `TyreLife`, `Stint`, `Position`) was complete.

**A string/int comparison bug that failed silently.** `TrackStatus` is a
string column. Filtering with `== 1` instead of `== "1"` matched zero rows,
which then tripped the minimum-laps guard and skipped every stint — no
error, no output, just an empty result. Worth noting as the most dangerous
class of bug encountered: it produced nothing rather than something wrong.

---

## 2. Detector bugs — four fixes, all found the same way

Every one of these was caught by checking the tool's output against a case
already verified by hand, then chasing the contradiction. None were found by
reading the code.

**Bug 1 — pit-lane contamination on the pitting driver's side.** The
detector compared drivers' cumulative `Time` at the pit lap itself. But that
lap includes pit-lane transit, which inflates the pitting driver's time by
~25 seconds and can coincidentally place it near a rival's. Bottas and Zhou
were flagged as a 0.063s battle at lap 30; checking the surrounding laps
showed a 14.6s gap two laps earlier and 73.9s two laps after. They were
never racing each other. Fixed by measuring at `pit_lap - 1`.

**Bug 2 — the same contamination on the rival's side.** After fixing bug 1,
Albon and Tsunoda surfaced with a 0.025s gap. Tsunoda was on his own pit
lap at the comparison point, so his time was distorted from the other
direction. Fixed with an `is_pit_affected()` check covering both drivers.

**Bug 3 — no direction check.** The detector surfaced cases where the
pitting driver was already *ahead* of the rival. Magnussen at lap 32 was
2.4s ahead of Ricciardo, not behind — a cover stop, not an undercut. The
question the model answers ("I am behind; is pitting worth it?") does not
apply. Fixed with a signed-gap filter requiring the pitting driver to be
trailing.

**Bug 4 — no stability check.** A single-lap proximity reading can be
coincidence. Added a second, independent gap reading three laps earlier,
with a deliberately looser threshold since gaps drift even in real battles.
Candidate count fell from 30 to 18.

**Bug 5 — no Safety Car filter.** Alonso and Gasly at 2025 Spain produced a
gap-after reading of +80.7 seconds. Reconstructing it lap by lap: 2.8s on
lap 54, then 27.8 + 20.6 + 27.1 across laps 55-57. Both drivers' lap times
went from ~1:20 to 2:18, 2:07, 1:55 and back — a Safety Car. Under caution,
lap time reflects queue position, not pace, and a pit stop costs far less
than usual. `compute_relative_degradation()` already filtered on
`TrackStatus`; the detector did not. Fixed by requiring green-flag status on
both the pit lap and the decision lap.

Cumulative effect of the filter chain: 30 → 18 → 10 → 7 candidates for 2024
Bahrain.

---

## 3. The first model, and why it was worthless

The original `evaluate_undercut()` returned a binary verdict: undercut or
stay out. Backtesting it against seven 2024 Bahrain candidates produced
**"stay out" on all seven** — zero variation across genuinely different
inputs.

Accuracy came out at 0.571. Four of the seven cases were in fact "stay out"
situations. **4/7 = 0.571 exactly** — mathematically identical to a trivial
model that ignores every input and always guesses the majority class. The
model had no discriminative power whatsoever.

**Root cause: a modeling error, not a tuning problem.** The formula charged
the pitting driver ~25 seconds of pit-lane loss and charged the rival
nothing. But the rival pits too, usually within a few laps. **The pit loss
largely cancels.** The model was weighing a ~0.3s degradation term against a
~25s cost that did not belong in the comparison, which made "stay out"
arithmetically inevitable for any realistic close-battle gap.

Quantified: for the model to ever recommend an undercut, the gap would have
had to approach the full pit-loss value — roughly 20+ seconds. But a 20
second gap is not a battle. Every genuine close-battle case observed had a
gap under 3 seconds. The model could never recommend the action it was
named after, in exactly the situations it existed to evaluate.

---

## 4. The pivot

Two changes, decided together.

**From verdict to break-even.** Rather than predicting an outcome that
depends on when the rival's team decides to pit — unknowable at decision
time — the tool now solves for the condition:

> "Stroll is 2.6s behind Tsunoda, whose tires are 5 laps older. If Stroll
> pits now, he needs Tsunoda to stay out 4 more laps for this to work."

This is honest about the one thing nobody can know in real time, and it is
the shape a broadcast insight actually takes.

**From backtester to detector.** The candidate-search tool, not the
evaluator, became the product. Three reasons: it was the component with a
real debugging track record (five bugs found and fixed); an event-driven
detector is a natural Lambda workload where a batch backtest is not; and
surfacing situational awareness is what the AWS F1 Insights product it
recreates actually does.

**Rejected: predicting when the rival will pit.** Considered and declined.
It would require fuel loads, tire allocation, and rival intent — none
available — and would sit on top of degradation numbers already known to be
unreliable. The break-even framing exists specifically to avoid needing it.

---

## 5. The degradation signal was wrong by 12x

With the break-even math in place, the Stroll/Tsunoda case came back
`not_viable`: a predicted advantage of 0.133 s/lap, never closing a 2.6s
gap.

**Measured against reality.** Stroll pitted lap 27, Tsunoda lap 34, so laps
29-33 are clean green-flag running with Stroll on fresh hards and Tsunoda on
hards 15-19 laps old:

| Lap | STR | TSU | Delta |
|---|---|---|---|
| 29 | 95.854 | 97.777 | +1.923 |
| 30 | 95.632 | 97.477 | +1.845 |
| 31 | 96.202 | 97.468 | +1.266 |
| 32 | 95.821 | 97.847 | +2.026 |
| 33 | 96.349 | 97.498 | +1.149 |

Mean 1.64 s/lap against a predicted 0.133. **A 12x understatement.** The
break-even math was sound; its input was not.

**Root cause: fuel burn.** `compute_tire_degradation()` fits lap time
against tire age *within* a stint. But within a stint, fuel burn makes the
car faster while tires make it slower — the fitted slope is the *net* of the
two, which systematically understates real degradation. In three races
(2026 Miami, 2021 France, 2021 Monaco) the netting went far enough to flip
the slope negative, which tripped the guard rail. In Bahrain it landed
positive but 12x light, sailed through the guard rail, and produced a
confident wrong answer. The positive-but-wrong case is the more dangerous
one.

**Fix: same-lap relative degradation.** Compare each driver against the
field median *at the same lap number*, where fuel loads are roughly equal so
fuel cancels exactly. The regression slope is degradation; the intercept is
the driver's inherent pace, which separates itself out instead of
contaminating the result. That intercept is what a raw two-car comparison
cannot handle — the measured 1.64s above mixes tire age with the Aston
Martin/RB car difference, and a pooled regression splits them.

**Result on 2024 Bahrain HARD:** 0.0616 s/lap, R² 0.176, 583 sample laps.
Applied to Stroll/Tsunoda: break-even 4 laps. Tsunoda stayed out 7. The
undercut succeeded. **First correct prediction the project produced.**

---

## 6. Quality filters, each added for a specific observed failure

**R² floor (0.05).** 2024 Bahrain's SOFT compound returned a slope of
-0.0032 at R² 0.001 — noise, not a small degradation rate. Notably it had
*ample* age spread (std 3.78, range -9 to +14.5), so a spread check alone
would not have caught it. Deliberately permissive: it is a floor against
numerically meaningless fits, not a quality bar.

**Sample floor raised 30 → 100.** 2026 Miami's SOFT fit passed on 40
samples with a plausible-looking R² of 0.291, but its `pace_offset` was
1.5359 — soft laps averaging 1.5s slower than field median *at equal tire
age*. A pooled field-wide fit should produce an intercept near zero
(Bahrain 0.064, France 0.045). That intercept revealed the sample was a
fringe subset rather than a cross-section. `pace_offset` was originally kept
as a diagnostic curiosity and turned out to be the sample-quality signal.

**Age spread floor.** Without variation in tire age relative to the field,
the regression is degenerate — every `age_delta` is zero and there is no
line to fit.

**Tire age delta heuristic (-8).** Two candidates at lap 40 of 57 showed
rivals on 6-lap-old tires against drivers on 20- and 25-lap-old tires. Both
rivals had pitted at lap 34 and never pitted again. The pit-cost
cancellation the model depends on requires the rival to still have a stop
coming; when they do not, the driver pays ~25s that the rival never repays.
These were not undercut opportunities — they were *reactions* to having been
undercut. The threshold stands in for "has the rival already finished
pitting," which cannot be known directly without the pit-window predictor
that was explicitly rejected. Chosen from observed separation: rivals with a
stop pending sat at -5, rivals already done at -14 and -19.

---

## 7. Monaco: a limitation, correctly detected

2021 Monaco returns a physically impossible negative degradation rate
(-0.0224) even under same-lap comparison, which cancels both fuel and track
evolution.

**Cause: traffic.** At Monaco, drivers with high `age_delta` are the ones
who stayed out long in clean air; drivers with low `age_delta` pitted early
and rejoined stuck behind cars they cannot pass. `age_delta` therefore
correlates with *clean air* rather than tire condition, and the regression
fits the wrong relationship. This independently reproduces the known
strategic character of the circuit — Monaco overcuts work precisely because
of pit-exit traffic.

**Scope statement this produces:** the method works where tire degradation
is a primary strategic factor and correctly declines where it is not.
Monaco and Miami both return detections with `insufficient_data` rather than
numbers. A tool that knows its own boundaries is a better outcome than one
that produces confident numbers everywhere.

Modeling traffic was considered and rejected as out of scope.

---

## 8. Validation — the honest result

Twelve scoreable cases across 2024-2025 Bahrain, Saudi Arabia, and Spain.
Scoring the conditional claim: if the rival stayed out at least
`break_even_laps`, the undercut should succeed.

**Result: 8 correct, 4 wrong — 66.7%.**

**Baseline: 10 of 12 undercuts succeeded, so always predicting success
scores 83.3%.** The model does not beat guessing. This is the same test the
first model failed, and it fails again — though differently: the first model
could not discriminate at all, this one discriminates and still loses.

**The errors are asymmetric, and that is the useful finding:**

| Prediction | Correct | Wrong |
|---|---|---|
| "works" | 7 | 1 |
| "fails" | 1 | 3 |

Positive indications carry real information (7/8). Negative ones do not
(1/4). Three of the four misses share a shape — the rival stayed out 1 lap
against a break-even of 2 or 3, and the undercut worked anyway. That is the
residual degradation understatement: if true break-even were 1 rather than
2, all three flip correct.

**A correction alone does not rescue it.** Halving every break-even fixes
those three but breaks the one case currently correct — net 11/12, identical
to the baseline, not better.

**Margin correlates with magnitude, weakly.** Margin (actual laps minus
break-even) against final gap: **-0.26** across all cases, **-0.64**
excluding one case with an independently diagnosed confound. Both numbers
belong in any report of this; quoting only the second would be selective,
and the contrast is more interesting anyway — it says the model tracks
magnitude where its assumptions hold and breaks where they do not.

**Sample bias worth naming.** Ten of twelve undercuts succeeded. That is not
an accident: the detector surfaces cases where a driver is close behind a
rival on older tires, which is by construction a favourable setup. The tool
selects for undercuts that work, then is measured on predicting something
that almost always happens.

---

## 9. Known limitations, carried forward

**Linear fit understates non-linear falloff.** Tire degradation steepens
with age; a straight line averages the gentle early slope with the steep
late one and understates at large age deltas. A quadratic fit was
considered and rejected: curvature is not visible above the noise at R²
0.176, and a quadratic's failure mode under extrapolation is unbounded in
either direction, where the linear fit's failure mode is conservative
understatement. Conservative is the safer error for a tool claiming an
undercut works. Candidate refinement: fit both degrees, compare R², report
which the data supports.

**Car pace is not modeled — the one miss that is not the understatement.**
2024 Spain, Russell vs Norris: Norris stayed out 11 laps against a break-even
of 3 and the undercut still failed. Over laps 38-45, Russell on fresh tires
averaged 79.09s against Norris's 79.30s on tires 13+ laps older — only 0.21
s/lap, where the model expected several times that. Norris's times were flat
or improving. The McLaren was simply faster than the Mercedes at that
circuit. `compute_relative_degradation()` computes a per-driver pace term as
the regression intercept, but fits it field-wide and discards it. Errors are
therefore not one-directional, and the residual correction would not have
fixed this case.

**Traffic at pit exit.** Not modeled. Killed the 2024 Imola case, where
Norris undercut Verstappen but rejoined behind Perez and spent the fresh-tire
advantage clearing him.

**Wet and drying conditions.** Excluded. 2024 Silverstone was dropped for
two independent confounds: intermediate-compound laps on a drying track,
where track evolution dominates the tire signal, and a 4.5-second pit stop
for one driver making the outcome unattributable.

**Out-lap penalty.** Left at zero. The pitting driver's cold-tire out-lap
falls inside the comparison window while the rival's falls after it, so it
does not fully cancel. Measuring it cleanly proved difficult because it
overlaps almost entirely with pit-lane transit time. Documented rather than
guessed at.

**Off-by-one in tire age.** Rival tire age is read at the decision lap while
`quantify_undercut()` counts laps from after the stop, with the out-lap in
between. The rival has aged roughly one lap more than the model assumes.
Left uncorrected deliberately — it biases away from claiming an undercut
works, which is the safe direction.

**Self-degradation is an estimate by necessity.** The pitting driver's new
tires have not turned a lap, so their rate cannot be measured. The
compound-typical rate is used, assuming they fit the same compound the rival
is on.

---

## 10. What this produced

A detector that surfaces undercut opportunities and quantifies what each
depends on, without claiming to predict outcomes it demonstrably cannot.

The validation is why the output is framed as context rather than
recommendation. Gap, tire-age delta, and break-even laps are reported;
no verdict is attached.

Three files, cleanly separated:

- `f1_pipeline/process.py` — pure analysis, no I/O, 16 passing tests
- `candidate_search.py` — `load_race()` and `find_candidates()`
- `race_checks.py` — the validation diagnostic

`find_candidates()` takes a DataFrame rather than fetching a session, so
Phase 1's Lambda handler replaces `load_race()` with an S3 read and calls it
unchanged.
