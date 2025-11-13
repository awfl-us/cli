# awfl CLI: dev command module

Purpose
- Provide a first-class developer experience for running the AWFL stack locally, mirroring the original dev.sh script while adding interactive configuration and safety.
- Orchestrate local services used during workflow development:
  - Environment bootstrapping (.env)
  - Ngrok tunnel and BASE_URL export
  - Docker Compose up/down
  - Scala sbt-based workflow YAML generation and auto-deploy on change

Key behaviors at a glance
- dev start ensures .env exists (creates from .env.example if missing) and continues into interactive configuration on first run.
- After completing configuration, the CLI checks authentication for the selected GCP project and may prompt you to log in via Google Device Flow. Tokens are cached per project, so you won't be prompted again once set.
- Starts/attaches to an ngrok tunnel, waits for the https URL, and exports BASE_URL in-process.
- Optionally brings Docker Compose up (with build) and tails logs via dev logs.
- Starts a watcher that regenerates YAMLs (sbt) and optionally auto-deploys changes.
- dev stop reliably tears everything down (watcher, compose, ngrok).
- dev status summarizes effective settings, paths, and runtime state.

Design and approach
- Single responsibility modules. The previous monolithic core.py was broken up into focused helpers:
  - paths.py: repository/workflows discovery and path helpers
  - dev_config.py: persisted config under ~/.awfl/dev_config.json
  - dev_state.py: in-process ephemeral state for the current CLI session
  - ngrok_utils.py: start/stop ngrok and tunnel discovery
  - docker_utils.py: compose up/down/status/logs
  - yaml_ops.py: sbt-driven YAML generation, change detection, deploy logic
  - watcher.py: async task that watches sources, triggers generation, and optionally deploys
  - prompt_utils.py: minimal input helpers and .env checks
- Thin compatibility shim. core.py re-exports the public API from the split modules. Existing callers can import from .core without breakage. Newer code should prefer direct imports from the concrete modules.
- Consistent logging. All user-facing output flows through utils.log_unique to de-duplicate noisy lines while retaining important updates.
- Safety and teardown. Shutdown hooks (SIGINT/SIGTERM + atexit) ensure dev stop behavior is applied on Ctrl-C or process exit.

Commands
- dev start [--no-ngrok] [--no-compose] [--no-watch] [--port N] [--auto-deploy=on|off] [--compose-file PATH] [--workflows-dir PATH] [--location REGION] [--project ID] [--reconfigure|-r] [-y|--yes] [--no-prompt]
  - Ensures .env, runs first-run configuration prompts, checks auth for the selected project, starts ngrok and exports BASE_URL, optionally composes up and starts the Scala watcher.
  - Interactive prompts are shown on first run or with --reconfigure. Selections can be saved to ~/.awfl/dev_config.json.
- dev stop [--no-ngrok] [--no-compose]
  - Stops the watcher, composes down, and stops ngrok (mirrors dev.sh teardown).
- dev status
  - Prints repo/workflows paths, ngrok URL, compose status, watcher status, last changed YAMLs, and default deploy target with env suffix.
- dev generate-yamls
  - Runs sbt clean compile and shows changed YAMLs.
- dev deploy-workflow workflows/yaml_gens/<file>.yaml
  - Substitutes ${WORKFLOW_ENV} via a temporary file, derives workflow name, and deploys with detailed history.
- dev logs [--follow]
  - Shows Docker Compose logs from the discovered compose file.

Configuration
- Persisted config path: ~/.awfl/dev_config.json
  - Keys: confirmed, ngrok_port, auto_deploy, use_ngrok, use_compose, use_watch, compose_file, workflows_dir, location, project
  - New auth keys (optional; can be left blank to use env or defaults):
    - firebase_api_key
    - google_oauth_client_id
    - google_oauth_client_secret
- Environment variables (selected):
  - AWFL_NGROK_PORT: default port for ngrok (default 8081)
  - AUTO_DEPLOY: on/off (default on)
  - AWFL_GCLOUD_LOCATION: default region (default us-central1)
  - PROJECT: default GCP project (default topaigents if unspecified in status)
  - WORKFLOW_ENV: suffix used in deploy names (e.g., Dev)
  - FIREBASE_API_KEY, GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET: override auth credentials for this shell session

Auth configuration precedence
- The CLI resolves Firebase and Google OAuth credentials at runtime with this precedence:
  1) Process environment variables: FIREBASE_API_KEY, GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET
  2) Per-repo dev config at ~/.awfl/<repo>/dev_config.json: firebase_api_key, google_oauth_client_id, google_oauth_client_secret
  3) Built-in development defaults defined in awfl/auth.py
- dev start exports any configured values into the current process environment before running authentication checks, so your session uses them immediately without restarting the CLI.

File structure
- commands.py: dispatches the dev subcommands (start/stop/logs/generate/deploy/status)
- core.py: thin compatibility layer re-exporting public APIs
- subcommands/
  - start.py: startup flow, interactive config, ngrok/compose/watcher bootstrap
  - stop.py: teardown flow for watcher/compose/ngrok, cleans state
  - logs.py: docker compose logs wrapper
  - generate.py: sbt trigger and YAML change reporting
  - deploy.py: WORKFLOW_ENV substitution, name derivation, deploy invocation
  - status.py: runtime summary (paths, ngrok, compose, watcher, last changes)
- helpers/
  - paths.py, dev_config.py, dev_state.py, ngrok_utils.py, docker_utils.py, yaml_ops.py, watcher.py, prompt_utils.py

Conventions and guardrails
- Use sys.executable for Python subprocesses outside this module to avoid mixed venvs.
- Prefer direct imports from concrete modules; keep core.py stable for external callers.
- Keep logs succinct and stable; use log_unique to avoid log storms.
- Be careful with RUN_COMMAND elsewhere in the CLI; dev commands are user-facing and should not invoke arbitrary shell without clear intent.

Troubleshooting tips
- Ngrok URL not detected: ensure ngrok is installed and in PATH; check AWFL_NGROK_PORT; run ngrok http <port> manually to test.
- Compose missing: set or confirm the compose file path via dev start --reconfigure; run docker compose ls to verify plugin presence.
- sbt build issues: ensure sbt and Java are installed; try dev generate-yamls to see errors and changed files.
- ADC/gcloud errors on deploy: ensure gcloud is installed and authenticated; verify PROJECT and AWFL_GCLOUD_LOCATION.

Roadmap
- Gradually migrate imports away from the core shim and add docstrings/types across helper modules.
- Optional: surface BASE_URL explicitly in dev status; add unit tests for derive_workflow_name and substitution logic.
