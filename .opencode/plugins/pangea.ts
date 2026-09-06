import { existsSync } from "node:fs"
import { randomUUID } from "node:crypto"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import type { Plugin } from "@opencode-ai/plugin"
import { tool } from "@opencode-ai/plugin"

type Binding = { dataRoot: string; runId: string; actionId: string }

const textPart = (text: string) => ({ type: "text" as const, text })

const ordinaryRecordKinds = new Set([
  "summary", "flow", "risk", "test_case", "test_case_group", "note",
  "unresolved", "branch", "evidence", "scenario", "review_finding",
  "blackbox_translation",
])

function ordinaryRecordKind(kind: string | undefined): string {
  return kind && ordinaryRecordKinds.has(kind) ? kind : "note"
}

const PangeaPlugin: Plugin = async ({ client, worktree }) => {
  const bindings = new Map<string, Binding>()
  const sessionModels = new Map<string, { providerID: string; modelID: string }>()
  const resultWrites = new Map<string, Promise<void>>()

  function pythonExecutable(): string {
    const configured = process.env.PANGEA_PYTHON
    if (configured) return configured
    for (const candidate of [
      join(worktree, ".venv", "bin", "python"),
      join(worktree, ".venv", "Scripts", "python.exe"),
    ]) {
      if (existsSync(candidate)) return candidate
    }
    return "python"
  }

  async function cli(args: string[]): Promise<any> {
    const proc = Bun.spawn(
      [pythonExecutable(), "-m", "pangea_agent.cli.main", ...args],
      { cwd: worktree, stdout: "pipe", stderr: "pipe" },
    )
    const [stdout, stderr, exitCode] = await Promise.all([
      new Response(proc.stdout).text(),
      new Response(proc.stderr).text(),
      proc.exited,
    ])
    let payload: any
    try {
      payload = JSON.parse(stdout.trim())
    } catch {
      throw new Error(`PANGEA CLI returned non-JSON output (exit=${exitCode}): ${stderr || stdout}`)
    }
    if (exitCode !== 0 || !payload?.ok) {
      throw new Error(payload?.error?.message || stderr || `PANGEA CLI failed with exit ${exitCode}`)
    }
    return payload.result
  }

  function current(context: { sessionID: string }): Binding {
    const binding = bindings.get(context.sessionID)
    if (!binding) throw new Error("当前 OpenCode session 未绑定 PANGEA action")
    return binding
  }

  function boundArgs(context: { sessionID: string }): string[] {
    const binding = current(context)
    return [
      "--data-root", binding.dataRoot,
      "--run-id", binding.runId,
      "--action-id", binding.actionId,
      "--task-id", context.sessionID,
    ]
  }

  function render(value: unknown): string {
    return JSON.stringify(value)
  }

  async function serializeResult<T>(
    context: { sessionID: string; abort: AbortSignal },
    write: (bindingArgs: string[]) => Promise<T>,
  ): Promise<T> {
    const bindingArgs = boundArgs(context)
    const key = JSON.stringify(bindingArgs.slice(0, 6))
    const previous = resultWrites.get(key) ?? Promise.resolve()
    const operation = previous.then(() => {
      context.abort.throwIfAborted()
      return write(bindingArgs)
    })
    const settled = operation.then(() => {}, () => {})
    resultWrites.set(key, settled)
    try {
      return await operation
    } finally {
      if (resultWrites.get(key) === settled) resultWrites.delete(key)
    }
  }

  function writeResult(
    context: { sessionID: string; abort: AbortSignal },
    command: string,
    extra: string[] | ((bindingArgs: string[]) => Promise<string[]>),
  ): Promise<any> {
    return serializeResult(context, async (bindingArgs) => {
      const writeArgs = typeof extra === "function" ? await extra(bindingArgs) : extra
      const result = await cli(["result-read", ...bindingArgs, "--view", "compact"])
      return cli([
        command, ...bindingArgs,
        command === "work-finish" ? "--revision" : "--expected-revision", String(result.revision),
        "--request-id", randomUUID(), ...writeArgs,
      ])
    })
  }

  async function comparisonVersion(bindingArgs: string[]): Promise<string> {
    const { task } = await cli(["task-open", ...bindingArgs])
    if (task.review_stage !== "comparison_review" || !task.version_set_id) {
      throw new Error("当前绑定 task 尚未开放 comparison version set")
    }
    return task.version_set_id
  }

  const workerTools = (stage: string) => ({
    pangea_task_open: true,
    pangea_input_read: true,
    pangea_source_index: true,
    pangea_source_read: true,
    pangea_source_search: true,
    pangea_result_read: true,
    pangea_result_write: true,
    pangea_result_supersede: true,
    pangea_comparison_finding: stage === "comparison_review",
    pangea_result_repair: true,
    pangea_plan_create: stage === "unit_planning",
    pangea_plan_update: stage === "unit_planning",
    pangea_comparison_read: stage === "comparison_review",
    pangea_work_finish: true,
    pangea_review_decide: stage === "comparison_review",
    bash: false,
    read: false,
    write: false,
    edit: false,
    glob: false,
    grep: false,
    task: false,
    webfetch: false,
    websearch: false,
  })

  const roleAgent = (role: string) => {
    if (role === "planning") return "planning-worker"
    if (role === "review") return "review-worker"
    return "analysis-worker"
  }

  return {
    "chat.message": async (input) => {
      if (input.model) sessionModels.set(input.sessionID, input.model)
    },
    tool: {
      pangea_run_create: tool({
        description: "Create one source-first-v1 Run from an explicit repository, the user's semantic target, and frozen source scope. The target must preserve only facts and identifiers the user explicitly supplied: never invent or add API/function names, status codes, macros, states, callbacks, cleanup actions, or expected source behavior before Planning/Analysis reads the frozen source. If the user says only 'public API', keep that wording generic. Every source_scope item must be a repository-relative source file or directory path such as lib/nvme/nvme_auth.c; never prefix repositories/<repo>/, append @commit, or encode repository/commit metadata in a scope path. Preserve a user-specified run_id when provided. Omit data_root to use the worktree pangea-data root; data_root contains both repositories/ and runs/ and must never point at runs/ itself.",
        args: {
          run_id: tool.schema.string().optional(),
          repository: tool.schema.string(),
          target: tool.schema.string(),
          source_scope: tool.schema.array(tool.schema.string()).min(1),
          data_root: tool.schema.string().optional(),
          focus: tool.schema.array(tool.schema.string()).optional(),
          asset_ids: tool.schema.array(tool.schema.string()).optional(),
          test_case_examples: tool.schema.array(tool.schema.string()).optional(),
          model_id: tool.schema.string().optional(),
          effective_context_budget: tool.schema.number().int().positive().optional(),
        },
        async execute(args) {
          const directory = await mkdtemp(join(tmpdir(), "pangea-opencode-"))
          const contractPath = join(directory, "contract.json")
          const contract = {
            workflow_version: "source-first-v1",
            analysis_profile: "behavior-test-v1",
            ...(args.run_id ? { run_id: args.run_id } : {}),
            data_root: args.data_root ?? join(worktree, "pangea-data"),
            repository: args.repository,
            target: args.target,
            source_scope: args.source_scope,
            focus: (args.focus ?? []).map((item) => item.trim()).filter(Boolean),
            asset_ids: (args.asset_ids ?? []).map((item) => item.trim()).filter(Boolean),
            test_case_examples: (args.test_case_examples ?? []).map((item) => item.trim()).filter(Boolean),
            ...(args.model_id ? { model_id: args.model_id } : {}),
            effective_context_budget: args.effective_context_budget ?? 250000,
          }
          try {
            await writeFile(contractPath, `${JSON.stringify(contract, null, 2)}\n`, "utf8")
            return render(await cli(["runs", "create", "--contract", contractPath]))
          } finally {
            await rm(directory, { recursive: true, force: true })
          }
        },
      }),

      pangea_run_resume: tool({
        description: "Resume one explicitly selected source-first Run without scanning history for another candidate.",
        args: {
          data_root: tool.schema.string(),
          run_id: tool.schema.string(),
        },
        async execute(args) {
          return render(await cli([
            "resume-run", "--data-root", args.data_root, "--run-id", args.run_id,
          ]))
        },
      }),

      pangea_action_dispatch: tool({
        description: "Dispatch or continue exactly one Graph action, bind it to a real OpenCode session, wait for the worker, then settle it.",
        args: {
          data_root: tool.schema.string(),
          run_id: tool.schema.string(),
          action_id: tool.schema.string(),
        },
        async execute(args, context) {
          const next = await cli([
            "adapter", "next", "--data-root", args.data_root,
            "--run-id", args.run_id, "--limit", "8",
          ])
          const actions = Array.isArray(next?.actions) ? next.actions : []
          const action = actions.find((item: any) => item?.action_id === args.action_id)
          if (!action) throw new Error(`Graph 当前没有待派发 action：${args.action_id}`)
          if (action.attention_required) {
            throw new Error(`宿主已暂停 action ${args.action_id}：${action.error ?? "同一结果未继续产出"}。按宿主续接要求处理后，再调用 pangea_action_retry。`)
          }

          let sessionID: string
          if (action.action === "continue_agent") {
            if (!action.task_id || action.task_id === "pending") {
              throw new Error(`continue_agent 缺少已绑定 task_id：${args.action_id}`)
            }
            sessionID = action.task_id
            await cli([
              "adapter", "bind", "--data-root", args.data_root,
              "--run-id", args.run_id, "--action-id", args.action_id,
              "--task-id", sessionID,
            ])
            bindings.set(sessionID, {
              dataRoot: args.data_root,
              runId: args.run_id,
              actionId: args.action_id,
            })
          } else {
            const created = await client.session.create({
              query: { directory: context.directory },
              body: { parentID: context.sessionID, title: `PANGEA ${args.action_id}` },
              throwOnError: true,
            })
            const session = (created as any).data ?? created
            sessionID = session?.id
            if (!sessionID) throw new Error("OpenCode 未返回新 worker session ID")
            await cli([
              "adapter", "bind", "--data-root", args.data_root,
              "--run-id", args.run_id, "--action-id", args.action_id,
              "--task-id", sessionID,
            ])
            bindings.set(sessionID, {
              dataRoot: args.data_root,
              runId: args.run_id,
              actionId: args.action_id,
            })
          }

          const opened = await cli([
            "task-open", "--data-root", args.data_root, "--run-id", args.run_id,
            "--action-id", args.action_id, "--task-id", sessionID,
          ])
          const behaviorTestProfile = opened?.task?.analysis_profile === "behavior-test-v1"
          const validationError = action.validation_error ?? action.pending_repair?.error
          const validationText = validationError
            ? JSON.stringify(validationError)
            : ""
          const repairInstruction = validationText
            ? /(尚未提交 work_finish|完成声明|completion|declared_revision)/.test(validationText)
              ? `\n这是同一 action 的局部续接：${validationText}\n正文已保存；回读当前 revision，核对正文无须修改时直接重新提交 pangea_work_finish。`
              : `\n这是同一 action 的局部修正：${validationText}\n先 pangea_result_read，再根据诊断调用 pangea_result_write/pangea_result_supersede/pangea_review_decide 产生新 revision；若旧记录错误，只能用 pangea_result_supersede 精确作废旧 record_id。在结果内容未变更前禁止重复 pangea_work_finish。`
            : ""
          const planningInstruction = action.stage === "unit_planning" && behaviorTestProfile
            ? "\nPlanning 只做紧凑归属：purpose 概括主责行为、用户点名生命周期和必要 context 类别，不展开状态机步骤、helper 清单、分支表或预期错误码；context 不产生额外用例义务。确认 owned regions、公开/自动入口、transport/adapter、feature-off 与测试路径后立即写 plan。"
            : ""
          const analysisInstruction = action.stage === "unit_analysis" && behaviorTestProfile
            ? "\n250K 任务给同一 worker 可能的 closure 预留约 70000 token，首轮输入历史目标约 145000。优先主干、错误传播、再次操作和清理，同结果枚举用紧凑参数表。再次操作必须连续追踪第一次 adapter/transport 终态、第二次公开调用命中的底层守卫、新异步状态/资源是否真实创建，以及下一次宿主 poll/callback 的指针和外部后果；公开入口返回 0 不等于新事务已经启动。两次操作之间不得无依据把内部状态改回 ready/running。"
            : ""
          const comparisonInstruction = action.stage === "comparison_review"
            ? behaviorTestProfile
              ? "\nComparison 对照重要业务行为、测试入口、步骤与预期、外部观测、清理恢复和真实 Coverage 对应。必要用例遗漏、源码可回答却仍悬置、互斥终态或不可执行步骤都可以成为 finding，不要求先证明产品缺陷。优先检查首轮 unresolved、‘可能/待确认’、跨次状态与次数。跨次链必须核对第一次 adapter/transport 终态、第二次公开调用的底层守卫、新异步资源是否真实建立及下一次宿主 poll/callback 的指针；公开入口返回 0 不等于新事务已经启动。两次操作之间无依据把内部状态改回 ready/running 属于不可执行前置条件。不得仅因 Planning 罗列 helper/context 就要求逐函数独立用例；context 不扩大 owned source，同结果分支可参数化合并。finding 通过 pangea_comparison_finding 绑定 Graph 的精确 unit_id。pangea_review_decide.correction_record_ids 必须填写 pangea_comparison_finding 返回的 finding record_id，绝不能填写被指出错误的 Analysis/test_case record_id。不要用 pangea_input_read 猜 version_set_id，comparison 工具由宿主绑定版本。"
              : "\nComparison 还必须逐条确认：只有当前可达且能证明具体错误外部结果的差异才是 finding；仅缺 callee/包装/清理函数体只是 unresolved，不得触发 closure。对齐 test_case 与 flow 的状态和协议消息顺序。finding 通过 pangea_comparison_finding 绑定 Graph 的精确 unit_id。"
            : ""
          const closureInstruction = action.stage === "targeted_closure"
            ? "\nClosure 先调用 pangea_input_read(input_id=\"correction_records\")，按 next_cursor 完整读完 Comparison 选中的冻结修正记录，不得依赖可能过长的 task-open 回包或自行猜 finding。若更正 inherited record，必须调用 pangea_result_supersede；target_record_ids 填被更正的精确 rec-...，kind/body 写唯一有效的新结论。不得只在普通 pangea_result_write 的正文中声称已作废旧记录。一个 finding 默认只做一次直接 replacement；仅当旧引用会变成事实错误时才级联，不反复 supersede 同组记录、不重写无关正文。"
            : ""

          const model = sessionModels.get(context.sessionID)
          let workerTurns = 0
          let recoveryUsed = false
          let lastWorkerResponse: any = null
          const recordCount = (result: any) =>
            result.total_record_count ?? (result.active_record_count + (result.superseded_record_ids?.length ?? 0))
          const errorMessage = (error: any): string =>
            error?.data?.message ?? error?.message ?? error?.name ?? "OpenCode worker 回合未完成"
          let startingRecordCount: number | null = null
          try {
            startingRecordCount = recordCount(await cli([
              "result-read", "--data-root", args.data_root, "--run-id", args.run_id,
              "--action-id", args.action_id, "--task-id", sessionID, "--view", "compact",
            ]))
          } catch {
            // The worker receives the bound read error during the first prompt check.
          }

          async function promptWorker(prompt: string) {
            workerTurns += 1
            let workerError: string | null = null
            try {
              const response = await client.session.prompt({
                path: { id: sessionID },
                query: { directory: context.directory },
                body: {
                  agent: roleAgent(action.role),
                  ...(model ? { model } : {}),
                  tools: workerTools(action.stage),
                  parts: [textPart(prompt)],
                },
                throwOnError: true,
              })
              const info = response.data?.info
              if (!info) workerError = "OpenCode 未返回本轮 assistant message"
              else if (info.error) workerError = errorMessage(info.error)
              else if (info.finish === "length") workerError = "OpenCode worker 输出达到长度上限，本轮输出被截断"
              else if (info.finish === "content-filter") workerError = "OpenCode worker 输出被提供方过滤，本轮输出未完成"
              lastWorkerResponse = info ? {
                message_id: info.id, finish: info.finish, tokens: info.tokens,
                error: workerError,
              } : { error: workerError }
            } catch (error) {
              workerError = errorMessage(error)
              lastWorkerResponse = { error: workerError }
            }
            let result: any = null
            let resultError: string | null = null
            try {
              result = await cli([
                "result-read", "--data-root", args.data_root, "--run-id", args.run_id,
                "--action-id", args.action_id, "--task-id", sessionID,
                "--view", "compact",
              ])
              if (result.active_record_count === 0) {
                const instruction = action.stage === "unit_planning"
                  ? "请用 pangea_plan_create 保存基于冻结源码确定的单元规划，再提交 pangea_work_finish。"
                  : action.stage === "independent_review"
                    ? "请用 pangea_result_write 保存实际审查结论；若独立盲审已完成且没有新 finding，写 summary 说明已核对的范围、证据与结论。comparison 尚未开放不妨碍保存独立盲审结果。"
                    : action.stage === "comparison_review"
                      ? "请保存当前锁定版本的实际对照结论，并用 pangea_review_decide 声明质量与需修正的记录；没有新 finding 时可写 summary 说明核对结论。"
                      : "请用 pangea_result_write 保存已经完成的源码理解、flow、风险或用例；未确认的证据缺口如实记录，然后继续同一任务。"
                resultError = `当前 active records=0。${instruction}`
              } else if (!result.completion_declared_revision) {
                resultError = "正文已保存，但尚未提交 pangea_work_finish；确认当前工作完成后提交完成声明。"
              } else if (!result.completion_complete) {
                resultError = "当前工作被声明为未完成；沿用已保存的记录和本会话证据，完成剩余工作后再提交 pangea_work_finish。"
              } else if (result.completion_declared_revision !== result.revision) {
                resultError = "已保存记录在完成声明后发生变化；回读当前结果，核对后重新提交 pangea_work_finish。"
              }
            } catch (error) {
              resultError = `当前绑定结果无法读取：${errorMessage(error)}。请修复同一 result_path。`
            }
            return {
              result,
              issue: [workerError ? `OpenCode worker 返回错误：${workerError}` : null, resultError].filter(Boolean).join("\n"),
              reasonCode: workerError ? "worker_error" : "result_incomplete",
            }
          }

          async function runPhase(prompt: string) {
            let outcome = await promptWorker(prompt)
            if (outcome.issue && !recoveryUsed && !context.abort.aborted) {
              recoveryUsed = true
              outcome = await promptWorker(`继续同一 action ${args.action_id} 的未完成部分。${outcome.issue}\n先 pangea_result_read 核对已保存内容，保留原记录和本会话证据，仅修正上述问题。`)
            }
            return outcome
          }

          const outcome = await runPhase(`执行 PANGEA Graph action ${args.action_id}。身份已由宿主绑定；先调用 pangea_task_open 获取唯一 task，不要自行填写或猜测 task_id。${repairInstruction}${planningInstruction}${analysisInstruction}${comparisonInstruction}${closureInstruction}\n完成语义工作并调用 pangea_work_finish 后，只回显 exact action_id。`)
          if (outcome.issue) {
            const noProgress = outcome.result === null || startingRecordCount === null || recordCount(outcome.result) <= startingRecordCount
            const continuation = await cli([
              "adapter", "defer", "--data-root", args.data_root, "--run-id", args.run_id,
              "--action-id", args.action_id, "--task-id", sessionID,
              "--reason-code", outcome.reasonCode, "--reason", outcome.issue,
              ...(noProgress ? ["--no-progress"] : []),
            ])
            return render({
              session_id: sessionID, action_id: args.action_id, worker_turns: workerTurns,
              completion_observed: false, worker_response: lastWorkerResponse,
              continuation,
            })
          }
          const settled = await cli([
            "adapter", "settle", "--data-root", args.data_root,
            "--run-id", args.run_id, "--action-id", args.action_id,
          ])
          return render({
            session_id: sessionID,
            action_id: args.action_id,
            worker_turns: workerTurns,
            completion_observed: true,
            worker_response: lastWorkerResponse,
            settle: settled,
          })
        },
      }),

      pangea_action_retry: tool({
        description: "After explicit user authorization, requeue one exact attention-required or interrupted continue_agent action. Preserves run_id, action_id, task_id, result_path, and failure history; does not dispatch by itself.",
        args: {
          data_root: tool.schema.string(),
          run_id: tool.schema.string(),
          action_id: tool.schema.string(),
        },
        async execute(args) {
          return render(await cli([
            "adapter", "retry", "--data-root", args.data_root,
            "--run-id", args.run_id, "--action-id", args.action_id,
          ]))
        },
      }),

      pangea_task_open: tool({
        description: "Open the one Graph task bound to this worker session.",
        args: {},
        async execute(_args, context) {
          return render(await cli(["task-open", ...boundArgs(context)]))
        },
      }),

      pangea_input_read: tool({
        description: "Read one bounded page of a frozen non-source input declared by the current task. Omit cursor on the first page; continue only with the returned opaque next_cursor and the same input_id.",
        args: {
          input_id: tool.schema.string(),
          cursor: tool.schema.string().optional(),
          max_chars: tool.schema.number().int().min(1).max(24000).optional(),
        },
        async execute(args, context) {
          const extra = ["--input-id", args.input_id]
          if (args.cursor) extra.push("--cursor", args.cursor)
          if (args.max_chars) extra.push("--max-chars", String(args.max_chars))
          return render(await cli(["input-read", ...boundArgs(context), ...extra]))
        },
      }),

      pangea_source_index: tool({
        description: "List compact frozen source files, or page regions for one exact repo/path. On the first call omit path to discover allowed files, then pass only an exact path returned by this task; never guess a header path or directory. Continue only with the returned opaque next_page_token and unchanged repo/path.",
        args: {
          repo_id: tool.schema.string().optional(),
          path: tool.schema.string().optional(),
          page_token: tool.schema.string().optional(),
        },
        async execute(args, context) {
          const extra: string[] = []
          if (args.repo_id) extra.push("--repo-id", args.repo_id)
          if (args.path) extra.push("--path", args.path)
          extra.push("--view", "compact")
          if (args.page_token) extra.push("--page-token", args.page_token)
          return render(await cli(["source-index", ...boundArgs(context), ...extra]))
        },
      }),

      pangea_source_read: tool({
        description: "Read frozen source by exact region or line range and return a source evidence handle. For a next page, copy next_page_token and keep repo/path/region stable; the token preserves the original line range. path is a source-relative path, not a task or result file.",
        args: {
          repo_id: tool.schema.string(),
          path: tool.schema.string().optional(),
          region_id: tool.schema.string().optional(),
          line_start: tool.schema.number().int().positive().optional(),
          line_end: tool.schema.number().int().positive().optional(),
          page_token: tool.schema.string().optional(),
        },
        async execute(args, context) {
          const extra = ["--repo-id", args.repo_id]
          for (const [flag, value] of [["--path", args.path], ["--region-id", args.region_id]] as const) {
            if (value) extra.push(flag, value)
          }
          if (args.line_start) extra.push("--line-start", String(args.line_start))
          if (args.line_end) extra.push("--line-end", String(args.line_end))
          extra.push("--view", "compact")
          if (args.page_token) extra.push("--page-token", args.page_token)
          return render(await cli(["source-read", ...boundArgs(context), ...extra]))
        },
      }),

      pangea_source_search: tool({
        description: "Literal search inside the frozen source scope; results include locations and short previews. Continue with next_page_token and unchanged query/repo/path; use source_read for full lines.",
        args: {
          query: tool.schema.string(),
          repo_id: tool.schema.string().optional(),
          path: tool.schema.string().optional(),
          page_token: tool.schema.string().optional(),
        },
        async execute(args, context) {
          const extra = ["--query", args.query]
          if (args.repo_id) extra.push("--repo-id", args.repo_id)
          if (args.path) extra.push("--path", args.path)
          extra.push("--view", "compact")
          if (args.page_token) extra.push("--page-token", args.page_token)
          return render(await cli(["source-search", ...boundArgs(context), ...extra]))
        },
      }),

      pangea_result_read: tool({
        description: "Read a character-bounded page of current active records. Omit page_token on the first page and never pass placeholders such as '0'. Continue only with the returned next_page_token and repeat every original filter on every page, including record_id/include_history; only the page_token changes. Set include_history only when audit history and warnings are needed.",
        args: {
          record_id: tool.schema.string().optional(),
          page_token: tool.schema.string().optional(),
          include_history: tool.schema.boolean().optional(),
        },
        async execute(args, context) {
          const extra: string[] = []
          if (args.record_id) extra.push("--record-id", args.record_id)
          extra.push("--view", "compact")
          if (args.page_token) extra.push("--page-token", args.page_token)
          if (args.include_history) extra.push("--include-history")
          return render(await cli(["result-read", ...boundArgs(context), ...extra]))
        },
      }),

      pangea_result_write: tool({
        description: "Append one plain-text semantic record to the bound result. Supported kind values include summary, flow, risk, test_case, test_case_group, note, unresolved, branch, evidence, scenario, review_finding, and blackbox_translation; omitted or unsupported values are stored as note without changing body. The host serializes writes and supplies the current revision. Use pangea_result_supersede to replace an earlier record; comparison findings use pangea_comparison_finding.",
        args: {
          kind: tool.schema.string().optional(),
          body: tool.schema.string().min(1),
        },
        async execute(args, context) {
          const record = { kind: ordinaryRecordKind(args.kind), body: args.body }
          return render(await writeResult(context, "result-write", ["--records", JSON.stringify([record])]))
        },
      }),

      pangea_result_supersede: tool({
        description: "Append one replacement record and retire exact earlier record IDs. The Agent chooses the targets and replacement meaning; the tool only places target_record_ids into the record's top-level supersedes field so prose cannot masquerade as a retirement.",
        args: {
          target_record_ids: tool.schema.array(tool.schema.string().regex(/^rec-\d{6}$/)).min(1).max(8),
          kind: tool.schema.string().optional(),
          body: tool.schema.string().min(1),
        },
        async execute(args, context) {
          const extra = [
            "--target-record-ids", JSON.stringify(args.target_record_ids),
            "--replacement", JSON.stringify({ kind: ordinaryRecordKind(args.kind), body: args.body }),
          ]
          return render(await writeResult(context, "result-supersede", extra))
        },
      }),

      pangea_comparison_finding: tool({
        description: "Write or replace one comparison finding. unit_ids is required and becomes the record's top-level relates_to route; do not repeat routing fields inside finding.",
        args: {
          unit_ids: tool.schema.array(tool.schema.string().regex(/^unit-\d{4}$/)).min(1).max(8),
          replace_finding_record_ids: tool.schema.array(tool.schema.string().regex(/^rec-\d{6}$/)).min(1).max(8).optional(),
          finding: tool.schema.object({
            summary: tool.schema.string().min(1),
            body: tool.schema.string().min(1),
            evidence: tool.schema.array(tool.schema.any()).optional(),
            correction_target: tool.schema.string().optional(),
          }),
        },
        async execute(args, context) {
          const extra = [
            "--unit-ids", JSON.stringify(args.unit_ids),
            "--finding", JSON.stringify(args.finding),
          ]
          if (args.replace_finding_record_ids) {
            extra.push("--replace-finding-record-ids", JSON.stringify(args.replace_finding_record_ids))
          }
          return render(await writeResult(context, "comparison-finding-write", extra))
        },
      }),

      pangea_result_repair: tool({
        description: "Repair an unreadable result shell with records resent by the same bound worker; refuses a changed or readable target.",
        args: {
          expected_sha256: tool.schema.string().length(64),
          records: tool.schema.array(tool.schema.object({
            kind: tool.schema.string().optional(),
            body: tool.schema.any(),
            evidence: tool.schema.array(tool.schema.any()).optional(),
            relates_to: tool.schema.array(tool.schema.any()).optional(),
            supersedes: tool.schema.array(tool.schema.string()).optional(),
          })).max(4),
        },
        async execute(args, context) {
          return render(await serializeResult(context, (bindingArgs) => cli([
            "result-repair", ...bindingArgs,
            "--expected-sha256", args.expected_sha256,
            "--records", JSON.stringify(args.records),
          ])))
        },
      }),

      pangea_plan_create: tool({
        description: "Create one planning unit. The machine assigns unit_id; this tool intentionally has no unit_id argument.",
        args: {
          unit: tool.schema.object({
            title: tool.schema.string().min(1),
            purpose: tool.schema.string().min(1),
            owned_regions: tool.schema.array(tool.schema.string()).min(1),
            context_regions: tool.schema.array(tool.schema.string()).optional(),
            context_files: tool.schema.array(tool.schema.string()).optional(),
            coverage_ids: tool.schema.array(tool.schema.string()).optional(),
            asset_item_ids: tool.schema.array(tool.schema.string()).optional(),
            mechanism_ids: tool.schema.array(tool.schema.string()).optional(),
          }),
        },
        async execute(args, context) {
          return render(await writeResult(context, "plan-write", ["--unit", JSON.stringify(args.unit)]))
        },
      }),

      pangea_plan_update: tool({
        description: "Update one existing planning unit using the exact machine-issued unit_id returned by pangea_plan_create.",
        args: {
          unit: tool.schema.object({
            unit_id: tool.schema.string().min(1),
            title: tool.schema.string().min(1),
            purpose: tool.schema.string().min(1),
            owned_regions: tool.schema.array(tool.schema.string()).min(1),
            context_regions: tool.schema.array(tool.schema.string()).optional(),
            context_files: tool.schema.array(tool.schema.string()).optional(),
            coverage_ids: tool.schema.array(tool.schema.string()).optional(),
            asset_item_ids: tool.schema.array(tool.schema.string()).optional(),
            mechanism_ids: tool.schema.array(tool.schema.string()).optional(),
          }),
        },
        async execute(args, context) {
          return render(await writeResult(context, "plan-write", ["--unit", JSON.stringify(args.unit)]))
        },
      }),

      pangea_comparison_read: tool({
        description: "Read one global character-bounded page of the Graph-pinned result versions exposed to the comparison Reviewer. For every continuation, repeat the original unit_id/include_history filters exactly and change only page_token; tokens are bound to those filters.",
        args: {
          unit_id: tool.schema.string().optional(),
          page_token: tool.schema.string().optional(),
          include_history: tool.schema.boolean().optional(),
        },
        async execute(args, context) {
          const bindingArgs = boundArgs(context)
          const extra = ["--version-set-id", await comparisonVersion(bindingArgs)]
          if (args.unit_id) extra.push("--unit-id", args.unit_id)
          extra.push("--view", "compact")
          if (args.page_token) extra.push("--page-token", args.page_token)
          if (args.include_history) extra.push("--include-history")
          return render(await cli(["comparison-read", ...bindingArgs, ...extra]))
        },
      }),

      pangea_work_finish: tool({
        description: "Declare whether the bound worker has completed its saved semantic records. The host supplies the current revision after earlier writes finish. A completed blind review with no new finding still needs a summary of what was reviewed and concluded.",
        args: {
          complete: tool.schema.boolean().optional(),
          note: tool.schema.string().optional(),
        },
        async execute(args, context) {
          const extra = [args.complete === false ? "--no-complete" : "--complete"]
          if (args.note) extra.push("--note", args.note)
          return render(await writeResult(context, "work-finish", extra))
        },
      }),

      pangea_review_decide: tool({
        description: "Save the comparison Reviewer's explicit quality decision and exact active Comparison finding record IDs that require the original worker to correct its result. correction_record_ids must contain record_ids returned by pangea_comparison_finding, never Analysis/test_case record IDs mentioned inside a finding. Example: if the finding tool returns rec-000001 about Analysis rec-000007, select rec-000001. Use correction_record_ids=[] when no correction is needed. Graph derives unit routes from the selected findings. To amend a decision, supply its active decision record ID in replace_decision_record_ids for atomic replacement.",
        args: {
          replace_decision_record_ids: tool.schema.array(tool.schema.string().regex(/^rec-\d{6}$/)).min(1).max(4).optional(),
          decision: tool.schema.object({
            disposition: tool.schema.enum(["pass", "unresolved"]),
            correction_record_ids: tool.schema.array(tool.schema.string().regex(/^rec-\d{6}$/)),
            summary: tool.schema.string(),
            body: tool.schema.any().optional(),
          }),
        },
        async execute(args, context) {
          return render(await writeResult(context, "review-decide", async (bindingArgs) => {
            const decision = { ...args.decision, version_set_id: await comparisonVersion(bindingArgs) }
            const extra = ["--decision", JSON.stringify(decision)]
            if (args.replace_decision_record_ids) {
              extra.push("--replace-decision-record-ids", JSON.stringify(args.replace_decision_record_ids))
            }
            return extra
          }))
        },
      }),
    },
  }
}

export default PangeaPlugin
