# AWFL CLI

A developer-friendly terminal for LLM‑assisted coding and repo automation.

AWFL CLI lets you talk to agent workflows from your terminal, apply code changes via tool calls, and track progress live. It’s designed for day‑to‑day work in AWFL repositories and other projects, with a focus on codebase‑aware agents and safe side‑effect execution.

Quick start
- Install (recommended via pipx)
  - pipx install awfl
  - Verify: awfl --version
- Launch
  - From your project directory: awfl
  - On first run you’ll be prompted to sign in with your Google identity (device login flow). The CLI caches tokens locally for future runs.
- Pick an agent
  - At the awfl prompt, type: ls
  - Use the tree selector to choose a workflow. The most useful agents for repo work live under the “codebase” menu.
- Ask for help
  - Type your request in natural language. The selected agent will respond and, when appropriate, execute tool calls (e.g., write files, run commands) through the project‑wide consumer described below.

Essential commands
- ls | workflows
  - Open the interactive workflow selector and set the active agent for this session.
- call <workflow> [args...]
  - Invoke a specific workflow one‑off without changing the active agent.
- model [name]
  - View or set the LLM model injected into workflow requests.
- stop | cancel | abort
  - Cancel the currently active workflow execution.
- status
  - Show execution mode, API origin, active workflow, and other runtime details.
- set api_origin <url>
  - Point the CLI to your server’s base URL (e.g., http://localhost:5050 during local dev). If you include a trailing /api, the CLI will normalize it to avoid double /api.
- auth login | whoami | auth logout
  - Start device login, inspect auth, and clear cached tokens.
- use api | exec api
  - Ensure API mode is selected (default). gcloud execution is not implemented in this snapshot.
- dev start | dev watch | dev status | dev stop | dev logs | dev generate-yamls | dev deploy-workflow
  - Developer helpers for local stack and workflow development.
  - dev watch is a convenience alias for: dev start --no-ngrok --no-compose (runs just the watcher).

How events and side effects work
- The CLI streams workflow events over SSE and applies tool calls through two complementary consumers so you can open multiple terminals without duplicating side effects.
  - Project‑wide consumer (leader)
    - The first awfl terminal you start in a repo acquires a lightweight “project leader” lock (per project derived from your git remote) and executes tool side effects for all sessions in that project. This keeps file edits and shell commands single‑sourced.
  - Session consumer(s)
    - Every terminal also streams events for its currently selected session and logs progress/messages. Session consumers do not execute side effects; they display them.
- This model lets you run multiple conversations with different agents at the same time. Only the leader applies changes; all sessions show consistent progress.

Implementation notes (for reference)
- Session identity is resolved in order: ASSISTANT_WORKFLOW env (normalized) > selected active workflow (normalized) > local fallback.
- Event routing logs first, then executes side effects when in leader mode.
- trigger_workflow posts to {API_ORIGIN}/api/workflows/execute with Firebase auth. The server applies any WORKFLOW_ENV suffix; the CLI sends unsuffixed workflow names in API mode.
- API origin resolution:
  - If API_ORIGIN is set, it is used (trailing /api is stripped).
  - Dev mode (WORKFLOW_ENV set): prefer local ngrok https; else http://localhost:5050.
  - Prod mode: https://topaigents.com.

Tips
- Start one awfl terminal first in a repo; it will take the project leader role. Open additional terminals to chat with other agents—those will log events for their sessions without duplicating side effects.
- If you see “Project-wide SSE already active,” another terminal holds the leader lock. That’s expected and avoids double‑executing tool calls.
- If your server is not at the default origin, run: set api_origin http://localhost:5050 (or your URL), then status to confirm.

Troubleshooting
- Login loop or auth issues: run auth login again and confirm the browser/device flow completes. Check whoami to verify.
- No workflows listed: ensure your server is running and API origin is correct. status shows the current origin.
- No side effects applying from this terminal: that usually means another terminal is the project leader; open that terminal to see execution logs, or close it if you want this one to take over.

License
- MIT