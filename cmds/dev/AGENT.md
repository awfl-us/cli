Dev Module Agent Guide

Purpose
- This agent owns the cli/cmds/dev module. It ensures a smooth local developer experience (parity with legacy dev.sh) while keeping the code modular, documented, and testable.

Core responsibilities
- Maintain and evolve dev subcommands: start, stop, status, logs, generate-yamls, deploy-workflow.
- Keep behavior aligned with developer expectations: .env bootstrap, ngrok BASE_URL export, docker compose lifecycle, sbt YAML generation, optional auto-deploy, clean teardown.
- Preserve a thin compatibility shim in core.py while encouraging direct imports from concrete modules.
- Document behaviors and update this README/AGENT when flows change.

Non-goals
- Changing global CLI session identity or response handling (handled at higher layers of the CLI).
- Pub/Sub consumer logic outside of dev workflows.

Entry points and file map
- commands.py: dispatches user-facing dev commands; route-only, minimal logic.
- core.py: re-exports selected APIs from helpers to retain backward compatibility. Do not add new logic here.
- subcommands/
  - start.py: startup flow; ensures .env, interactive config, ngrok, compose, watcher; registers shutdown hooks.
  - stop.py: teardown flow; cancels watcher, compose down, stops ngrok, clears state.
  - status.py: summarizes repo/workflows paths, ngrok URL, compose status, watcher state, last-changed YAMLs, and deploy target.
  - logs.py: docker compose logs wrapper.
  - generate.py: sbt build and YAML change detection.
  - deploy.py: WORKFLOW_ENV substitution, workflow name derivation, and gcloud deploy with detailed history.
- helpers (module-level files)
  - paths.py: repo/workflows discovery and derived directories.
  - dev_config.py: persisted config at ~/.awfl/dev_config.json.
  - dev_state.py: in-process ephemeral state accessors.
  - ngrok_utils.py: start/stop ngrok and tunnel inspection.
  - docker_utils.py: compose up/down/status/logs wrappers.
  - yaml_ops.py: sbt-driven YAML workflows and deploy helpers.
  - watcher.py: async regeneration/deploy loop.
  - prompt_utils.py: small input helpers and .env management.

Operational routine (how to work this module)
- Before changes
  - Read README.md for current design/constraints.
  - Identify whether your change belongs in a helper vs a subcommand. Prefer helpers for reusable logic.
  - Confirm external dependencies installed: ngrok, docker (with compose), gcloud, sbt, Java.
- Development loop
  - Run: python cli/main.py then dev start
  - Verify: BASE_URL exported, compose up, watcher running, status shows expected values.
  - Exercise subcommands: dev status, dev logs, dev generate-yamls, dev deploy-workflow <yaml>, dev stop.
  - Add/adjust logs via utils.log_unique to avoid spam.
- Testing checklist
  - YAML flow: derive_workflow_name, WORKFLOW_ENV substitution via temp file, deploy flags.
  - Ngrok: start, URL detection, failure path abort, stop behavior.
  - Compose: up with build, down, logs, status parsing on systems with/without compose plugin.
  - Watcher: starts, stops, handles error loops; auto_deploy on/off.
  - Config: persisted settings honored; reconfigure prompts update and save; no-prompt/yes-all flags respected.

Conventions
- Prefer direct imports from helpers; keep core.py as a stable re-export only.
- Keep user-facing output concise; use log_unique for deduplication.
- Avoid hidden implicit state. Persist only user choices in ~/.awfl/dev_config.json; keep runtime state in dev_state.
- Subprocesses
  - Use sys.executable for Python subprocesses (outside this module) to avoid mixed venvs.
  - Treat gcloud, docker, ngrok, sbt as external tools; validate presence where practical and emit clear error messages.
- Error handling
  - Fail fast on critical setup errors (e.g., ngrok URL missing when requested). Perform best-effort cleanup before exiting.

External dependencies (required or commonly used)
- ngrok (http tunnel) — used to expose local API and set BASE_URL.
- Docker + compose plugin — spins up local services.
- gcloud CLI — deploy workflows and wait/cancel operations.
- sbt + Java — generate YAML from Scala workflows.

Runbooks
- Add a new dev subcommand
  1) Create subcommands/<name>.py with a <name>_cmd(args: List[str]) -> bool.
  2) Wire it in commands.py dispatch.
  3) Add minimal logs and help text; keep heavy logic in helpers.
- Change compose behavior
  1) Update docker_utils.py (e.g., flags such as --build).
  2) Ensure status/logs/stop paths handle the new behavior.
- Update deploy behavior
  1) Edit yaml_ops.py for name derivation or substitution logic.
  2) Validate with dev deploy-workflow against a known YAML.
- Debug ngrok issues
  1) Confirm ngrok in PATH; run `ngrok http <port>` manually.
  2) Check _get_ngrok_existing_url output; verify AWFL_NGROK_PORT alignment.

Checklists
- Pre-commit
  - No stray print() calls; use log_unique.
  - Update README.md and this AGENT.md if behavior or flags change.
  - Validate on Python 3.11–3.13 where feasible.
- Pre-merge
  - dev start/status/stop happy path tested locally.
  - generate/deploy commands exercise at least one workflow YAML.

Known hazards and mitigations
- Mixed virtualenvs: ensure CLI uses cli/.venv or packaged entry; prefer sys.executable where needed.
- macOS LibreSSL warnings: prefer Python linked with OpenSSL (e.g., Homebrew python) to avoid TLS issues.
- Pub/Sub credentials not applicable here; deploy via gcloud requires auth.

Backlog (suggested next tasks)
- Surface BASE_URL in dev status explicitly (copy-friendly).
- Unit tests for derive_workflow_name and WORKFLOW_ENV substitution.
- Migrate import sites away from core.py re-export; add docstrings and typing.
- Improve error surfacing for missing external tools with actionable suggestions.

Acceptance criteria for future changes
- Parity with dev.sh preserved or improved.
- Clear startup and teardown logs; no orphaned processes on stop or Ctrl-C.
- README and AGENT updated to reflect new behaviors/flags.
