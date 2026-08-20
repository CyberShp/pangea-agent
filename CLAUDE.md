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
- Treat `source_scope` as the starting point. Deterministically include direct callers and target-related configuration, specifications, and tests without recursively expanding the call graph. Each analysis worker must complete both `source_scope` and `context_scope`.
- Before retaining a risk, check reachability, caller constraints or remedies, documented high-level behavior, and existing tests. Expected behavior must not be reported as a risk. Do not add another agent or review layer for this check.
- Analyze frozen source first, then consult the run-scoped material catalog and finally use Coverage only to prioritize tests. Freeze the risk set before writing test cases.

## Client compatibility

This repository also includes `AGENTS.md` for OpenCode and other agent clients. Keep `CLAUDE.md` and `AGENTS.md` aligned when changing project-level rules.

## V1 worker lifecycle

- Python never calls a model API. Read the JSON tasks under the current run and write results to each declared `result_path`.
- Dispatch at most four non-overlapping `analysis-worker` tasks concurrently. Workers must not spawn child workers.
- Use one `review-worker` after analysis. Initial review and rework verification are one review lifecycle; allow at most one rework and require the same reviewer for verification.
- Initial review has two checkpoints in the same reviewer session: `independent_review` does not expose worker results, and `comparison_review` is generated only after the graph accepts the independent findings. Complete each checkpoint in one call.
- Before the reviewer returns from `independent_review`, `comparison_review`, or `rework_verification`, run `python -m pangea_agent.cli.main check-review-artifact --task "<review task JSON>"` and require `PASS`. A failure is fixed by the same reviewer in the same result file; the main Agent must not rewrite the review artifact to make it pass.
- After each Agent dispatch, record the returned task ID with `record-agent-session`. Restore task IDs from `progress.agent_sessions` after a main-session restart.
- Initial analysis uses three calls to the same worker session: checkpoint, risks/evidence, then tests/final validation. Resume after `[STAGE:checkpoint]` and `[STAGE:risks]`; these planned returns are not corrections or rework. If a stage returns empty without writing its marker, resume the same session and replace it only after two empty returns while keeping the same task, attempt, result path, and unfinished stage.
- Start a new main-session analysis with `module-analysis`. Once that session has a concrete `run_id`, use `resume-run` after completing the tasks for the current `phase`; do not scan historical runs to choose one automatically. Never replace missing worker output with placeholder risks.

## Initialization contract

- When the user asks to initialize PANGEA, say that initialization is starting, then inspect `py -0p`, `.venv`, and pip.
- Select only Python 3.10, 3.11, or 3.12. Stop and explain if none is installed; do not install Python silently.
- Before creating or recreating `.venv` or installing dependencies, show the selected version, path, and actions, then ask for confirmation.
- Keep the machine's internal pip source unchanged. If it fails, ask before using the repository's offline wheels and never rewrite pip configuration.
