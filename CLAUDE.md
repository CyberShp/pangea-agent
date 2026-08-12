# Claude Code Instructions for pangea-agent

## Language

Use Chinese for project discussion, planning, review notes, and user-facing explanations. Keep code symbols, protocol fields, configuration keys, paths, and error messages in their original language.

## Project intent

`pangea-agent` is a testing-analysis agent framework. It turns source code, design materials, coverage information, and existing test cases into structured risks, test points, test cases, and reports.

## Architecture source of truth

- Workflow: `src/pangea_agent/graph/`
- Node implementations: `src/pangea_agent/graph/nodes/`
- Data contracts: `schemas/`
- Analysis rubrics: `src/pangea_agent/rubrics/builtin/`
- Local user data layout: `pangea-data/`

Do not redefine workflow, schemas, or rubrics in ad-hoc prompts. Update the corresponding source file instead.

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

## Client compatibility

This repository also includes `AGENTS.md` for OpenCode and other agent clients. Keep `CLAUDE.md` and `AGENTS.md` aligned when changing project-level rules.
