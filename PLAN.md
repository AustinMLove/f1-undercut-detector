# F1 Undercut Detector — Project Plan

Supersedes `PROJECT_PLAN.md` and `PIVOT_PLAN.md`, both of which described
the project as it was scoped rather than as it was built. See §7 for what
changed and why.

Engineering narrative lives in `PHASE_0_LOG.md` (local build) and
`PHASE_1_LOG.md` (AWS core). Architecture decisions with tradeoffs live in
`DECISIONS.md`. This document covers scope, status, and what comes next.

---

## 1. What this is

A detector that surfaces undercut opportunities in Formula 1 races and
quantifies what each one depends on.

Given lap telemetry, it identifies moments where a driver pitting now could
gain track position on a rival ahead, and reports the condition that
decides it:

> "Stroll is 2.6s behind Tsunoda, whose tires are 5 laps older. If Stroll
> pits now, he needs Tsunoda to stay out 4 more laps for this to work."

It reports break-even as context, not as a recommendation. Validation
showed the figure is not a reliable outcome predictor (§4), and the output
framing reflects that.

The project recreates the logic behind **Pit Strategy Battle**, one insight
in AWS's *F1 Insights powered by AWS* product line, from public data. That
framing is deliberate: it demonstrates understanding a real customer use
case well enough to rebuild a version independently.

### Why this shape

- **Scoped to be fully explainable.** Depth of understanding over breadth of
  services. Every component can be defended, including the parts that do not
  work.
- **Reinforces Cloud Practitioner study.** S3, Lambda, IAM, Athena, and
  QuickSight are all CP-domain services.
- **Phased so a working core exists early**, in case the AWS SA internship
  posting goes live sooner than expected.

---

## 2. Architecture

```
FastF1 (official F1 timing API)
    ↓  [ingest]
S3 — raw/          lap telemetry per race
    ↓  [S3 event trigger]
AWS Lambda         find_candidates() — detection + quantification
    ↓
S3 — processed/    JSON: detected battles with break-even and confidence
    ↓
Amazon Athena      SQL query layer
    ↓
Amazon QuickSight  dashboard
```

**Live so far:** ingest → S3 `raw/` → Lambda → S3 `processed/`, fully
automated end to end (Phase 1, complete).

**Deferred, documented as future work:** Amazon Bedrock natural-language
layer, live in-race data, Step Functions or Glue orchestration, a custom
frontend.

---

## 3. Current state — Phase 0 and Phase 1 complete

**Phase 0 (local build):**

| File | Contents |
|---|---|
| `f1_pipeline/process.py` | Pure analysis. No I/O. 16 passing tests. |
| `candidate_search.py` | `load_race()` (the only FastF1 call) and `find_candidates()` |
| `race_checks.py` | Validation diagnostic |
| `tests/test_process.py` | pytest coverage |

**Phase 1 (AWS core), fully detailed in `PHASE_1_LOG.md`:**

| Component | Status |
|---|---|
| S3 bucket (`f1-undercut-detector-dev`, `us-east-1`), `raw/`/`processed/` prefixes | Live |
| IAM execution role, least-privilege scoped policy | Live |
| Lambda function, container image (ECR), pandas packaging solved | Live |
| `handler.py`, `config.py`, `local_run.py` | Live |
| S3 event notification — automatic trigger on `raw/` uploads | Live |

Verified twice: Lambda output matches local output exactly (2024 Bahrain,
7 candidates, matching every previously-validated figure), and a fully
automated run with no manual invocation (2025 Spain, upload → processed
output with no console interaction).

### Detection filters, each added for an observed failure

Applied in order. Details and the case that motivated each are in
`PHASE_0_LOG.md`.

1. `min_lap` — early stops are usually reactive, not strategic
2. Green-flag status on the pit lap and decision lap
3. Neither driver on a pit in-lap or out-lap at the decision lap
4. Pitting driver must be *behind* the rival
5. Gap under `max_gap`
6. Gap also small a few laps earlier — rules out one-lap coincidences
7. Rival not on substantially fresher tires (`max_fresher_tyre_delta`)

Cumulative effect on 2024 Bahrain: 30 → 18 → 10 → 7 candidates.

### Output statuses

