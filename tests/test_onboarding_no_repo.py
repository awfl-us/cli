import json
from pathlib import Path


def test_onboarding_creates_project_and_writes_config(tmp_path, monkeypatch):
    # Import here so conftest has already adjusted sys.path
    import awfl.onboarding.git as git_mod

    # Work in an isolated temp directory with no .git and no remotes
    monkeypatch.chdir(tmp_path)

    # Ensure not headless/CI and no explicit skip
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("AWFL_SKIP_GIT_ONBOARDING", raising=False)

    # Monkeypatch environment/behavior to simulate "no repo existed at startup"
    monkeypatch.setattr(git_mod, "_is_headless", lambda: False)
    monkeypatch.setattr(git_mod, "_git_available", lambda: True)
    monkeypatch.setattr(git_mod, "_has_git_repo", lambda cwd: False)
    monkeypatch.setattr(git_mod, "_ensure_local_repo_initialized", lambda cwd: None)
    monkeypatch.setattr(git_mod, "_has_remote", lambda cwd: False)
    # Simulate prompt returning a human name that needs sanitization
    monkeypatch.setattr(git_mod, "_prompt_project_name", lambda default: "My Project ")

    # Capture create call and set_project_id usage
    created = {"id": "proj-123", "name": "my-project"}
    create_calls = []

    def _fake_create(name: str):
        create_calls.append(name)
        return created

    monkeypatch.setattr(git_mod, "_create_project_no_remote_sync", _fake_create)

    set_calls = []

    def _fake_set_project_id(pid: str):
        set_calls.append(pid)

    monkeypatch.setattr(git_mod, "set_project_id", _fake_set_project_id)

    # Avoid external GitHub interactions
    monkeypatch.setattr(
        git_mod,
        "_attempt_github_push",
        lambda cwd, repo_name, visibility="private": None,
    )

    # Run onboarding
    git_mod.first_run_git_onboarding()

    # Validate project creation stub was called with sanitized name
    assert create_calls == ["my-project"], f"unexpected create calls: {create_calls}"

    # Validate .awfl/config.json was written
    cfg = tmp_path / ".awfl" / "config.json"
    assert cfg.is_file(), ".awfl/config.json was not created"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data.get("projectId") == "proj-123"
    assert data.get("name") == "my-project"

    # Validate set_project_id was invoked with the created project id
    assert set_calls == ["proj-123"], f"unexpected set_project_id calls: {set_calls}"
