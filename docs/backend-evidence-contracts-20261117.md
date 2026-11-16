# Backend evidence contracts candidate — 2026-11-17

Worktree: `/Volumes/Media/pangea-agent-backend-20261117`; branch `codex/backend-contracts-20261117`; base `390aa7d11d062f523ff2b68331317ff09be1890c`. New Runs freeze the contracts through scene_spec. Historical Runs retain their frozen rules. No live Run, user repository, task lifecycle or plugin frontend was changed.

## Scope

- `instrumentation-readiness-v1`: generation rubric distinguishes reproducible public inputs from internal fault construction. Explicit injection wording combined with ready returns an exact field advisory. Python never silently changes readiness; negations and existing facilities can yield an advisory requiring the original worker's judgment.
- `source-facts-v1`: frozen-source path/line bounds and explicit repo/path/symbol tuples are checked. Symbol token presence does not prove a function definition or call reachability. Lazy resolution avoids requiring frozen source for records without source coordinates. The same-worker write response binds warnings to action/task/result/record/revision and retires them on explicit supersession.
- `high-risk-caller-falsifier-v1`: the existing Analysis worker performs one bounded caller investigation for its declared High/Critical risks, at most two levels, and records original grade, depth, outcome, exact source quotations and reasoning. Missing evidence remains unresolved. Python checks these declarations and quotations; it does not decide whether a guard actually blocks a risk, downgrade severity, dispatch another model or start a repair loop. This is an in-task bounded investigation contract, not an independent Reviewer.
- `interface-boundary-reconciliation-v1`: report aggregation compares exact repo/path/symbol declarations from different units and explicit producer/consumer error-code sets, and checks risk/case/flow ID references for missing or ambiguous targets. Differences remain evidence requests because callers can have different preconditions. Matching sets do not establish runtime correctness. Missing declarations are evidence_pending, never a pass.

## Validation

Reproducible tracked suite: `PYTHONPATH=src python verification/backend_evidence_contracts.py` (11 tests). It covers valid and invalid coordinates, false-ready advisories, caller quotes/depth/original grade, unresolved outcomes, missing and ambiguous cross-unit references, supersession, exact worker binding and preservation of completion state. 1,000 cached small-file coordinate checks took 0.0094 s locally; this is not an arbitrary whole-Run <1 second guarantee.

Additional local checks passed: existing backend diagnostics 12; coverage references 6; flow-path diagnostics 3; retired-model-review policy 7; report delivery smoke (including supersession and historical closure compatibility). These are deterministic fixture tests, not real-model output-quality acceptance. The separately borrowed legacy result-store pagination mismatch reproduces on unmodified base and remains documented in the Desktop checkpoint.

No semantic failure gates, automatic severity rewriting or model API calls were added. No component pin should reference this candidate until its commit is explicitly authorized and pushed. Native/package tests must use the resulting exact SHA after authorization.
