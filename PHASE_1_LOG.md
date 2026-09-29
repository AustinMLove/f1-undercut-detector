# Phase 1 Engineering Log — AWS Core (S3 + Lambda)

Companion to `PHASE_0_LOG.md`, covering the move from a working local
pipeline to a working, event-driven AWS pipeline. Same purpose: a running
record of what was built, what broke, and why each fix was made — source
material for the TWP and for explaining this work later, not a polished
narrative written after the fact.

---

## 1. Local entry point (`config.py`, `local_run.py`)

Before touching AWS, wrote a local runner mirroring the shape Phase 1's
Lambda handler would eventually need: `load_race()` → `find_candidates()`
→ write output, using the same `raw/`/`processed/` prefix layout S3 would
use. `config.py` reads bucket/prefix names from environment variables even
locally, since Lambda has no other way to receive configuration — building
that habit before it was required meant zero rework later.

First real run against 2024 Bahrain reproduced the exact validated
STR/TSU figures from Phase 0 (`gap_seconds: 2.643`, `break_even_laps: 4`,
`advantage_first_lap_seconds: 0.739`) — a clean regression check
confirming the local pipeline still matched everything Phase 0 had
already verified before any AWS work began.

---

## 2. S3 bucket, prefixes, and a region mismatch

Created the bucket with default settings (Block Public Access on,
default SSE-S3 encryption) and two prefixes, `raw/` and `processed/`.
Manually uploaded one race's raw laps to confirm the key layout before
anything automated touched it.

**Bug caught before it cost debugging time later:** the bucket was
created in `us-east-2`, while ECR and Lambda had already been set up in
`us-east-1`. S3 event notifications can only invoke a Lambda function in
the *same region* as the bucket — this isn't configurable, so the
mismatch would have made the eventual trigger silently never fire, with
no error message pointing at the actual cause. Fixed by recreating the
bucket in `us-east-1` as `f1-undercut-detector-dev`, which also required
updating the IAM policy's three `Resource` ARNs (S3 ARNs are global-format
and don't encode region, but they do encode the bucket *name*, which
changed along with the region).

---

## 3. IAM execution role and least-privilege policy

Created a Lambda execution role via the console's service-linked flow
(handles the trust policy — "Lambda is allowed to assume this role" —
automatically). Attached `AWSLambdaBasicExecutionRole` (CloudWatch Logs
write access only) plus a hand-written scoped policy:

- `s3:ListBucket` on the bucket ARN, scoped further with a
  `Condition: StringLike s3:prefix` restricting it to `raw/*` and
  `processed/*` — without the condition, `ListBucket` would expose the
  whole bucket's contents, not just the two relevant prefixes.
- `s3:GetObject` scoped to `raw/*` (object-level ARN, with `/*`).
- `s3:PutObject` scoped to `processed/*` (object-level ARN, with `/*`).

No `s3:*`, no wildcard resource, no broad managed policy attached.

---

## 4. Pandas packaging: decision and proof-of-concept

Lambda's runtime ships with no third-party packages, and pandas depends
on numpy's compiled, architecture-specific binaries — installing it
locally and zipping it up produces a package that crashes on import in
Lambda's actual execution environment.

Evaluated two solutions and documented the choice as a standalone
decision record (`DECISIONS.md`):

- **AWS-managed Lambda layer** (`AWSSDKPandas-PythonXXX`) — attach a
  pre-built, AWS-maintained layer directly from the console, no build
  step required.
- **Container image**, built `FROM public.ecr.aws/lambda/python`, with
  `pip install` happening inside the build so installed binaries are
  guaranteed to match Lambda's real runtime.

Chose the container image path — not because the layer was wrong, but
because it extends existing Docker/CI-CD experience into a new context
and avoids version lock-in for any future dependency, at the cost of
more setup than the layer would have needed.

