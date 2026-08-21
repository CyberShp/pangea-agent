---
name: pangea-agent
description: Start and advance this repository's PANGEA test-analysis graph in DSH. Use for natural-language repository, module, risk, flow, requirement, design, Coverage, test-case, and explicit Run requests. Also use whenever a delegated Agent receives a graph-generated JSON path under agent-tasks/analysis, agent-tasks/rework, or agent-tasks/review, including review-independent.json, review.json, and rework-review.json.
---

# PANGEA Agent

Use this Skill only as the DSH entry into the repository's existing PANGEA graph. Do not
reimplement the graph, schemas, rubrics, worker roles, or report generation here.

If the current prompt is a graph-generated path under `agent-tasks/analysis/`,
`agent-tasks/rework/`, or a review task path, this is a delegated Agent call. Read
`.agents/pangea/dsh.md`, then the matching `.opencode/agents/analysis-worker.md` or
`.opencode/agents/review-worker.md`, then the task. Do not read
`.opencode/agents/pangea-agent.md`, do not start or resume a Run, and do not follow the root
sections below; the selected worker role owns the rest of this call.

Otherwise this is a root-Agent request. Read `.agents/pangea/dsh.md` and
`.opencode/agents/pangea-agent.md` completely before acting.

The DSH `bash` tool uses the host shell. On a POSIX host, use POSIX commands and never send
PowerShell cmdlets to `bash`; the repository's Windows compatibility rules do not change the
actual DSH tool shell.

Choose the repository virtual-environment interpreter once for the current DSH root session and
reuse that exact command for every PANGEA CLI call:

- POSIX host: `.venv/bin/python`
- Windows PowerShell host: `& '.\.venv\Scripts\python.exe'`

If the selected path does not exist, stop and report that PANGEA must be initialized. Do not try a
different Python executable, install packages, or build a fallback chain.

## Start a new analysis

When the user requests a new analysis and does not explicitly name a historical `run_id`:

1. Treat the current Run as empty. Do not call `pangea_status`; do not list or read
   `pangea-data/runs/`; do not read an existing pending contract.
2. Determine only the requested repository and smallest `source_scope` under
   `pangea-data/repositories/`. Every `source_scope` item is relative to the selected repository
   root and always uses `/` as the separator, including on Windows: for repository
   `acceptance-demo` and directory `module`, write `"module"`, never
   `"acceptance-demo/module"` or a backslash path. If the request already supplies them, do not
   explore further.
3. Delete the exact temporary path `pangea-data/.pangea/pending-task-contract.json` without
   reading it. Use `rm -f` for that file only on POSIX. On Windows PowerShell use
   `Remove-Item -LiteralPath 'pangea-data/.pangea/pending-task-contract.json' -Force -ErrorAction SilentlyContinue`.
   Then write it from the current request. For one
   repository, include `repository: "<repo_id>"` and omit `repositories`; for multiple
   repositories, include a non-empty `repositories` list and omit `repository`. Also include
   only `data_root`, `mode=module_analysis`, `target`, `source_scope`, and optional `focus`.
   `source_scope` is always a JSON array, even for one path. Never include a `run_id` or reuse
   old contract content.
4. With the interpreter selected above, run the matching command: POSIX
   `.venv/bin/python -m pangea_agent.cli.main module-analysis --contract
   pangea-data/.pangea/pending-task-contract.json`; Windows PowerShell
   `& '.\.venv\Scripts\python.exe' -m pangea_agent.cli.main module-analysis --contract
   'pangea-data/.pangea/pending-task-contract.json'`.
5. After the command returns the new `run_id`, delete the pending contract in a separate tool
   call. Keep that `run_id` as the only current Run for this DSH root session.

Do not inspect CLI help, schemas, dependencies, historical Runs, or reports before starting.
If the selected virtual-environment interpreter is unavailable, stop and report the initialization
requirement; do not install or upgrade anything.

## Advance the current Run

Follow the returned `phase` and graph-generated task paths:

- The main Agent may read tasks, dispatch or resume the required subagent, record its
  `subagent_id`, run the declared validation CLI, and call `resume-run`.
- The main Agent does not read worker role files or restate task content. A first-dispatch
  subagent prompt contains exactly the graph-returned task JSON path and nothing else; the
  delegated Agent loads its role through `AGENTS.local.md` and `.agents/pangea/dsh.md`.
- The main Agent must not create, fill, edit, normalize, or repair analysis, rework, or review
  semantic result files. Only the subagent holding that task may write them.
- Analysis uses one continuing worker session for checkpoint, risks, and tests. Review uses one
  continuing reviewer session for independent review, comparison review, and any rework
  verification.
- First dispatch always sets `run_in_background=true`; omitting it creates a one-shot result in
  DSH. Save and record the returned `subagent_id` before waiting.
- Immediately after dispatch, run `record-agent-session` with that `subagent_id`; no other tool
  call comes first. A subagent ID is not a job ID, so never pass it to `job_output`.
- Bind the `data_root` returned for the new Run together with its `run_id`. Pass the literal
  `--data-root <data_root>` to every run-scoped `record-agent-session`, `resume-run`, and
  `mark-reviewer-unavailable` command, including the default `pangea-data`. Do not rely on a CLI
  default or recover after first probing the wrong directory.
- Do not record ordinary `send_message` continuations again. During formal rework, resume the
  original analysis worker when possible; only a graph-authorized replacement dispatch records
  the new ID with `--role rework --unit-id <unit_id>`.
- Wait by checking `list_agents`. While the target is `running`, do not read its result or send
  the next stage; wait exactly twenty seconds with one host-shell call (`sleep 20` on POSIX or
  `Start-Sleep -Seconds 20` on PowerShell), then check `list_agents` again. Do not lengthen a wait
  to 30, 45, or 60 seconds because the host tool may time out; repeat the same twenty-second step
  instead. Continue only after the target is `ready` or `inactive`.
- Do not queue the next analysis stage while the worker is still running. Continue only after
  the current turn returns its expected `STAGE` marker and the result file records that same
  stage; a failed stage must not be skipped.
- `STAGE checkpoint` and `STAGE risks` pause only the continuing worker. Do not call
  `resume-run` until the tests stage passes `validate-worker-result`.
- If dispatch, resume, validation, or artifact writing fails, stop and report the real phase.
  Do not replace missing results with main-Agent output.
- Rework verification must use the original reviewer. If that reviewer cannot be resumed, use
  the selected interpreter to run `-m pangea_agent.cli.main mark-reviewer-unavailable` with the
  bound reviewer ID and real reason, then use it again for `-m pangea_agent.cli.main resume-run`
  to produce `UNRESOLVED`; do not dispatch a substitute reviewer.

Continue until the graph returns a terminal quality result and generates `report.md` and
`report.html`, or until a real failure requires an honest stop.
