# F1 Undercut Detector

A telemetry-driven detector for Formula 1 undercut opportunities, built to
recreate the logic behind **Pit Strategy Battle**, one insight in AWS's
*F1 Insights powered by AWS* product line, from public race data.

Given lap telemetry, it identifies moments where a driver pitting now could
gain track position on a rival ahead, and reports the condition that
decides it — not a predicted outcome:

> "Stroll is 2.6s behind Tsunoda, whose tires are 5 laps older. If Stroll
> pits now, he needs Tsunoda to stay out 4 more laps for this to work."

Validation showed break-even is not a reliable outcome predictor (see
§Validation below), so the tool reports context, not a verdict. That
distinction — and the failed first design that led to it — is the most
interesting part of this project; see `PHASE_0_LOG.md`.

---

## Status

| Phase | Scope | Status |
|---|---|---|
| **Phase 0** | Local pipeline: tire-degradation model, undercut quantification, candidate detection, validation | ✅ Complete |
| **Phase 1** | AWS core: S3 + Lambda, event-driven, container-image deployment | ✅ Complete |
| **Phase 2** | Athena + QuickSight dashboard | ⏳ In Progress |

Full scope, gate criteria, and what changed from the original design are in
[`PLAN.md`](./PLAN.md). Engineering narratives — what was built, what broke,
and why each fix was made — are in [`PHASE_0_LOG.md`](./PHASE_0_LOG.md) and
[`PHASE_1_LOG.md`](./PHASE_1_LOG.md). Architecture decisions with tradeoffs
are in [`DECISIONS.md`](./DECISIONS.md).

---

## Architecture

```
FastF1 (official F1 timing API)
    ↓  [ingest]
S3 — raw/          lap telemetry per race
    ↓  [S3 event trigger]
AWS Lambda         find_candidates() — detection + quantification
    ↓
S3 — processed/    JSON: detected battles with break-even and confidence
    ↓
Amazon Athena      SQL query layer            (Phase 2)
    ↓
Amazon QuickSight  dashboard                  (Phase 2)
```

**Live today:** ingest → S3 `raw/` → Lambda → S3 `processed/`, fully
automated end to end.

The Lambda function runs as a container image (Amazon ECR) rather than an
AWS-managed layer — see `DECISIONS.md` for the tradeoff and rationale.

---

## Validation

Twelve scoreable cases across 2024–2025 Bahrain, Saudi Arabia, and Spain.

- **66.7% accuracy** against an **83.3% always-predict-success baseline**
  — the model does not beat guessing on its own.
- **Errors are asymmetric**, which is the useful part: when the rival
  stayed out at least the break-even, the undercut succeeded 7/8 times;
  when they didn't, it still succeeded 3/4 times. Positive indications
  carry information; negative ones don't.
- **Margin correlates with outcome magnitude at −0.26** (−0.64 excluding
  one case where car-pace dominated tire age).

Full reasoning, including the sample-size caveat, is in `PHASE_0_LOG.md`.

---

## Known limitations

- Linear tire-degradation fit understates non-linear falloff by roughly
  2x at large tire-age deltas
- Car pace isn't modeled — a rival in a faster car can absorb a large
  tire-age disadvantage
- Pit-exit traffic isn't modeled
- Wet/drying conditions are out of scope
- Declines at low-degradation circuits (e.g. Monaco), where tire age
  correlates with clean air rather than tire condition
- CSV as the raw storage format doesn't preserve pandas dtypes
  (`Timedelta` columns, and numeric-looking string columns like
  `TrackStatus`); `handler.py` repairs this explicitly on read

---

## Repository layout

```
f1_pipeline/
  process.py          Pure analysis — degradation model, gap/quantification logic. No I/O.
config.py                Environment-driven config (bucket/prefix names). Lives at project
                          root, not inside f1_pipeline/ — local_run.py imports it with a bare
                          `import config`, which resolves via the script's own directory, not
                          package structure. Not currently copied into the Lambda image
                          (handler.py doesn't import it).
candidate_search.py     find_candidates() — detection + quantification pipeline
local_run.py             Local dev entry point (S3-shaped local I/O)
handler.py               Lambda entry point (S3 event → S3 output)
Dockerfile               Lambda container image build
requirements.txt         Runtime dependencies (pandas)
tests/
  test_process.py       pytest coverage for process.py
race_checks.py           Manual validation diagnostic
PLAN.md                  Scope, status, gate criteria
PHASE_0_LOG.md            Local build engineering log
PHASE_1_LOG.md            AWS core engineering log
DECISIONS.md              Architecture decision records
```

---

## Running locally

Requires Python 3.14 and `pip install -r requirements.txt` plus `fastf1`
(used only by the local entry point, not the Lambda handler).

```bash
python local_run.py
```

Pulls a race session via FastF1, runs the detection pipeline, and writes
`raw/<race>/laps.csv` and `processed/<race>/candidates.json` locally,
mirroring the S3 key layout used in AWS.

---

## Why this shape

Scoped to be fully explainable rather than broad — every component,
including the parts that don't work, can be defended. Built in phases so a
working core exists early. More in `PLAN.md` §1.
