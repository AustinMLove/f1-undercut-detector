# Architecture Decision Records

*F1 Undercut Detector*

Format: Context / Options / Decision / Rationale / Consequences. Recorded
at the time each decision was made, not reconstructed afterward — kept
here rather than folded into the phase logs because these are the
decisions worth defending independently of the narrative around them.

---

## ADR-000: Minimal serverless architecture (S3 + Lambda + Athena + QuickSight)

**Status:** Decided, in progress (Phases 0–1 implemented, Phase 2 planned)

### Context

Before any implementation work started, the project needed an overall
shape. The goal was a portfolio piece that demonstrates real
understanding of AWS services for a Solutions Architect internship
application and reinforces AWS Cloud Practitioner study — not the
broadest possible architecture, but one that could be fully explained,
including the parts that don't work.

### Options

**A. Minimal serverless pipeline** — S3 for storage, Lambda for
processing, Athena for querying, QuickSight for the dashboard. Each
service maps directly onto a Cloud Practitioner exam domain.

- Pros: every component is small enough to explain end-to-end, including
  its failure modes; fast to stand up a working core; each service choice
  is independently defensible against a named alternative
- Cons: no orchestration layer for multi-step workflows, no managed ETL,
  no natural-language interface — all real AWS capabilities left off the
  table

**B. Fuller orchestrated pipeline** — add Step Functions or Glue for
orchestration, Bedrock for a natural-language query layer over the
detections, and a custom frontend instead of QuickSight.

- Pros: broader service coverage, closer to what a production system
  would actually look like, more impressive on paper
- Cons: more surface area to build, secure, and — critically — be able to
  explain in depth under interview questioning; risks becoming a project
  that's wide but shallow, with components added because they exist
  rather than because they solve a problem this project actually has

### Decision

Chose **A — minimal serverless pipeline**, with B's components
(Bedrock, Step Functions/Glue, a custom frontend) explicitly deferred and
documented as future work rather than silently dropped.

### Rationale

Depth of understanding was prioritized over breadth of services. A
five-service pipeline that can be defended in detail, including its
tire-degradation modeling limitations and its two real AWS bugs (a
region mismatch and an OCI manifest-format incompatibility), demonstrates
more engineering judgment in an interview than a ten-service pipeline
built by following tutorials. Phasing the build so a working core (S3 +
Lambda) existed early was a deliberate hedge against the internship
posting appearing before the full pipeline was done.

### Consequences

- Phase 2 (Athena + QuickSight) is scoped as the MVP completion gate —
  end-to-end from raw data to dashboard, every service choice
  explainable against a considered alternative, demoable in under five
  minutes (`PLAN.md` §6).
- Bedrock, Step Functions/Glue orchestration, live in-race data, and a
  custom frontend are listed as deferred future work, not silently
  absent — so the scope decision itself is visible and defensible if
  asked "why didn't you use X."
- Because the pipeline is intentionally small, individual component
  decisions (ADR-001 through ADR-003) carry more relative weight — there
  are fewer places for a bad choice to hide.

---

## ADR-001: Pandas packaging — container image vs. AWS-managed Lambda layer

**Status:** Decided, implemented (Phase 1)

### Context

Lambda's Python runtime ships with no third-party packages. `pandas`
depends on `numpy`'s compiled, architecture-specific binaries, so a
package built by running `pip install` locally and zipping the result
produces a `.zip` that crashes on import inside Lambda's actual execution
environment — the compiled binaries don't match Lambda's runtime
architecture.

Two ways to get a working pandas into the function were considered.

### Options

**A. AWS-managed Lambda layer (`AWSSDKPandas-PythonXXX`)**
Attach a pre-built, AWS-maintained layer directly from the console. No
build step required; AWS guarantees binary compatibility with the
runtime.

- Pros: fastest to stand up, zero build tooling, maintained by AWS
- Cons: pinned to whichever pandas version AWS has published for that
  runtime; no control over the dependency beyond what the layer offers;
  doesn't generalize if a future dependency isn't covered by an existing
  managed layer

**B. Container image, built `FROM public.ecr.aws/lambda/python`**
`pip install` happens inside the image build, so installed binaries are
guaranteed to match Lambda's real runtime because they're compiled in an
environment based on it. Pushed to ECR, deployed as a container-image
Lambda function.

