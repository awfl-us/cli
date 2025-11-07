# AWFL CLI

[![License](https://img.shields.io/github/license/awfl-us/cli)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.13-3776AB)
![Status](https://img.shields.io/badge/Status-Beta-blueviolet)

<img src="docs/awfl-quickstart.svg" alt="AWFL CLI demo" width="750">

A developer-friendly command line for working with AI-powered workflows and codebase-aware agents.

AWFL CLI lets you converse with agent workflows from your terminal, apply code changes safely via tool calls, and watch live progress and events. It is designed for day‑to‑day development in AWFL repositories and any project where agents need to read, write, and run within your workspace.


Table of contents
- Overview
- Features
- Installation
- Quick start
- Demo
- Usage
  - Essential commands
  - Configuration
  - Authentication
- How it works (high level)
- Troubleshooting
- Development
- Contributing
- License
- Links


Overview
- AWFL is a local CLI that connects to an AI workflows service. You select a workflow (agent), chat in natural language, and the agent can execute side effects via tool calls such as writing files or running commands.
- The CLI streams events using Server‑Sent Events (SSE) and ensures side effects are executed exactly once per project, even if you’ve opened multiple terminals.


Features
- Talk to codebase‑aware agents from your terminal.
- Apply tool calls safely: write files, run shell commands, and more.
- Live event streaming with clear, deduplicated logs.
- Project‑wide leader election to avoid duplicate side effects across terminals.
- Flexible execution backends (API mode today; gcloud compatibility in progress).
- Developer helpers for local workflow development (watcher, logs, deploy).


Installation
- Prerequisites
  - Python 3.11+
  - Optional but recommended: pipx for isolated CLI installs
  - gcloud CLI if you plan to deploy or use gcloud execution modes

- Install with pipx (recommended)
  - pipx install awfl
  - Verify: awfl --version

- Install with pip (user environment)
  - pip install --user awfl
  - Ensure ~/.local/bin (or your platform’s user scripts dir) is on PATH.

- Install from source (for development)
  - git clone https://github.com/awfl-us/cli.git
  - cd cli
  - python -m venv .venv && source .venv/bin/activate
  - pip install -U pip
  - pip install -e .
  - Run: awfl --help


Quick start
- Launch
  - From your project directory: awfl
  - On first run you’ll be prompted to sign in with your Google identity (device login flow). Tokens are cached locally.
- Pick an agent
  - At the awfl prompt, type: workflows (or ls)
  - Use the tree selector to choose a workflow. Codebase‑oriented agents live under the “codebase” menu.
- Ask for help
  - Type your request in natural language. The selected agent will respond and, when appropriate, issue tool calls (e.g., write files, run commands). The project leader terminal applies these side effects; all terminals display them.


Demo
- Watch: See recording and export options in docs/DEMO.md.
- Transcript (60 seconds)
  ```
  $ awfl
  … connecting to server, session initialized …

  awfl> workflows
  … select: codebase-ProjectManager …

  awfl> "Add a Demo section to README and a recording script"
  Agent: Planning changes…
  Agent: Applying tool calls
    • UPDATE_FILE: README.md
    • UPDATE_FILE: docs/DEMO.md
    • UPDATE_FILE: scripts/record_demo.sh
    • RUN_COMMAND: chmod +x scripts/record_demo.sh
  Agent: Done. Review changes in git.
  ```


Usage
- Essential commands
  - workflows | ls
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
    - Set the server origin, e.g., http://localhost:5050. Trailing /api is normalized.
  - auth login | whoami | auth logout
    - Start device login, show the authenticated user, and clear cached tokens.
  - dev start | dev status | dev stop | dev logs | dev generate-yamls | dev deploy-workflow
    - Developer helpers for local stack and workflow development. dev start can run the watcher, compose, and ngrok.

- Configuration
  - API origin
    - set api_origin http://localhost:5050 sets the base URL. status shows the current value.
  - Model
    - model gpt-5 sets the LLM model that the CLI injects into workflow payloads.

- Authentication
  - The CLI uses Google Device Login followed by Firebase sign‑in. Tokens are cached under ~/.awfl.
  - You can skip auth in trusted local development with an environment flag (see status).


How it works (high level)
- Sessions
  - Session identity is resolved in order: ASSISTANT_WORKFLOW env (normalized) > selected active workflow (normalized) > local UUID fallback.
- Event streaming and side effects
  - Each terminal streams SSE events for its current session. One terminal per project acquires a lightweight project leader lock and applies tool calls (file writes, shell commands). Other terminals only display events, avoiding duplicate side effects.
- Workflow execution
  - The CLI posts to your server’s /api/workflows/execute, including the active model and session metadata. See AGENT.md for deeper internals.


Troubleshooting
- Login loop or auth errors
  - Run auth login again and confirm the device flow. Check whoami afterwards.
- No workflows listed
  - Ensure your server is running and API origin is correct. Use status to verify.
- Side effects not applying in this terminal
  - Another terminal likely holds the project leader role. That’s expected. Close it if you want this terminal to take over.


Development
- Repository layout
  - src/awfl: CLI sources (entry point, commands, consumers, response handling, utils)
  - src/awfl/cmds/dev: Developer helpers (watcher, deploy, logs)
- Local setup
  - python -m venv .venv && source .venv/bin/activate
  - pip install -e .
- Helpful commands
  - awfl dev status: Show environment, paths, watcher state
  - awfl dev start: Start ngrok/compose/watcher tasks as needed (returns to prompt)
  - awfl dev generate-yamls: Regenerate workflow YAMLs (project‑specific)
  - awfl dev deploy-workflow <yaml>: Deploy a single workflow via gcloud
- Notes
  - Prefer installing and running within a project‑local virtualenv to avoid path mismatches.
  - When spawning Python subprocesses, the CLI uses the current interpreter to keep environments consistent.


Contributing
- Issues and feature requests are welcome: https://github.com/awfl-us/cli/issues
- Pull requests
  - Fork the repo and create a feature branch.
  - Add or update tests where reasonable.
  - Keep changes focused and small; include a clear description and rationale.
  - Ensure linters/tests pass locally.
- Code of Conduct
  - Be respectful and constructive. Harassment or discrimination are not tolerated.


License
- MIT — see LICENSE for details.


Links
- Homepage: https://github.com/awfl-us/cli
- Issues: https://github.com/awfl-us/cli/issues
- Internals overview: see AGENT.md in the repo’s root
