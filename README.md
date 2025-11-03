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
  - Use the tree selector to choose a workflow. The most useful agents for repo work live under the “codebase” menu (e.g., for managing awfl‑us projects).
- Ask for help
  - Type your request in natural language. The selected agent will respond and, when appropriate, execute tool calls (e.g., write files, run commands) through the project‑wide consumer described below.

Essential commands
- ls | workflows
  - Open the interactive workflow selector and set the active agent for this session. You’ll typically choose from the codebase category for repository tasks.
- call <workflow> [args...]
  - Invoke a specific workflow one‑off without changing the active agent.
- model [name]
  - View or set the LLM model injected into workflow requests.
- stop | cancel | abort
  - Cancel the currently active workflow execution.
- status
  - Show execution mode, API origin, active workflow, and other runtime details.
- use api | exec api
  - Use API mode to execute workflows via the server (default for most users).
- auth login | login
  - Start Google device login and cache tokens for API calls.
- whoami | auth status
  - Show your authenticated user (when in API mode).
- auth logout
  - Remove cached tokens.
- set api_origin <url>
  - Point the CLI to your server’s base URL (e.g., http://localhost:5050 during local dev).
- dev start | dev status | dev stop
  - Developer helpers to run ngrok, docker compose, and the workflows watcher during local development. Optional, but convenient if you’re working across the full stack.

How events and side effects work (for the curious)
- The CLI streams workflow events and applies tool calls through two complementary consumers so you can open multiple terminals without duplicating side effects.
  - Project‑wide consumer (leader)
    - The first awfl terminal you start in a repo acquires a lightweight “project leader” lock (per project derived from your git remote) and executes tool side effects for all sessions in that project. This keeps file edits and shell commands single‑sourced.
  - Session consumer(s)
    - Every terminal also streams events for its currently selected session and logs progress and messages. Session consumers do not execute side effects; they display them.
- This model lets you run multiple conversations with different agents at the same time. Only the leader applies changes; all sessions show consistent progress.
- Implementation notes (kept brief for new users)
  - Project/workspace resolution uses your git remote to find or create a project and then registers a workspace for the current session.
  - A simple lock file under ~/.awfl/locks ensures one local leader per project; if the leader exits, another terminal can take over automatically.
  - Events stream over SSE; the CLI resumes from the last cursor so you don’t miss updates after reconnects.
  - Key modules (for reference only):
    - src/awfl/events/workspace.py
    - src/awfl/consumer/sse_consumer.py
    - src/awfl/consumer/leader_lock.py
    - src/awfl/consumer/debug.py

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