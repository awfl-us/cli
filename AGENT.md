CLI Agent Overview

Purpose
- Developer CLI to submit queries to AI workflows, watch repo changes to trigger workflows, and process Pub/Sub-driven responses and operational events.

Current task
- Update each TODO item in the manual's TOC one at a time, performing any necessary research and asking clarifying questions as needed.

Entry point: main.py
- Sets gRPC log env vars to reduce noise.
- Initializes a session UUID and sets it in response_handler.
- Starts:
  - File system watcher (watchdog) that reads diffs vs HEAD and triggers cli-CommentAddedDev when any added line contains " ai:".
  - Pub/Sub consumers:
    - consumers/
- Interactive prompt loop:
  - Shows recent log lines.
  - Accepts commands; if not a command, sends the text to the currently selected workflow (default codebase-ProjectManagerDev).
- Uses state.get_active_workflow + normalize_workflow; allows overriding session with ASSISTANT_WORKFLOW env.

Session identity
- response_handler.get_session resolution order:
  1) ASSISTANT_WORKFLOW env (normalized to dashed form)
  2) state.get_active_workflow (normalized)
  3) Local UUID set by main.py
- response_handler.set_session(new_uuid) called at startup.

Pub/Sub consumers FIX: We no longer consumer pubsub, now we consumer the SSE from the service (consumer/)
- consumers/
  - Subscribes to the given subscription and decodes message.data as JSON.
  - Filters by attributes.sessionId equal to get_session(); messages with payload.background == true bypass filtering (always processed). Legacy messages whose attributes.sessionId starts with "background-" are also treated as background for backward compatibility.
  - On process success: ACK. On failure or (non-background) session mismatch: NACK for redelivery.
  - Forwards the JSON to response_handler.handle_response.

Response handling (response_handler/) FIX: response_handler.py was broken into multiple files to be cleaner
- handle_response(data: dict)
  - Uses data.create_time when provided for timestamps.
  - Results are POSTed exclusively via the internal callback service using callback_id. If callback_id is missing, no callback is sent and a log line is emitted.
  - Tool calls: data.tool_call.function.name determines action (case-insensitive handling via .upper()):
    - UPDATE_FILE: write content to path (create parents). Sends callback payload with filepath.
    - READ_FILE: read text and send up to READ_FILE_MAX_BYTES (default 200000) with truncated flag.
    - RUN_COMMAND: runs shell command, captures stdout/stderr; truncates stdout to 50,000 chars.
    - Unknown tools: log "Unknown tool".
  - Back-compat direct action path is present but commented out.
- post_internal_callback(callback_id, payload)
  - Builds URL from get_api_origin() and posts to {origin}/api/workflows/callbacks/{callback_id}; no fallback paths.
  - Uses get_auth_headers() to include user Authorization (Firebase ID token) or X-Skip-Auth and x-project-id.
  - Respects timeouts controlled by env:
    - CALLBACK_TIMEOUT_SECONDS (default 25)
    - CALLBACK_CONNECT_TIMEOUT_SECONDS (default 5)
    - CALLBACK_RETRY_DELAY_MS (default 500)
  - Minimal retry policy: one attempt plus at most one fixed-delay retry on transient errors (HTTP 429 or 5xx) or network/timeout errors. No Retry-After handling and no logging.

Workflow execution utilities (utils.py)
- log_unique(text)
  - Dedupes consecutive identical lines by SHA1; keeps last 20 lines; prints via prompt_toolkit fallback to print().
- get_api_origin()
  - Prefers API_ORIGIN env; default http://localhost:5050/api for local dev.
- trigger_workflow(name, data)
  - Adds LLM_MODEL (default gpt-5) to data and BASE_URL.
  - Two execution modes via WORKFLOW_EXEC_MODE env:
    - gcloud (default): runs `gcloud workflows execute` with JSON --data; extracts execution name and spawns a waiter thread.
    - api: POST {origin}/workflows/execute with Firebase Auth headers; logs response; if an execution name is returned, also spawns the waiter thread.
  - Waiter thread (_wait_for_execution_and_log) uses `gcloud workflows executions wait`; on success attempts to parse result JSON and log cost if present.
- cancel_active_execution()
  - Issues `gcloud workflows executions cancel` in a non-blocking Popen; clears active execution immediately.
- Global/project constants: PROJECT=topaigents, LOCATION=us-central1.

State management (state.py)
- DEFAULT_WORKFLOW = codebase-ProjectManager
- normalize_workflow: strips leading "workflows.", replaces dots with dashes (e.g., workflows.cli.QuerySubmitted -> codebase-ProjectManager).
- Tracks active workflow and active execution (resource name + workflow name). Helpers to set/get/clear.
- Dev runtime helpers: dev commands keep ephemeral state within the dev_cmds module; state.py remains minimal.

Commands (commands.py)
- Discovers workflow YAML files under ../workflows/yaml_gens and provides an interactive tree selector.
- Key commands (see help text):
  - login | auth login: Google Device login and cache Firebase tokens
  - whoami | auth status: Show authenticated user (API mode)
  - logout | auth logout: Remove cached tokens (~/.awfl/tokens.json)
  - use api | exec api / use gcloud | exec gcloud: Switch execution mode
  - status: Show mode, API origin, BASE_URL, SKIP_AUTH, token override
  - set api_origin <url>: Set API_ORIGIN
  - auth skip on|off: Toggle SKIP_AUTH
  - auth set-token <idToken> / auth clear-token: Manage FIREBASE_ID_TOKEN override
  - ls | workflows: Open selector and set active workflow (sets {wf}Dev)
  - call <workflow> [args...]: Invoke a specific workflow
  - model [name]: Get/set LLM model injected into payload
  - stop | cancel | abort: Cancel active workflow execution
  - dev …: Development helpers for ngrok/compose/watch/build/deploy (see below)

