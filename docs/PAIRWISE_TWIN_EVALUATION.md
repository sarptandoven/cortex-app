# Pairwise Digital-Twin Evaluation

For a start-to-finish architecture and Cortex product-integration walkthrough,
see
[PAIRWISE_TWIN_INTEGRATION_GUIDE.md](./PAIRWISE_TWIN_INTEGRATION_GUIDE.md).

## Status and claim boundary

The pairwise core is implemented as a sibling of the existing
`CortexStore.would_i()` path. It does not replace or alter the current scalar
twin API, event ledger, calibration, or scorecard.

The deterministic core, optional OpenAI Responses judge, blinded owner and
pointwise-scalar study artifacts, repeat-run analysis, and guarded retention
workflow are implemented. The current evidence still establishes only
**mechanical validity on a deterministic synthetic benchmark**. It does not
establish that the system better captures the owner's subjective taste. That
stronger claim requires actual blinded owner labels on held-out response pairs.

## Why pairwise

Absolute questions such as “is this response correct?” remain useful for
factual checks. They are a weak fit for tone, style, and taste, where people are
usually more consistent when comparing two concrete alternatives.

The implemented flow is:

```text
frozen cited profile
  → frozen candidates
  → deterministic pair schedule
  → adapter-redacted A/B judgment in both presentation orders
  → swap-consistency resolution
  → normalized logical outcomes
  → pluggable ranking backend
  → reliability and uncertainty metrics
```

## Package architecture

The provider-free core lives in `backend/app/twin_eval/`.

- `domain.py` defines immutable profiles, candidates, plans, judgments,
  resolved comparisons, ratings, and reports.
- `protocols.py` defines candidate-generator, judge, strategy, and ranker ports.
- `strategies.py` provides all-pairs, anchor/challenger, and repeated-swapped
  schedules.
- `scheduling.py` builds and validates the single deterministic schedule shared
  by preflight and execution, including constant-time built-in schedule-size
  rejection before plan allocation.
- `preflight.py` forecasts exact call counts plus assumption-bounded tokens,
  cost, and duration without invoking generators or judges.
- `admission.py` applies operator-owned hard ceilings and conservative
  assumption floors that API callers cannot weaken.
- `application.py` strictly parses product/API requests and exposes a reusable
  conservative budget gate for future execution workers.
- `profile_adapter.py` converts valid-time-filtered Cortex context packs into a
  minimized owner-authored profile, deterministic prompt scopes, safe coverage
  counts, and a digest manifest while forcing best-effort export redaction and
  rejecting authoritative context changes during a build.
- `profile_artifacts.py` validates and serializes the exact redacted profile,
  prompt scope, coverage, and builder manifest for CXE1-encrypted retention.
- `runner.py` validates a run, derives stage-specific seeds, builds prompt-level
  disclosure scopes, caches candidates, executes comparisons, resolves
  structurally valid swapped presentations, ranks normalized logical outcomes,
  and enforces count/size ceilings.
- `ranking.py` provides Bradley–Terry and simple win-rate backends.
- `metrics.py` computes order/repeat reliability and case-cluster bootstrap
  intervals.
- `observable.py` provides a deterministic, citation-grounded feature oracle
  for mechanical validation.
- `policies.py` enforces eligible or preregistered prompt-scoped citation
  evidence and can verify that quoted evidence occurs in each cited memory.
- `owner_study.py` exports blinded owner-label cohorts and computes paired,
  cluster-aware agreement.
- `scalar_study.py` exposes each identical frozen candidate once for blinded
  pointwise scoring, then derives equivalent pair outcomes using preregistered
  tie and absolute-failure thresholds.
- `openai_judge.py` provides a secret-free OpenAI Responses configuration and a
  cancellable, retrying, per-comparison process-isolated remote judge.
- `stability.py` measures repeat-run outcome entropy, inter-run agreement,
  top-rank stability, confidence variance, provider latency, usage, and cost.
- `repository.py` stores immutable user-scoped reports and normalized audit
  records in dedicated SQLite tables using compact candidate references and
  verifies exact replay digests.

The evaluation engine remains provider-independent; the OpenAI adapter uses
only Python's standard library and is optional. Persistence never writes
experiment artifacts into `memory_events`.

## Outcome semantics

The judge contract preserves six distinct results:

- `left` and `right`: decisive preference, requiring cited profile evidence;
- `tie`: both candidates are equally preferred;
- `both_bad`: both violate an absolute quality or hard-constraint floor;
- `abstain`: the frozen owner-authored profile lacks relevant evidence;
- `invalid`: malformed, ungrounded, or swap-inconsistent judgment.