Built a throwaway proof-of-concept (`requirements.txt`: `pandas`;
`handler.py`: import pandas, build a DataFrame, return its sum) and
verified it two ways before deploying anything: locally via the Lambda
Runtime Interface Emulator (`docker run -p 9000:8080 ...` + `curl` against
the RIE's invoke endpoint), then for real after pushing to ECR and
creating the actual Lambda function. Both confirmed pandas importing
cleanly inside the real target environment.

**Bug hit along the way:** the first image push failed at Lambda function
creation with *"the image manifest, config or layer media type... is not
supported."* Root cause: modern Docker Desktop's default `docker build`
attaches provenance/SBOM attestations, wrapping the image in an OCI
manifest-list format Lambda doesn't accept. Fixed by rebuilding with
`--provenance=false --sbom=false`, which restores a plain, single-manifest
image.

---

## 5. Building the real handler

Replaced the throwaway `handler.py` with one that parses the S3 event,
pulls the raw CSV, and calls `find_candidates()` unchanged — the whole
reason `candidate_search.py` was refactored to take a DataFrame rather
than fetch its own session.

**Fix required in `candidate_search.py`:** `import fastf1` sat at module
level, meaning importing `candidate_search` at all — which `handler.py`
does — pulled in `fastf1` and its dependency tree even though the Lambda
path never calls `load_race()`. Moved the import inside `load_race()`
itself, a function-scoped import, so only local development (which
actually calls that function) pays for that dependency.

**Two CSV round-trip dtype bugs, found via a zero-candidates run:**

1. `Timedelta` columns (`Time`, `LapTime`, `PitInTime`, etc.) come back
   from `pd.read_csv()` as plain strings, not `Timedelta` objects, which
   every gap/tyre-age calculation depends on. Fixed with an explicit
   `pd.to_timedelta()` pass over each affected column after reading the
   CSV.
2. `TrackStatus` — a string field whose values (`"1"`, `"2"`, etc.) all
   happen to look numeric — got silently inferred as `int64` by
   `pd.read_csv()`. Every downstream filter does exact string comparison
   (`row["TrackStatus"] != "1"`), so an `int64` column made every green-flag
   check fail simultaneously, in both `find_candidates()`'s pit-lap loop
   and `compute_relative_degradation()`'s cleaning filter — independently
   producing zero usable output from two different code paths, which is
   why the first real handler run returned zero candidates with no error
   at all. Fixed with an explicit `.astype(str)` cast after read.

Both are the same underlying class of bug: FastF1's live DataFrame carries
dtype information a CSV file cannot, so anything read back from CSV needs
explicit dtype restoration before it can be trusted to behave like the
original.

---

## 6. Resource sizing: default memory and timeout were both wrong

First real invocation of the finished handler failed with:
```
Init Duration: 9999.44 ms  Phase: init    Status: timeout
Duration: 3000.00 ms       Phase: invoke  Status: timeout
```
Lambda's defaults — 128 MB memory, 3-second timeout — are sized for
trivial functions, not one importing pandas and numpy. Lambda's init
phase has a fixed, non-configurable 10-second ceiling; at 128 MB (which
also proportionally caps available CPU), importing pandas alone consumed
nearly all of it. Raised memory to 512 MB and timeout to 30 seconds,
which resolved both the init-phase timeout and the retry-during-invoke
timeout that followed it.

---

## 7. Automating the trigger

Added an S3 event notification (Properties → Event notifications),
scoped to the `raw/` prefix specifically — deliberately excluding
`processed/`, since a trigger with no prefix filter watching a bucket the
function also writes to would re-invoke itself on every write it makes.
Destination: the Lambda function directly. Creating the notification
attaches a resource-based policy to the function (separate from the
execution role — this one governs who may *invoke* the function, not what
it's allowed to *do*), handled automatically by the console.

---

## 8. Validation

Two end-to-end checks, both passing:

- **2024 Bahrain, re-run through the finished Lambda path** (manual test
  event): 7 candidates, matching `local_run.py`'s output exactly,
  including the STR/TSU figures already validated in Phase 0
  (`break_even_laps: 4`, `gap_seconds: 2.643`).
- **2025 Spain, uploaded with no manual invocation at all**: the event
  notification fired the Lambda automatically, producing
  `processed/2025_Spain/candidates.json` — 7 candidates, correct mix of
  `quantified`/`not_applicable` statuses, degradation fits consistently
  reused per compound (`SOFT`: r² 0.065, 434 sample laps across all three
  SOFT candidates; `MEDIUM`: r² 0.113, 365 sample laps across all three
  MEDIUM candidates). This is the actual proof the pipeline is
  event-driven end to end, not just correct when manually invoked.

---

## 9. Current state — Phase 1 complete

| Component | Status |
|---|---|
| S3 bucket (`f1-undercut-detector-dev`, `us-east-1`), `raw/`/`processed/` prefixes | Done |
| IAM execution role, least-privilege scoped policy | Done |
| Lambda function, container image (ECR), pandas packaging solved | Done |
| `handler.py` — S3 read, dtype restoration, `find_candidates()`, S3 write | Done |
| S3 event notification, automatic trigger on `raw/` uploads | Done |
| Verified: Lambda output matches local output exactly | Done |
| Verified: fully automated run (no manual invocation) | Done |

**Known follow-up, not yet fixed:** `local_run.py`'s CSV write
(`laps.to_csv()`) doesn't preserve `Timedelta`/dtype information, which is
why `handler.py` has to repair it on read. This is intentional given the
scope of Phase 1 — the fix lives on the read side rather than trying to
make CSV a dtype-preserving format — but it's worth remembering if this
pipeline ever needs a cleaner intermediate format (Parquet would carry
dtypes natively, unlike CSV).

---

## 10. What's next — Phase 2 (Athena + QuickSight)

Per `PLAN.md` §6: an Athena table over `processed/`, core queries
(detections per race, status breakdown, break-even distribution), and a
QuickSight dashboard surfacing detected battles with gap, tire-age delta,
break-even, and confidence. Gate: end-to-end from raw upload to dashboard,
every service choice explainable against a considered alternative,
demoable in under five minutes.
