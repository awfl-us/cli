# CLI `wip-old` reconciliation (2026-11-19)

Source `wip-old` 4ba04b7; target `main` 856db29 (34 main-only commits). **Preserve today's shared CLI runtime, credentials, native tool routing, SSE consumer and cloud workspace lifecycle. No wholesale modularization.**

## Selective decisions

| Old change | Decision | Reason |
|---|---|---|
| Split `main.py` into `bootstrap/{args,env}`, `consumer/supervisor`, `session/workflow`, `ui/prompt`, `dev/cleanup` | **Drop unless a targeted maintenance problem arises** | Current `main.py` includes important later changes: project SSE classification, `idle-timeout` behavior, credential refresh, and native tool consumer. Cosmetic extraction risks changing startup/termination semantics. |
| `onboarding/git.py` first-run interactive GitHub setup | **Optional small UX feature, no auto-actions** | May help new users, but no automatic repo creation/push, git installs, or interactive prompts in headless/native agent runs. Requires explicit user opt-in. |
| Project creation with no Git remote + `.awfl/config.json` `projectId` | **Evaluate independently; highest-value CLI candidate** | Could support source-less / local projects. Compare current project discovery and API support before patching. Preserve precedence: explicit `AWFL_PROJECT_ID` > validated `.awfl/config.json` > remote mapping. |
| `start` / `watch` aliases and improved terminal prompts | **Defer** | Minor usability, no reason to risk CLI runtime changes for core reconciliation. |
| SSE supervisor error classifications | **Drop** | Current project/session consumer behavior is newer, with worker inactivity, leader-lock and shutdown fixes absent from wip. |
| `sessions/patches/*.patch`, `main_old.py` | **Discard** | Development artifacts, not production features. |

## Optional local-project slice

1. Confirm the modern server's project-create request accepts absent `remote` and persists a stable project ID in Postgres. Verify authorization, user scope, and duplicate-name behavior (do not modify server merely to satisfy the old CLI).
2. Add a tiny, opt-in `awfl project init` flow using current CLI command registration. Read/write `.awfl/config.json` atomically; validate IDs against authenticated project API. Never push to GitHub automatically or mutate the user's existing repo on startup.
3. Ensure project selection is consistent across terminal, SSE subscriptions, cloud consumer workspace, and native tool callbacks; preserve per-project isolation on refresh/restart.

## Tests / release gates

- Unit tests: malformed/missing config, nonexistent local repo, no remote, multiple remotes, wrong-project ID, read-only directory, invalid JSON, migration of existing key names, explicit env override, headless invocation, and absent `gh` binary.
- Integration: create/select project without remote (if supported); restart process and retain project; read/execute project-scoped commands with no cross-project leakage.
- Consumer regression: leader-lock skip, idle-timeout, SIGINT/SIGTERM, project/session SSE errors, token expiry/refresh, workspace snapshot/resume, native tool routing, and valid execution status updates.
- Explicitly run existing tests from current `main`; benchmark startup and normal request flow. Don't replace the current consumer/supervisor with historic code to get this feature.

**Exit:** at most a small opt-in local-project enhancement with no degradation of current CLI behavior; otherwise close this repo as no-op. Core typed-tool/workflow work should not wait on this.

## Reconciliation outcome — 2026-11-20

**Accepted and merged only the remote-free local-project functionality:** [CLI PR #26](https://github.com/awfl-us/cli/pull/26), main `0ec97dc34293cff1a336089783e72a543992cabc`. The existing authenticated server POST `/workflows/projects` accepts omitted `remote` and creates user-scoped project records. The CLI now offers **explicit** `awfl project init "Name"`; it does not create a Git repository, push, or prompt automatically. The generated `.awfl/config.json` contains `projectId` and name, written with restrictive permissions and exclusive creation; existing config is not overwritten. On a later resolver startup, this ID takes precedence over Git remote detection and is validated using the authenticated GET `/workflows/projects/:id` before selecting SSE/native callbacks. `AWFL_PROJECT_ID` still overrides it; interactive consumers must be restarted after `project init`.

**Evidence:** `PYTHONPATH=src python3 -m unittest discover -s tests -v` passed **74/74**, including six new local-project tests for no-remote creation, duplicate names, invalid/existing local config, validated selected-project resolution and explicit environment override. [GitHub Actions run 44801604488](https://github.com/awfl-us/cli/actions/runs/44801604488) passed the full suite. No live external project was created in this checkpoint; server API contract was inspected directly. No consumer/supervisor/SSE/credential architecture was replaced.

**Rejected/deferred as originally planned:** historical main.py modularization, first-run interactive GitHub setup, automatic pushes, legacy SSE supervisor, patch files and nonessential start/watch aliases. These cannot be safely restored over newer production code. CLI `wip-old` reconciliation is complete for the useful optional slice; browser-based/web model discovery is tracked independently.