`tie` contributes half a win to each item. `both_bad`, `abstain`, and `invalid`
are excluded from relative ranking and reported separately. This prevents
missing evidence from being silently converted into preference signal.

Central citation validation accepts only unique, active, positive-trust,
owner-authored IDs from the frozen profile. This is a syntactic provenance
check, not a semantic entailment check. Prompt relevance, source authenticity,
contradiction resolution, and rule derivation remain responsibilities of the
profile/rubric builder.

`PromptScopedCitationPolicy` makes a preregistered relevance/precedence decision
enforceable by limiting each prompt to an explicit memory-ID set. The runner
uses the same mapping before any provider call to construct a minimal
prompt-specific profile for both generators and judges. This prevents one
prompt's private evidence from influencing or being disclosed during another
prompt. Missing, unknown, empty, or ineligible scopes fail closed before
generation. It still does not prove that a cited memory entails the judge
rationale.

`QuotedEvidenceCitationPolicy` additionally requires every cited memory to have
a bounded NFKC-normalized quote that occurs in that memory. This blocks
hallucinated IDs and fabricated quotations. Quote occurrence is auditable
support, not proof of semantic entailment; claim-to-evidence entailment still
requires an independently calibrated semantic verifier or human review.

## Reproducibility

Canonical JSON and SHA-256 are used for content IDs and component seeds. Domain
metadata is recursively snapshotted into immutable JSON values. Seeds are
derived independently for each generation and judgment task, so parallel
execution cannot change results by consuming shared random state. Integer and
string seeds are intentionally distinct.

`metadata.spec_id` identifies the frozen specification, candidates, and
allowlisted reproducibility configuration. `run_id` additionally includes raw
judgment digests and an optional caller-supplied `trial_id`. The explicit trial
ID is required when independently executed stochastic trials may return
byte-identical judgments; otherwise content addressing would collapse them to
one stored run and bias stability analysis. `artifact_digest` hashes the
complete report, including rankings, for exact replay verification.

Remote model calls cannot be made mathematically deterministic. The OpenAI
adapter records its model, endpoint, reasoning effort, timeouts, retry policy,
prompt-contract version, provider response ID, attempts, latency, confidence,
usage, and optional caller-supplied token prices. Exact reproducibility means
replaying frozen artifacts; fresh provider reruns measure stability and share
a `spec_id`. API keys remain in the named environment variable and are never
included in reproducibility configuration or artifacts. Provider error bodies
are discarded because they can echo private input.

Judge inputs are identity-blind by default: the runner replaces candidate and
system IDs, seeds, and metadata with anonymous A/B views before calling the
judge. Identity-aware fixture judges such as `OracleJudge` require the runner to
opt out explicitly with `blind_judge_inputs=False`; this mode is not suitable
for product evaluation.

## Remote judge execution

`IsolatedOpenAIResponsesJudge` calls `/v1/responses` with a strict JSON schema.
Its prompt treats profile, evaluation prompt, and candidate strings as
untrusted quoted data; prohibits following embedded instructions; uses only
eligible owner evidence; defines `tie`, `both_bad`, and `abstain`; and requires
verbatim evidence quotes for decisive outcomes.

Every comparison executes in a spawned process. The child performs bounded
retries for timeouts, network errors, HTTP 408/409/429, and 5xx responses with
bounded exponential backoff. The parent enforces a hard deadline, can terminate
all active calls through `cancel()`, and converts provider, parsing, timeout,
worker-start, and cancellation failures into one auditable `invalid` judgment.
One bad provider response therefore does not abort the whole evaluation.

