# Batch 0: development and release baseline

Audit date: 2026-10-20. Work branch: `refactor`, initially clean at
`6dd9736b93a33df87edc13d94723c2ba80c0d52e`, verified against GitHub `dev`.

## Before changes

- `main` is `1bce35429a5f9e01282c235abecd6ab5936c0e28` and an ancestor of
  `dev` (0 main-only, 12 dev-only commits). Release integration PRs exist,
  alongside direct development commits. No history rewrite is needed.
- Both branch endpoints report `protected: false`. The only ruleset,
  [Protect main branch](https://github.com/theAfish/open-agent-world/rules/23912326),
  is disabled and has an empty required-check list. The authenticated protection
  endpoint returns 404, "Branch not protected".
- [Latest dev CI](https://github.com/theAfish/open-agent-world/actions/runs/39393500126)
  fails: backend 2593 passed / 2 failed / 60 prerequisite skips; frontend 179
  files passed / 3 failed; Windows source startup and core smoke fail. Linux
  and macOS source startup pass. No failure was hidden by continue-on-error.
- [Latest documentation](https://github.com/theAfish/open-agent-world/actions/runs/39393500063)
  fails English source-language validation; tutorial examples pass, Pages is
  correctly blocked.
- Native checks are path-filtered PR checks, not a release dependency.
  Desktop release runs on `v*` or manual dispatch without running CI first.
  It rewrites four desktop version declarations after checkout, omits the
  Cargo lock entry, and does not update the backend version used for Pack
  compatibility. All app declarations still say `0.1.0`.
- [v0.5.0 packaging](https://github.com/theAfish/open-agent-world/actions/runs/36957149700)
  passed and remains a draft; published v0.4.0 used the same source commit
  `be55502c`. Packaging success alone did not establish test success.
- Existing signing, updater manifest consistency, backup/restore tests and
  draft-only publishing provide useful foundations. They must remain intact.
- The SDK is `@oaw/plugin-api@1.0.0`, backend/frontend Plugin API major is 1,
  and Pack versions are independent (Greeter `0.1.0`). These are not app versions.
- External Greeter build and restart/browser acceptance already exist but
  are not called by CI. Tutorial entry-point tests do not cover that boundary.

## Confirmed failures and repair rationale

| Failure | Classification | Required repair |
| --- | --- | --- |
| ScopedFunctionTool has no `__name__` | Stale test double | Fake runner must call ADK's Tool `name` / `run_async` contract; preserve failure/recovery assertions |
| No adapter `.pyc` under `PYTHONDONTWRITEBYTECODE` | Test environment assumption | Explicitly create the bytecode fixture; retain cleanup/reverification assertions |
| Knowledge Base cannot resolve lucide-react | CI preparation order | Prepare declared Pack dependencies in their own projects before source-import tests |
| PluginSurface rejects openai.codex | Stale catalog fixture | Enroll the real owner and exercise its source component using the existing view registry |
| ExecutionGraph cannot find Study copper | Stale UI assertion | Observe an actual execution node after removal of the redundant graph heading |
| Windows short/long temporary path mismatch | Test path comparison | Canonicalize both paths, preserve exact directory and environment checks |
| Windows smoke prints PASS then exits 1 | Test harness state leak | Restore LASTEXITCODE after failure-simulation shims; keep thrown assertions fatal |
| Chinese prose in card-design.md | Documentation defect | Translate prose in the English page; keep the language gate strict |

## Verification

Results and remaining acceptance boundaries are recorded below as work completes.