Dev command group (dev_cmds.py)
- dev status: Show repo/workflows paths, ngrok URL, compose status, watcher state, recent YAML updates, default deploy target
- dev start [options]: Optionally start ngrok, docker compose, and a Scala workflows watcher that runs sbt to regenerate YAMLs and optionally auto-deploys changed YAMLs. Runs as asyncio Tasks inside the CLI; returns to prompt.
- dev stop: Stop watcher and tear down compose/ngrok if started by this session.
- dev logs [--follow]: Tail docker compose logs for the discovered compose file.
- dev generate-yamls: Run sbt clean compile in workflows dir and list updated YAMLs.
- dev deploy-workflow <yaml_path>: Deploy a single YAML via gcloud with WORKFLOW_ENV suffix.

Authentication (auth.py)
- Implements Google Device Flow and Firebase sign-in to get an ID token; caches in ~/.awfl/tokens.json.
- Key env vars (with defaults in code; override in production):
  - FIREBASE_API_KEY
  - GOOGLE_OAUTH_CLIENT_ID
  - GOOGLE_OAUTH_CLIENT_SECRET (optional)
- Endpoints used:
  - Google Device Code: https://oauth2.googleapis.com/device/code
  - Google Token: https://oauth2.googleapis.com/token
  - Firebase signInWithIdp: https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp
  - Firebase token refresh: https://securetoken.googleapis.com/v1/token
- get_auth_headers() (imported elsewhere) returns headers with Authorization: Bearer <idToken> unless SKIP_AUTH=1 (then X-Skip-Auth: 1); supports FIREBASE_ID_TOKEN override; refreshes tokens via Firebase as needed.

Dependencies
- requirements.txt includes: prompt_toolkit, watchdog, aiohttp, pathspec, requests, google-cloud-pubsub.
- Additional implicit dependencies used by the code (ensure they are installed):
  - google-auth (for google.auth and transport requests)
  - sbt (for dev generate-yamls / watcher)
  - docker + docker compose plugin (for dev compose commands)
  - ngrok (optional; dev start autodetects existing tunnels)
- External tools:
  - gcloud CLI installed and authenticated for gcloud execution mode and waiter/cancel commands; also required for dev deploy-workflow.

Local virtual environment (.venv) FIX: Now packaged with pyproject.toml
- Scope
  - The CLI assumes a project-local virtual environment at cli/.venv for dependency isolation and reproducible runs while developing features.
- Conventions
  - Use cli/.venv as the canonical environment for this CLI. Avoid using a repo-root .venv for CLI work to prevent interpreter/path mismatches.
  - When installing or upgrading packages for the CLI, ensure cli/.venv is the active interpreter so entry-point shebangs bind to cli/.venv/bin/python.
  - Keep requirements.txt in sync with what’s installed in cli/.venv when adding imports in code.
- Known hazards we’ve observed
  - Mixed venvs: Some scripts inside cli/.venv/bin were generated with shebangs pointing at the repo-root .venv. This happens if a different venv was active during installation. It can cause imports to resolve from the wrong site-packages.
  - Remedy: Recreate cli/.venv and reinstall from requirements.txt with only cli/.venv active; or force-reinstall affected packages so their entry points reference cli/.venv/bin/python.
  - Python version: Current cli/.venv was created with Python 3.13 (Homebrew). Packages like aiohttp, grpc, and google-cloud-pubsub are present with 3.13 wheels. If changing Python versions, fully rebuild cli/.venv.
  - SSL on macOS: Prior logs showed urllib3 NotOpenSSLWarning with LibreSSL. Prefer a Python build linked against OpenSSL (e.g., Homebrew python) to avoid TLS issues.
- Guardrails for development
  - Consider exporting PIP_REQUIRE_VIRTUALENV=1 to avoid accidental global installs.
  - When adding subprocess calls to Python, use sys.executable to ensure the current cli/.venv interpreter is used.
  - gcloud is external and unaffected by the Python venv; no change needed there.

Operational notes and cautions
- Pub/Sub consumers require ADC or appropriate credentials; otherwise they will fail to subscribe.
- consume_cli_operations NACKs on non-matching session unless payload.background == true (legacy: or sessionId starts with background-), enabling other subscribers to process the message.
- RUN_COMMAND executes arbitrary shell; only enabled via tool_call messages—ensure upstream is trusted.
- File watcher triggers on diffs vs HEAD for lines starting with "+" that contain " ai:"; ensure this convention is intentional.
- log_unique keeps only last 20 unique lines; repeated identical logs are collapsed.
- Pub/Sub subscription constants are in main.py; consider moving to config in packaging work.
- Dev commands start background tasks within the CLI; use dev stop to clean up.

Quick start
- pip install -r requirements.txt (also install google-auth)
- Ensure gcloud is installed and ADC is available (or use API mode and run login).
- python cli/main.py
- In the prompt, use workflows to select an assistant, then type queries or use call <workflow>.
- For local development: dev start, touch Scala files, dev status, dev stop.
