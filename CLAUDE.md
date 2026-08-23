# Claude Code Instructions for pangea-agent

## Language

Use Chinese for project discussion, planning, review notes, and user-facing explanations. Keep code symbols, protocol fields, configuration keys, paths, and error messages in their original language.

## Project intent

`pangea-agent` is a project-level testing-analysis agent. It turns source code, design materials, coverage information, and existing test cases into structured risks, test cases, and reports.

## Architecture source of truth

- Workflow: `src/pangea_agent/graph/`
- Node implementations: `src/pangea_agent/graph/nodes/`
- Data contracts: `schemas/`
- Analysis rubrics: `src/pangea_agent/rubrics/builtin/`
- Local user data layout: `pangea-data/`

Do not redefine workflow, schemas, or rubrics in ad-hoc prompts. Update the corresponding source file instead.

## Windows / PowerShell compatibility

Assume the primary local shell may be Windows PowerShell.

- Prefer one command per execution.
- Do not use `cd /d ... && ...`, POSIX path rewrites, `source`, `export`, `rm -rf`, or bash-only command chaining.
- Prefer Python module entrypoints: `python -m pangea_agent.cli.main ...` or the installed `pangea ...` command.
- Quote paths that may contain spaces or Chinese characters.
- Use project-file edit/read capabilities for file changes instead of shell redirection when possible.
- Never run destructive Git commands against user source repositories under `pangea-data/repositories/`.

## Development rules

- Keep the package name `pangea_agent` and project name `pangea-agent`.
- Prefer small, focused changes.
- Preserve user data directories listed in `.gitignore`.
- Do not add `tests/` to Git in the current project stage.
- Keep generated outputs, SQLite indexes, local source repositories, and run artifacts out of Git.

## Testing-analysis rules

- Source evidence should use repository-relative paths and line references.
- Risk output should explain reproducible trigger, system result, external observation, and exclusion condition.
- Test cases should include preconditions, steps, expected results, observability, and cleanup.
- When evidence is insufficient, record `UNRESOLVED` rather than inventing a conclusion.
- Treat `source_scope` as repository-relative paths that use `/` separators on every host, including Windows. Use it as the starting point. Deterministically include direct callers and target-related configuration, specifications, and tests without recursively expanding the call graph. Each analysis worker must complete both `source_scope` and `context_scope`.
- Before retaining a risk, check reachability, caller constraints or remedies, documented high-level behavior, and existing tests. Expected behavior must not be reported as a risk. Do not add another agent or review layer for this check.
- Analyze frozen source first, then consult the run-scoped material catalog and finally use Coverage. Risk-driven test generation is always required for executable risks. Requirement/design materials and Coverage are optional inputs only until they are relevant to the current target; every `decision=current` material and every matched Coverage gap must then be closed by a real TestCase or an explicit unsupported-entry conclusion. Freeze the risk set before writing test cases.

## Client compatibility

This repository also includes `AGENTS.md` for OpenCode and other agent clients. Keep `CLAUDE.md` and `AGENTS.md` aligned when changing project-level rules.

## Graph-owned worker lifecycle

- Python never calls a model API. Read the JSON tasks under the current run and write results to each declared `result_path`.
- Dispatch at most four non-overlapping `analysis-worker` tasks concurrently. Workers must not spawn child workers.
- Use one `review-worker` after analysis. Initial review and rework verification are one review lifecycle; allow at most one rework and require the same reviewer for verification.
- Initial review has two checkpoints in the same reviewer session: `independent_review` does not expose worker results, and `comparison_review` is generated only after the graph accepts the independent findings. Complete each checkpoint in one call.
- Before the reviewer returns from `independent_review`, `comparison_review`, or `rework_verification`, run `python -m pangea_agent.cli.main check-review-artifact --task "<review task JSON>"` and require `PASS`. A failure is fixed by the same reviewer in the same result file; the main Agent must not rewrite the review artifact to make it pass.
- Treat each `action=<JSON>` line returned by `module-analysis` or `resume-run` as the sole dispatch authority. Use `dispatch_agent` or `continue_agent` exactly as declared, send only `task_path`, and use the declared `task_id` for continuation. Do not infer work from `phase` or Agent response text.
- After a `dispatch_agent`, record the returned task ID with `record-agent-session`. Restore and continue that exact task ID when the graph returns `continue_agent`; do not record ordinary continuations again.
- Each analysis-worker turn performs only the current worker task `stage`, writes the same value to `completed_stage`, and runs `validate-worker-result` until `PASS`; that successful validation completes the currently bound session. Review completion is recorded only by a successful `check-review-artifact`. After the delegated Agent reports the turn complete, the main Agent follows `after_completion=resume_run` and runs `resume-run` without polling, reading artifacts, or recording completion itself. The next graph action alone decides the next stage.
- Start a new main-session analysis with `module-analysis`. Once that session has a concrete `run_id`, keep its literal `data_root` on every run-scoped CLI call; do not scan historical runs to choose one automatically. Never replace missing worker output with placeholder risks.

## Initialization contract

- When the user asks to initialize PANGEA, say that initialization is starting, then inspect `py -0p`, `.venv`, and pip.
- Select only Python 3.10, 3.11, 3.12, or 3.13. Stop and explain if none is installed; do not install Python silently.
- Before creating or recreating `.venv` or installing dependencies, show the selected version, path, and actions, then ask for confirmation.
- Keep the machine's internal pip source unchanged. If it fails, ask before using the repository's offline wheels and never rewrite pip configuration.
