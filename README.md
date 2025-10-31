AWFL CLI

Open-source command-line tool to run AWFL workflows via Google Cloud Workflows (gcloud) or over HTTP API.

Install
- pipx install awfl-cli
  - or: pip install awfl-cli

Usage
- Direct call:
  - awfl call <workflowName> "your query text"
- Interactive shell:
  - awfl
  - help | call <workflow> [text] | exit

Execution modes
- WORKFLOW_EXEC_MODE=api (default)
  - API_ORIGIN (or BASE_URL) like http://localhost:5050 or your https host
  - Auth: run awfl login (recommended) or set FIREBASE_ID_TOKEN
- WORKFLOW_EXEC_MODE=gcloud
  - Requires gcloud installed and configured
  - PROJECT (default topaigents), LOCATION/us-central1

Environment
- WORKFLOW_ENV: suffix for workflow names in gcloud mode (default Dev). API mode auto-strips.
- LLM_MODEL: defaults to gpt-5 (passed to workflow params)

Dev mode (inside the repo)
- When run from a cloned repository, awfl reuses the full in-repo CLI (cli/main.py), preserving the original interactive UX and commands.
- Force packaged mode anywhere: AWFL_STANDALONE=1 awfl … or awfl --standalone

License
- Apache-2.0
