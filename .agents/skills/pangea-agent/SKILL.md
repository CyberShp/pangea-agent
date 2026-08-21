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
   `<data_root>/repositories/`; substitute the exact `data_root` from the current request, including
   the default `pangea-data`. Never probe the default root first when the request supplies another
   one. Every `source_scope` item is relative to the selected repository
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
   Encode `focus` as a JSON array in every contract; one natural-language focus becomes a
   one-item array, and an omitted focus becomes `[target]`.
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

Treat every `action=<JSON>` line returned by `module-analysis` or `resume-run` as the sole
authority for the next Agent turn. `phase` is display information only. Do not infer dispatch,
continuation, stage completion, or replacement from `phase` or from subagent response text.

- For `action=dispatch_agent`, start one persistent subagent with `run_in_background=true`. Its
  prompt contains exactly `task_path` and nothing else. Save the returned `subagent_id`, then make
  `record-agent-session` the next tool call. Use action `role` and `unit_id`; record no `unit_id`
  when it is null. A subagent ID is not a job ID, so never pass it to `job_output`.
- For `action=continue_agent`, send exactly `task_path` to the action's `task_id`. Do not create a
  new subagent and do not record an ordinary continuation again.
- The action `stage` decides what the delegated Agent executes. Analysis stages for one unit use
  the same persistent worker; `independent_review`, `comparison_review`, and any
  `rework_verification` use the same persistent reviewer. A formal `rework` resumes the original
  analysis worker whenever its action provides that task ID. Dispatch a replacement only when
  the action is `dispatch_agent` and `replacement_allowed=true`.
- The main Agent does not read worker role files or restate task content. It must not create,
  fill, edit, normalize, or repair analysis, rework, or review semantic result files. Only the
  subagent holding that task may write them.
- Bind the `data_root` returned for the new Run together with its `run_id`. Pass the literal
  `--data-root <data_root>` to every run-scoped `record-agent-session`, `resume-run`, and
  `mark-reviewer-unavailable` command, including the default `pangea-data`. Do not rely on a CLI
  default or recover after first probing the wrong directory.
- Wait by checking `list_agents`. While the target is `running`, do not read its result or send
  another message; wait exactly twenty seconds with one host-shell call (`sleep 20` on POSIX or
  `Start-Sleep -Seconds 20` on PowerShell), then check `list_agents` again. Repeat that same wait
  if needed. Continue only after the target is `ready` or `inactive`.
- Each delegated Agent validates the artifact for its current task before ending its turn. Once
  that turn completes successfully, obey `after_completion=resume_run` immediately by running
  `resume-run --run-id <run_id> --data-root <data_root>`. The next returned action decides the
  next turn; do not inspect or parse a textual completion marker.
- If dispatch, continuation, validation, or artifact writing fails, stop and report the real
  action and stage. Do not replace missing results with main-Agent output.
- Rework remains limited to one graph-authorized round. Rework verification must use the original
  reviewer. If that reviewer cannot be resumed, use the selected interpreter to run
  `-m pangea_agent.cli.main mark-reviewer-unavailable` with the bound reviewer ID and real reason,
  then run `resume-run` with the same `run_id` and `data_root` to produce `UNRESOLVED`; do not
  dispatch a substitute reviewer.

Continue until the graph returns a terminal quality result and generates `report.md` and
`report.html`, or until a real failure requires an honest stop.