- Pros: full control over the dependency set and versions; same
  build/push/deploy shape as any other containerized service; extends
  existing Docker and CI/CD experience into a new context
- Cons: more setup (Dockerfile, ECR repo, image build/push loop) than
  attaching a layer from a dropdown; slightly larger deployment artifact;
  cold-start init time is sensitive to image size

### Decision

Chose **B — container image**.

### Rationale

Not because the layer was wrong — for a single well-supported dependency
like pandas, the layer is the faster, lower-maintenance choice, and that
tradeoff is real. The container image was chosen because:

1. It avoids version lock-in. If a future iteration needs a dependency
   the managed layer doesn't cover, or a pandas version ahead of what
   AWS has published, the container path already supports it with no
   architecture change.
2. It reuses and extends already-held skills (Docker, image build/push,
   CI/CD) rather than introducing a Lambda-specific mechanism that
   doesn't transfer elsewhere.
3. The extra setup cost is fixed and one-time (Dockerfile, ECR repo);
   it doesn't recur per-dependency the way managing multiple layers
   might.

### Consequences

- Introduced a full Docker build/push loop into the deployment path,
  including a real bug: Docker's default `docker build` attaches
  provenance/SBOM attestations that wrap the image in an OCI
  manifest-list format Lambda's function-creation step rejects
  ("the image manifest, config or layer media type... is not
  supported"). Fixed with `docker build --provenance=false --sbom=false`.
  This is a one-time cost of the chosen path, not a recurring one — see
  `PHASE_1_LOG.md` §4.
- Image size and cold-start init time now depend on what's copied into
  the image; the fixed 512 MB / 30s memory/timeout resize (§6 of
  `PHASE_1_LOG.md`) was necessary regardless of which packaging option
  was chosen, since it's driven by pandas/numpy import cost at low
  default memory, not by the container path specifically.
- Redeploys require a full image build + push cycle rather than a
  layer-version bump; acceptable at this project's scale and change
  frequency.

---

## ADR-002: Verdict machine → opportunity detector (the core pivot)

**Status:** Decided, implemented (Phase 0)

### Context

The project was originally scoped as a predictive model: given a moment
in a race, predict whether an undercut would succeed or the rival should
stay out, validated by backtesting predictions against real outcomes.

That model was built first. It failed a baseline check — it predicted
"stay out" on all seven test cases, scoring exactly what an
always-guess-the-majority-class baseline would score, meaning it had
learned nothing distinguishing. The root cause was a modeling error: the
model charged the pitting driver a pit-lane time cost and charged the
rival nothing, when in reality both eventually pay a pit-lane cost and it
largely cancels out of the comparison. That asymmetric cost dominated
every other signal in the model.

### Options

**A. Fix the cost model and keep the verdict-machine framing**
Correct the pit-loss asymmetry, keep predicting success/fail, re-validate.

- Pros: preserves the original scope and the "predictive model" framing
- Cons: even with the cost bug fixed, a bare success/fail verdict still
  depends heavily on what the rival's pit wall decides to do next — a
  variable outside the data the model has access to. A single-number
  verdict overstates the confidence the underlying signal can support.

**B. Reframe as a break-even detector**
Instead of predicting an outcome, solve for and report the break-even
condition — how many laps the rival needs to stay out for the undercut to
work — and quantify the current situation against it, without asserting
what will happen.

- Pros: matches what the data can actually support; the output becomes
  context a strategist can act on rather than a claim of certainty;
  aligns naturally with an event-driven, real-time architecture rather
  than a batch backtest
- Cons: no longer a simple "did it work" backtest; validation has to be
  reframed around whether the break-even figure correlates with outcomes
  rather than whether it predicts them outright

### Decision

Chose **B — reframe as a break-even detector**, after fixing the
underlying pit-loss modeling error first and confirming the reframe was
still necessary (i.e., the fix alone didn't produce a good verdict
machine — see `PHASE_0_LOG.md` for the full sequence).

### Rationale

The validation data itself made the case: even after the cost-model fix,
margin correlated with outcome magnitude at only −0.26 (−0.64 excluding
one car-pace-dominated outlier) — real signal, but not enough to support
a confident binary verdict. Reporting the break-even condition as context
is an honest match to what the signal can support; reporting a verdict
would not be.

### Consequences

- Changed what "done" looks like: the deliverable is a detector that
  surfaces and quantifies situations, not a classifier scored on
  accuracy alone. Validation in `PLAN.md` §4 reports accuracy against a
  baseline *and* the asymmetry/correlation findings, because either
  number alone would be misleading.
- Improved the architecture fit: an event-driven detector is a natural
  Lambda workload (react to new telemetry, emit a detection); a batch
  backtest against historical outcomes is not. Phase 1's handler is
  simpler as a direct result.
- Required rebuilding the tire-degradation signal from scratch after
  discovering it understated real degradation by roughly 12x (measured
  directly against Bahrain STR/TSU) — a consequence of taking the
  reframe seriously enough to hold the new detector to real ground-truth
  checks rather than trusting the original signal by default. Full
  account in `PHASE_0_LOG.md` §5.

---

## ADR-003: Repair dtypes on read, rather than changing the raw storage format

**Status:** Decided, implemented (Phase 1)

### Context

`local_run.py` writes raw lap telemetry to CSV via `laps.to_csv()`. CSV
has no way to record pandas dtypes — every column round-trips as a
string or gets dtype-inferred by `pd.read_csv()` on the way back in. Two
real bugs came from this: `Timedelta` columns (`Time`, `LapTime`,
`PitInTime`, etc.) come back as plain strings, and `TrackStatus` — a
string field whose values (`"1"`, `"2"`, ...) all happen to look numeric
— gets silently inferred as `int64`, breaking every exact-string
comparison against it.

### Options

**A. Switch the raw storage format to something dtype-preserving**
(e.g. Parquet), so `Timedelta` and string-that-looks-numeric columns
round-trip correctly with no extra handling.

- Pros: fixes the root cause once, for every column, permanently
- Cons: changes the storage format used across the whole pipeline
  (local runner, S3 `raw/` prefix, and anything downstream that reads
  from it, including Phase 2's planned Athena table); a format change at
  this stage is a larger, riskier edit than the alternative, for a
  problem with a narrow, already-understood scope

**B. Repair known-affected columns explicitly on read, in `handler.py`**
Keep CSV as the storage format. Cast the specific `Timedelta` columns
with `pd.to_timedelta()` and force `TrackStatus` to `str` immediately
after reading the CSV, before any downstream logic runs.

- Pros: small, targeted fix; storage format stays simple and
  human-readable; every future column added to the pipeline still needs
  the same kind of check, but the pattern (verify dtype assumptions
  after any CSV round-trip) is now established and documented
- Cons: doesn't generalize automatically — a new Timedelta or
  numeric-looking string column added later needs to be added to the
  explicit repair list by hand, or it will reproduce the same class of
  bug silently

### Decision

Chose **B — repair on read**, scoped to Phase 1.

### Rationale

Both known bugs had a narrow, fully diagnosed cause. Fixing them where
they surface keeps the fix small and auditable, and avoids a storage
format migration whose benefit — at current pipeline size — is mostly
theoretical. The tradeoff is deliberately revisited rather than ignored:
`PHASE_1_LOG.md` §9 flags Parquet as the fix if this pipeline ever
outgrows CSV's limitations, specifically because Phase 2's Athena table
will be reading from `processed/`, and if that table ever needs to read
`raw/` directly, the dtype-repair burden would move with it.

### Consequences

- `handler.py` carries an explicit dtype-repair block that has no
  purpose other than working around CSV's limitations — a reader
  unfamiliar with the history could reasonably ask why it's there. This
  document and `PHASE_1_LOG.md` §5 are the answer.
- Any new column added to the pipeline that is either a `Timedelta` or a
  numeric-looking string needs to be added to `TIMEDELTA_COLUMNS` (or
  given an equivalent explicit cast) by hand — this is a known, not
  hidden, maintenance cost.
- Format migration to Parquet remains available as a future fix and is
  tracked as a candidate follow-up rather than scoped into Phase 1 or 2.