The default model is the cost-oriented `gpt-5.6-luna`, following the current
[OpenAI model guidance](https://developers.openai.com/api/docs/models). Model
selection remains explicit configuration, and a production study should pin a
stable model revision when the provider exposes one. The adapter sends
`store: false`, a privacy-preserving safety identifier, low verbosity, and
structured output. No real provider call is part of CI; provider transport,
retry, parsing, timeout, cancellation, and isolation are tested with fakes.
The request shape follows OpenAI's
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs),
and the hashed identifier follows its
[safety guidance](https://developers.openai.com/api/docs/guides/safety-best-practices).

## Bradley–Terry backend

The default backend fits Bradley–Terry abilities with a pure-Python
minorization-maximization update. A symmetric prior adds equal pseudo-wins in
both directions between every pair, keeping estimates finite without a
scale-dependent external reference.

The backend reports:

- empirical comparison-graph components;
- convergence, iterations, and maximum update;
- data log likelihood;
- comparisons, fractional wins, and ignored outcome counts.

Disconnected empirical graphs do not receive global ranks. Each item instead
gets a component-local rank, because scores across disconnected components are
not identified by observed comparisons. Equal scores share a dense rank.

The `WinRateRanker` consumes the same normalized outcome stream. Re-ranking
without regeneration or re-judging demonstrates that Bradley–Terry is an
extension point, not a hardcoded domain assumption.

Future backends can implement the same `RankingBackend` port. Useful candidates
include Davidson or Rao–Kupper models for explicit ties, Plackett–Luce for
listwise observations, Bayesian Bradley–Terry for posterior uncertainty, and
game-theoretic methods when preferences are materially non-transitive. New
backends must preserve ignored-outcome and disconnected-graph semantics.

## Zero-call preflight estimator

Run the benchmark estimator before any provider-backed evaluation:

```bash
python3 scripts/pairwise_twin_eval.py --estimate-only
```

The JSON result reports exact prompt, candidate-generation, raw-judgment,
logical-comparison, swap, and potential provider-call counts. It also reports
lower, expected, and upper token and duration forecasts. The command sets
`provider_calls_made` to zero and does not construct a generator or judge.

Add provider prices in USD per million tokens and conservative budgets:

```bash
python3 scripts/pairwise_twin_eval.py \
  --estimate-only \
  --generator-input-price 1 \
  --generator-output-price 3 \
  --judge-input-price 2 \
  --judge-output-price 4 \
  --max-provider-calls 600 \
  --max-total-tokens 5000000 \
  --max-cost-usd 15 \
  --max-duration-seconds 7200
```

Budget checks use the upper estimate, not the expected value. A violation is
printed as structured JSON and exits with status 1. Invalid or incomplete
assumptions exit as argument errors. `schedule_digest` can be compared with the
completed report's `metadata.reproducibility_manifest.plan_digest`.

Token, cost, and duration values are forecasts because candidate size, adapter
prompt wrappers, provider latency, and billing vary. Call counts and the
schedule digest are exact for deterministic strategies. Concurrency flags
forecast a future worker configuration; they do not make the current
synchronous runner parallel.

The authenticated product endpoint is available on both Cortex server planes:

```text
POST /v1/twin/pairwise/preflight
Authorization: Bearer <read-scoped token>
```

It accepts the frozen profile, prompts, system IDs, strategy, assumptions, and
budgets as bounded JSON. It returns only counts, hashes, ranges, and violations;
profile evidence and prompt content are not echoed. This is an estimator
endpoint, not an execution endpoint.

The REST boundary applies operator-owned defaults of 1,000 provider calls, 10M
upper-bound tokens, 24 hours, and sequential execution. Clients may request
stricter limits but cannot raise these ceilings. Size, token-output, request
overhead, latency, tokenization, and concurrency assumptions are hardened
against optimistic client values. The response includes a versioned
`policy_digest` for future queue admission.

When `CORTEX_PAIRWISE_ADMISSION_SIGNING_KEY` contains at least 32 bytes and
the estimate is within budget, the response also includes a short-lived,
HMAC-signed `admission_receipt`. Its signature binds the exact canonical
request, authenticated `user_id`, schedule digest, estimate digest, policy
digest, issue time, and expiry. The receipt contains no profile, prompt, or
candidate text. A future execution endpoint must rerun preflight under the
current policy and verify the receipt before making provider calls.

The receipt is deliberately not an execution authorization: preflight remains
read-scoped, while execution must require export scope, the
`allow_agent_exports` trust control, explicit consent, and a server-owned spend
limit. Receipts are stateless and therefore replayable by
the same user until expiry; a future execution service must consume a
one-time job id or nonce atomically.

The existing generic `memory_jobs` queue is not used for pairwise requests.
That queue stores `payload_json` in plaintext, returns payloads through generic
job APIs, has no cancellation operation, and dispatches only existing memory
job types. Private evaluation inputs require a dedicated encrypted,
retention-aware job store with redacted status views.

Provider cost is reported when pricing is supplied, but it is not yet a
server-enforced ceiling. Trusted pricing must come from the future
operator-selected execution configuration rather than from the requesting
client.

## Offline benchmark

Run:

```bash
python3 scripts/pairwise_twin_eval.py
```

Persist a completed run in a local Cortex database:

```bash
python3 scripts/pairwise_twin_eval.py \
  --seed 7 \
  --db-path /path/to/cortex.sqlite \
  --user-id local \
  --keyring-db-path /path/to/keyring.sqlite
```

Set `CORTEX_KEK` or `CORTEX_KEK_FILE` before using the encrypted CLI path. The
`--allow-plaintext-report` escape hatch exists only for bundled synthetic test
fixtures. Cortex/user reports must use the hosted keyring.

Verify and replay a stored artifact as a redacted summary:

```bash
python3 scripts/pairwise_twin_eval.py \
  --db-path /path/to/cortex.sqlite \
  --user-id local \
  --keyring-db-path /path/to/keyring.sqlite \
  --replay-run-id twin_eval_...
```

Stored artifacts are scoped by `(user_id, run_id)`. Identical writes are
idempotent, conflicting bytes under the same run ID are rejected atomically,
and cross-user lookups return no artifact. The replay command prints IDs,
digests, counts, and diagnostics only—not profile evidence, candidate text, or
judge rationales.

Storage schema `pairwise-twin-artifact/v2` is now sealed as one canonical CXE1
report artifact under the dedicated `twin_eval_report` key purpose. The outer
run and child rows contain only content-free markers, opaque hashed references,
digests, counts, and numerical ranking summaries. Prompt text, caller metadata,
candidate text, citations, and judge rationales exist only inside the
authenticated ciphertext. Outer references use a per-report HMAC key that is
itself sealed inside that ciphertext. The reader remains compatible with legacy v1/v2
plaintext artifacts, but new writes fail closed unless a cipher is available
or the caller explicitly enables local/test-only plaintext mode.

`TwinEvalRepository.delete_report()` removes one exact user/run artifact
atomically and can require the expected artifact digest. The bounded,
user-scoped `purge_reports_before()` method supports retention jobs without
cross-user deletion.

When a report carries a Cortex profile manifest, persistence now fails closed
unless the caller also supplies the exact server-built profile bundle, an
explicit expiry, and an available Cortex keyring. The repository stores that
bundle as one CXE1 AES-GCM ciphertext under the dedicated
`twin_eval_evidence` purpose. The encrypted inner envelope binds the user, run,
random artifact ID, profile fingerprint, prompt scope, builder manifest, and
expiry. Reads reject plaintext, wrong-user/purpose blobs, corruption, expired
artifacts, and any digest or report-link mismatch.

Verbatim provider `evidence_quote` values exist only long enough for citation
occurrence validation. Before a comparison enters a report, the runner removes
the quotes, redacts exact repeats from the rationale, and retains only
content-addressed quote digests.

`list_expired_profile_artifacts()` and
`purge_expired_profile_artifacts()` provide bounded preview/apply deletion for
evidence ciphertext. Routine Cortex backups deliberately omit the entire
pairwise run graph, preventing both decryptable history and broken marker-only
restores. The current per-user key hierarchy still cannot crypto-shred
one artifact in an out-of-band SQLite copy; destroying that copy or the user
key is required. Account deletion now counts and removes every
`twin_eval_*` row. Routine backups also remove encrypted full-report artifacts,
so deleting a run cannot leave a decryptable historical report in a normal
Cortex backup.

Retention uses SQLite date parsing instead of textual timestamp comparison.
Preview and apply use the same ordering, and apply atomically rejects a target
set that changed after preview. A 90-day default policy is exposed by:

```bash
python3 scripts/pairwise_twin_retention.py preview \
  --db-path /path/to/cortex.sqlite \
  --user-id local

python3 scripts/pairwise_twin_retention.py apply \
  --db-path /path/to/cortex.sqlite \
  --user-id local \
  --expected-preview-digest retention_preview_...
```

Set `CORTEX_TWIN_EVAL_RETENTION_DAYS` or pass `--retention-days` to change the
policy. The command is intentionally schedule-agnostic: invoke it from the
deployment's existing scheduler rather than creating a second in-process
timer. Apply is bounded to 100 records by default and 1,000 maximum.

### Legacy plaintext migration

Legacy v1/v2 reports require an explicit maintenance workflow. It never runs
at application startup.

```bash
# Preview one user-scoped, bounded batch.
python3 scripts/pairwise_twin_migrate.py preview \
  --db-path /path/to/cortex.sqlite \
  --user-id USER_ID \
  --limit 100

# Apply exactly the returned IDs and selection digest.
python3 scripts/pairwise_twin_migrate.py apply \
  --db-path /path/to/cortex.sqlite \
  --user-id USER_ID \
  --keyring-db-path /path/to/keyring.sqlite \
  --expected-run-id twin_eval_... \
  --expected-selection-digest pairwise_legacy_selection_...

# Require a clean logical audit.
python3 scripts/pairwise_twin_migrate.py audit \
  --db-path /path/to/cortex.sqlite \
  --user-id USER_ID \
  --keyring-db-path /path/to/keyring.sqlite \
  --require-clean

# Reject any managed backup without the verified sanitization receipt.
python3 scripts/pairwise_twin_migrate.py backup-audit \
  --db-path /path/to/cortex.sqlite \
  --vault-root /path/to/cortex-vault \
  --require-clean
```

Each run is replay-verified before mutation, encrypted and decrypted once for
verification, then atomically converted with `secure_delete=ON`. After every
user is clean, stop all Cortex processes and workers, close database handles,
and run the physical scrub:

```bash
python3 scripts/pairwise_twin_migrate.py finalize \
  --db-path /path/to/cortex.sqlite \
  --keyring-db-path /path/to/keyring.sqlite \
  --vault-root /path/to/cortex-vault \
  --exclusive-maintenance
```

Finalization acquires an exclusive operating-system maintenance fence that all
normal Cortex database connections and offline reranking reads honor. It fails
closed if this process or another Cortex process still holds the shared fence.
The operator must still stop all processes because an arbitrary SQLite client
that bypasses Cortex cannot be forced to honor an advisory lock.

While holding the fence and one SQLite connection, finalization requires zero
legacy/inconsistent rows, two successful `wal_checkpoint(TRUNCATE)` operations
around `VACUUM`, SQLite integrity and foreign-key checks, and replay of every
encrypted report. SQLite documents a rare WAL-reset race in older runtimes and
fixes in 3.51.3, 3.50.7, and 3.44.6
([SQLite WAL documentation](https://www.sqlite.org/wal.html)).

The command rejects any managed backup that lacks a verified
`backup-security.json` receipt binding its standalone sanitized SQLite digest.
It also binds `--vault-root` to the database path recorded in the vault
manifest and rejects every unexpected backup-directory artifact, including old
loose SQLite, WAL, journal, temporary, or directory entries.
Delete or separately migrate those pre-cutover backups. External Time Machine,
cloud, and volume snapshots remain an operator attestation and must be
inventoried before declaring production readiness.

The versioned `subjective-mechanical-v1` benchmark contains 24 cases, four in
each stratum. The existing Phase 5 categorical suite is a separate non-regression
gate; it is not included in this dataset digest.

1. clear style preferences;
2. explicit negative constraints;
3. near ties;
4. conflicting profile signals;
5. sparse or noisy evidence;
6. deceptive/adversarial responses.

Three frozen candidate systems are compared for every case. Every logical pair
is presented in both orders and repeated three times. The observable judge uses
only frozen candidate text and active owner-authored cited rules; candidate
identity is never a scoring feature.

The first evaluation loop found a fixture defect: seven expected labels asserted
a strict ordering even though the preregistered observable rules scored the two
compliant candidates equally. Those labels were corrected to ties. No ranker or
judge threshold was changed to fit the results.

Validated seeds `7`, `41`, and `97` each produced:

- 24 cases;
- 432 raw judgments;
- 216 resolved logical comparisons;
- synthetic pair-label accuracy `1.00`;
- swap agreement `1.00`;
- repeat agreement `1.00`;
- position bias `0.00`;
- invalid rate `0.00`;
- reported-confidence coverage `0.8333`, with mean `1.00` on covered
  deterministic oracle judgments;
- hard-constraint-violating winner rate `0.00`;
- a connected, converged Bradley–Terry fit;
- stable ordering `strong > partial > mismatch`.

The same frozen candidates and rules are also scored by a synthetic five-bin
oracle comparator. It is not the production Phase 5 scalar evaluator. At seed
7:

- pairwise recovery was `1.0000`;
- synthetic five-bin oracle recovery was `0.9861`;
- paired case-cluster bootstrap delta was `+0.0139`;
- deterministic 95% interval was `[0.0000, 0.0417]` over 24 case clusters.

Because the interval includes zero, this benchmark does **not** demonstrate a
statistically reliable advantage over the synthetic comparator. It shows
mechanical correctness and a small synthetic point difference only.

These values show that the machinery recovers declared observable preferences
and detects abstention, ties, and hard constraints. They are not measurements of
human taste.

The confidence values are fixture assertions, not calibrated probabilities.
Calibration error, Brier score, or selective-risk curves require owner labels.

The benchmark is intentionally circular at the semantic layer: expected labels,
the pairwise oracle, and the scalar baseline derive from the same frozen
observable rules. It is an integration benchmark, not evidence that pairwise
judging is better in the wild.

At three systems, three repetitions, and two presentation orders, pairwise mode
uses 18 raw judge calls per prompt versus three scalar ratings: a 6× raw-call
multiplier (3× when counting resolved logical pairs). All-pairs judging scales
as `O(prompts × systems² × repetitions)`. No decision-grade wall-clock or
provider-cost comparison exists yet because no real remote owner-labeled cohort
has been run. The scalar-study workflow now ensures the eventual production
comparison uses the exact same frozen candidates instead of the synthetic
five-bin shortcut.

## Adversarial review results

The second review pass exercises sparse evidence, contradictory fixtures, noisy
and hallucinated citations, identical and near-identical candidates, long
answers, malformed schedules/data, and deterministic/non-deterministic judges.

| Failure found | Resolution |
|---|---|
| A hard-constraint violator could win through enough soft features | Hard constraints now resolve lexicographically before soft utility |
| Two same-orientation records could report perfect swap agreement | Logical trials require at most one A/B and one B/A presentation |
| One trial could be duplicated under two logical IDs | Trial keys map to exactly one logical comparison |
| Duplicate candidate IDs silently aliased audit rows | Runner and repository reject conflicting candidate identities |
| Empty or partial schedules produced apparently valid reports | Empty plans and missing prompt/system coverage are rejected |
| Same-spec non-deterministic runs collided in persistence | `spec_id` and judgment-addressed `run_id` are separate |
| Non-deterministic schedules/rankers could alias IDs | Ordered plans enter `spec_id`; ranking output enters `run_id` |
| Unanimous malformed judgments reported perfect swap reliability | Invalid source pairs are counted separately and excluded |
| NaN/negative ranker or rubric values corrupted results | Numeric and feature-specific configuration validates eagerly |
| Zero-width Unicode evaded phrase constraints | The fixture oracle normalizes NFKC text and removes format controls |
| Mutable metadata could change IDs after construction | Artifact metadata is recursively immutable |
| CLI replay skipped normalized child-row verification | Replay cross-verifies the entire stored bundle |
| Unlimited answers/configurations amplified memory and storage | Runner applies prompt/system/plan/input/candidate/rationale/report ceilings |
| Provider hangs could stall the whole run | Every remote comparison has request and parent-enforced hard timeouts in a killable child process |
| Cancellation could race with pipe EOF and be misclassified | Cancellation state wins over worker EOF/exit during teardown |
| Provider errors could persist echoed private input | Error bodies/details are discarded; only failure classes are stored |
| Citation IDs could be real while supporting text was fabricated | Optional quote policy verifies every quote against the frozen cited memory |
| Scalar baseline used a non-equivalent five-bin fixture | Blinded pointwise scoring now uses each exact frozen candidate once |
| Stochastic variance and cost had no common report | Same-spec repeat analysis aggregates outcome entropy, agreement, confidence, latency, usage, and cost |
| Retention compared timestamp strings and could race after preview | SQLite date comparison and atomic expected-target checks guard deletion |
| Byte-identical stochastic replicates collapsed to one stored run | Optional `trial_id` preserves independent executions without changing `spec_id` |
| Reversed owner-study repeats double-weighted primary agreement | Repeats measure reliability only; main agreement uses each independent pair once |
| Disconnected runs produced a perfectly stable empty top-rank set | Top-rank stability is withheld unless every run has a global top |
| Extra pointwise-baseline pair IDs were accepted | Baseline outcomes must exactly cover the owner-study pair groups |
| Edited study files could retain apparently valid source IDs | Public cohorts and private mappings are reconstructed or cross-checked against the verified source |

An exact text match is not automatically converted to a tie: two identical
answers may both violate an absolute floor (`both_bad`) or lack profile evidence
(`abstain`). The judge retains those semantics, but a decisive left/right result
for NFKC-identical content is invalidated as evidence of leakage. Near-identical
answers remain a normal judgment and should be over-sampled in a real
calibration set.

## Current limits and supported scope

Default runner ceilings are 1,000 prompts, 100 systems, 100,000 raw plans,
2,000,000 canonical input characters, 200,000 canonical characters per
candidate, 100,000 rationale characters, and 50,000,000 canonical characters
in the complete report. These are safety rails, not a scalable execution
architecture.

The fixture oracle uses lightweight Unicode-aware token counting with
conservative character boundaries for CJK, Kana, Hangul, Thai, Lao, Myanmar,
and Khmer. This closes long-answer bypasses but is not linguistic word
segmentation. Product evaluation still needs locale-aware segmentation or
model-token limits.

Comparison rows no longer duplicate candidate text. A future high-cardinality
version can remove the remaining two-copy candidate representation by
reconstructing reports entirely from normalized rows or object storage.

## Testing

CI-discovered tests under `backend/tests/` cover:

- canonical IDs and deterministic/permutation-invariant schedules;
- exact balanced presentation pairs;
- swap resolution counted once for ranking;
- multi-candidate utility oracles;
- citation validation and distinct ignored outcomes;
- connected/disconnected Bradley–Terry graphs;
- shared ranks and backend permutation invariance;
- exact replay and run-identity sensitivity;
- swap, repeat, and position-bias metrics;
- case-cluster and paired bootstrap behavior;
- adversarial benchmark gates and multi-seed stability;
- duplicate identities, malformed/partial schedules, metadata immutability,
  quota enforcement, Unicode evasions, ineligible citations, and
  non-deterministic trial identity.

The existing Phase 5 scalar suite remains the non-regression gate and is not
rewritten around pairwise evaluation.

## Required next stage for a subjective claim

Collect a preregistered owner-label pilot:

1. freeze unseen tasks, cited-profile snapshots, and candidate responses;
2. blind candidate and generator identity;
3. randomize presentation order and repeat a subset in reverse;
4. allow `tie` and `neither`;
5. keep calibration examples separate from the reported set;
6. report owner self-consistency, judge agreement, clustered confidence
   intervals, latency, and cost;
7. compare scalar and pairwise methods on the identical held-out cohort.

Only that study can support the statement that pairwise evaluation better
captures the owner's taste.

The repository now includes a blinded workflow for this study. First persist a
frozen run, then export a public cohort, private decoding key, and label
template:

```bash
python3 scripts/pairwise_twin_owner_study.py export \
  --db-path /path/to/cortex.sqlite \
  --user-id local \
  --run-id twin_eval_... \
  --public-out owner-cohort.json \
  --key-out owner-cohort.private.json \
  --labels-out owner-labels.json \
  --seed 7
```

The public file contains prompts and anonymous response A/B text only. The
private key is written with mode `0600`; keep it away from the labeler. After
filling every label with `a`, `b`, `tie`, `both_bad`, or `abstain`, run:

```bash
python3 scripts/pairwise_twin_owner_study.py analyze \
  --db-path /path/to/cortex.sqlite \
  --user-id local \
  --public owner-cohort.json \
  --key owner-cohort.private.json \
  --labels owner-labels.json \
  --output owner-analysis.json
```

Analysis reports owner reversed-repeat consistency, displayed-position bias,
pairwise agreement, and a prompt-clustered confidence interval. Hidden reversed
repeats contribute to repeat consistency and position-bias diagnostics but are
excluded from the primary agreement estimate, so selected pairs are not
double-weighted. An optional baseline mapping must exactly cover the independent
pair groups and adds paired scalar agreement and a pairwise-minus-baseline
interval. Analysis also deterministically reconstructs the public cohort and
private key from the verified source report, rejecting edited response text,
mapping, or item order. Scalar export performs the same owner-key verification,
and scalar baseline conversion cross-checks candidate-to-prompt/system mappings.

Build that equivalent scalar baseline from the same frozen candidates:

```bash
python3 scripts/pairwise_twin_owner_study.py scalar-export \
  --db-path /path/to/cortex.sqlite \
  --user-id local \
  --owner-key owner-cohort.private.json \
  --public-out scalar-cohort.json \
  --key-out scalar-cohort.private.json \
  --scores-out scalar-scores.json

# Fill every pointwise score with a number from 0 through 100.
python3 scripts/pairwise_twin_owner_study.py scalar-baseline \
  --owner-key owner-cohort.private.json \
  --scalar-key scalar-cohort.private.json \
  --scores scalar-scores.json \
  --output scalar-baseline.json \
  --tie-margin 2 \
  --both-bad-at-or-below 10
```

Pass `--baseline scalar-baseline.json` to `analyze`. The baseline artifact is
bound to the owner cohort, source run, artifact digest, score digest, and
thresholds, so results from a different candidate set cannot be substituted.

For stochastic remote trials, persist at least two runs with the same `spec_id`
and a unique `trial_id` per execution, then analyze them together:

```bash
python3 scripts/pairwise_twin_stability.py \
  --db-path /path/to/cortex.sqlite \
  --user-id local \
  --run-id twin_eval_trial_1 \
  --run-id twin_eval_trial_2 \
  --output stability.json
```

Reported latency is provider-call latency captured by the adapter, not complete
queue or process-start wall time. Cost is reported only when the run config
supplies explicit input/output token prices; this avoids silently applying
stale prices. Stability rejects duplicate run IDs. It reports top-rank
stability only when every included run has an identifiable global rank; a
disconnected ranking can no longer look perfectly stable merely because every
top-rank set is empty.

## Product integration boundary

This remains an experimental/developer mode and intentionally does not register
REST, MCP, or generic worker routes. The adapter now covers cancellation,
retention/deletion mechanics, prompt-injection boundaries, structured-output
validation, quote verification, and per-record timeout/retry isolation.
Product routing still requires authenticated ownership checks, provider
allowlists, profile minimization/redaction, explicit consent, quotas and
budgets, scheduler integration, semantic entailment calibration, and completed
owner-labeled calibration. Those obligations are outside the local CLI.

The content-free Cortex profile manifest is copied into persisted run metadata.
The encrypted repository path retains the exact redacted evidence for citation
audit and seals the complete report separately. No execution route invokes
that path yet. `as_of` filters
memory validity; it does not reconstruct historical active/supersession state.
Export redaction covers known sensitive patterns but is defense in depth, not a
proof that arbitrary free text contains no secret. Production provider calls
therefore still require explicit consent. Existing legacy plaintext reports and
backups require an explicit verified migration and secure cleanup before remote
production execution can be enabled.

The disabled execution foundation now includes a durable dispatch authority:
an operational config epoch and each user's consent are revalidated under the
same SQLite write lock used by revocation. Config changes invalidate old
consent, exact expiry boundaries fail closed, backups omit consent rows, and
account deletion removes them. A private candidate-only begin-call transaction
now binds that authority to the live lease, exact prompt-scoped adapter input,
trusted endpoint registry, reservation budget, and one encrypted checkpoint.
An atomic second transaction now authenticates and consumes that checkpoint
exactly once, burns the raw in-memory permit after commit, and marks an expired
consumed handoff as `outcome_unknown` rather than retrying it. Content-free
dispatch counters and an ambiguity tombstone survive encrypted checkpoint
retention, and the state-table migration is crash-atomic. A strict offline
OpenAI Responses candidate parser is bound by endpoint model, parser revision,
text limits, and token ceilings; its normalized result is deliberately not
trusted until a future recorder seals it to the authenticated call. The system
has no public consent/enable route, provider transport,
successful/provider-failed outcome recorder, judge checkpoint, worker loop, or
network path.

## Research-informed opportunities

Recent work suggests the following next experiments:

- Measure non-transitivity and compare round-robin Bradley–Terry with adaptive
  Swiss-style matchmaking rather than relying on one anchor
  ([Xu et al., 2025](https://arxiv.org/abs/2502.14074)).
- Add a Davidson/Rao–Kupper tie backend instead of treating ties as half a
  Bernoulli win
  ([Chen et al., 2024](https://arxiv.org/abs/2409.17431)).
- Add distractor, persuasion, length, and formatting attacks to
  meta-evaluation; pairwise protocols are not uniformly more robust than
  pointwise scoring
  ([Tripathi et al., 2025](https://arxiv.org/abs/2504.14716),
  [Raina et al., 2024](https://aclanthology.org/2024.emnlp-main.427/)).
- Optimize and audit evaluator prompt fairness across semantically equivalent
  instructions rather than treating one prompt as canonical
  ([Zhou et al., 2024](https://aclanthology.org/2024.emnlp-main.72/)).
- Report benchmark separability and confidence intervals, and test style
  controls and multi-judge ensembles where owner labels support them
  ([Arena-Hard-Auto](https://github.com/lmarena/arena-hard-auto)).

These are evaluation-design recommendations. Preference optimization such as
DPO should not consume synthetic twin judgments until a held-out owner study
demonstrates calibration, stability, and acceptable failure rates.

## Review decision

Approve the deterministic core, repository, study tooling, and remote-adapter
contract for continued experimental use. Do **not** approve end-user production
routing yet. The remaining gates are empirical or deployment-specific:

- collect real blinded owner labels and frozen pointwise scores;
- run repeated remote trials to establish agreement, latency, token cost,
  confidence calibration, and variance;
- set product-specific acceptance thresholds and budgets;
- integrate authenticated ownership, consent, redaction, quotas, and the
  retention command with the deployment scheduler;
- calibrate semantic claim-to-citation entailment beyond quote occurrence;
- replace conservative character fallback with locale-grade segmentation where
  product rules depend on linguistic word counts.

No further local implementation can truthfully replace the owner labels or
real-provider measurements. Shipping before those gates would turn an
experiment with strong audit mechanics into an uncalibrated product claim.