- `quantified` — break-even figure computed
- `beyond_horizon` — no break-even within the search window
- `not_applicable` — rival already on much fresher tires; the pit-cost
  cancellation the model depends on does not hold
- `insufficient_data` — no usable degradation fit for that compound

---

## 4. What validation showed

Twelve scoreable cases across 2024–2025 Bahrain, Saudi Arabia, and Spain.

**66.7% accuracy against an 83.3% always-predict-success baseline.** The
model does not beat guessing.

**The errors are asymmetric**, and that is the useful part: where the rival
stayed out at least the break-even, the undercut succeeded 7 times out of 8.
Where they did not, it still succeeded 3 times out of 4. Positive
indications carry information; negative ones do not.

**Margin correlates with outcome magnitude at −0.26**, or −0.64 excluding
one case where a car-pace difference dominated tire age. Both figures
belong in any report of this.

This is why the tool reports context rather than verdicts. Full reasoning,
including the sample-bias caveat, is in `PHASE_0_LOG.md` §8.

---

## 5. Known limitations

Carried forward deliberately, not oversights:

- Linear degradation fit understates non-linear falloff by roughly 2x at
  large tire-age deltas
- Car pace is not modeled — a rival in a faster car can absorb a large
  tire-age disadvantage
- Traffic at pit exit is not modeled
- Wet and drying conditions are out of scope
- The method declines at low-degradation circuits like Monaco, where tire
  age correlates with clean air rather than tire condition
- CSV as the raw storage format doesn't preserve pandas dtypes (`Timedelta`,
  and string columns that look numeric like `TrackStatus`); `handler.py`
  repairs this explicitly on read rather than changing the storage format
  (see `PHASE_1_LOG.md` §5)

---

## 6. Remaining work

### Phase 1 — AWS core (S3 + Lambda) — COMPLETE

See `PHASE_1_LOG.md` for the full narrative, including the pandas
packaging decision (`DECISIONS.md`), the region-mismatch catch, two CSV
round-trip dtype bugs, a resource-sizing (memory/timeout) bug, and both
validation runs.

**Gate criteria, all met:** uploading a race produces processed output;
results are identical to local; the IAM role is genuinely least-privilege
and can be explained; CloudWatch logs are usable.

### Phase 2 — Athena + QuickSight

- [ ] Athena table over `processed/` — the JSON shape may need adjusting
- [ ] Core queries: detections per race, status breakdown, break-even
      distribution
- [ ] QuickSight dashboard: detected battles with gap, tire-age delta,
      break-even, and confidence
- [ ] Architecture diagram
- [ ] Be able to justify every service choice *and* name a considered
      alternative with the trade-off

**Gate = MVP complete:** end-to-end from raw data to dashboard; every
service explainable and justified against an alternative; demoable in under
five minutes.

### Candidate refinements, if time allows

Not scoped. Listed so they are visibly deferred rather than missed:

- Fit degradation at both linear and quadratic degree, compare R², report
  which the data supports
- Use `compute_relative_degradation()`'s per-driver intercept to model car
  pace, addressing the one miss that is not the understatement

---

## 7. What changed from the original plan

The project was scoped as a **verdict machine**: given a moment, predict
undercut or stay out, validated by backtesting against history.

That model was built and **failed a baseline check** — it predicted "stay
out" on all seven test cases, scoring exactly what always-guessing-the-
majority-class would score. Root cause was a modeling error: it charged the
pitting driver a pit-lane cost and charged the rival nothing, when in
reality both pay it and it largely cancels.

The redesign produced this document's subject: a **detector** that solves
for break-even rather than predicting an outcome that depends on what the
rival's pit wall decides next.

The pivot improved the architecture fit. An event-driven detector is a
natural Lambda workload; a batch backtest was not. Phase 1's handler is
thinner as a result, and Phase 2's dashboard shows detected battles with
their signals rather than a predictions-versus-outcomes table.

Phase numbering also drifted. `PIVOT_PLAN.md` anticipated 0.7a–0.7d as
rewrite, detector promotion, and revalidation. What was actually built
inserted a phase: the degradation signal had to be rebuilt from scratch
(§5 of `PHASE_0_LOG.md`) before the detector could be promoted. Both
superseded plan documents can be deleted; this one and the two phase logs
replace them.
