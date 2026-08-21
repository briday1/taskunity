from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

from fastapi.testclient import TestClient

import taskunity.app as app_module
from taskunity.app import create_app
from taskunity.models import Milestone
from taskunity.task_store import (
    create_task,
    ensure_workspace,
    load_all_milestones,
    save_task,
    upsert_project,
)


def _client(workspace: Path) -> TestClient:
    ensure_workspace(workspace)
    return TestClient(create_app(workspace))


def test_done_tasks_are_hidden_by_default_and_can_be_explicitly_shown(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = create_task(workspace, "Already shipped")
    task.status = "done"
    save_task(workspace, task)
    client = _client(workspace)

    hidden = client.get("/partials/main?view=list")
    shown = client.get("/partials/main?view=list&show_done=1")

    assert "Already shipped" not in hidden.text
    assert "Already shipped" in shown.text
    assert 'name="show_done" value="1" checked' in shown.text


def test_save_and_add_another_keeps_project_and_visibility_context(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = upsert_project(workspace, "Apollo")
    assert project is not None
    client = _client(workspace)

    response = client.post(
        "/tasks/create",
        data={
            "title": "First batch task",
            "project": project.id,
            "f_project": project.id,
            "f_hide_done": "1",
            "f_hide_old": "1",
            "continue_creating": "1",
        },
    )

    assert response.status_code == 200
    assert "First batch task" in response.text
    assert "Save &amp; add another" in response.text
    assert f'name="project" value="{project.id}"' in response.text
    assert "hide_old=1" in response.text
    assert "hide_done=1" in response.text

    task_files = list((workspace / "tasks").glob("*/task.json"))
    assert len(task_files) == 1
    stored = json.loads(task_files[0].read_text(encoding="utf-8"))
    assert stored["project_id"] == project.id
    assert "project" not in stored


def test_legacy_milestone_project_names_are_migrated_out_of_storage(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    ensure_workspace(workspace)
    milestone = Milestone(id="M-LEGACY", title="Legacy", projects=["Renamed project"])
    path = workspace / "milestones" / "M-LEGACY.json"
    path.write_text(json.dumps(milestone.model_dump(mode="json")), encoding="utf-8")

    loaded = load_all_milestones(workspace)

    assert loaded[0].id == "M-LEGACY"
    assert "projects" not in json.loads(path.read_text(encoding="utf-8"))


def test_ai_chat_surfaces_provider_error_body(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = _client(workspace)

    def fail_request(*_args, **_kwargs):
        body = io.BytesIO(b'{"error":{"message":"Key is valid but not active"}}')
        raise urllib.error.HTTPError(
            "https://ai.example/v1/chat/completions", 403, "Forbidden", {}, body
        )

    monkeypatch.setattr(urllib.request, "urlopen", fail_request)
    response = client.post(
        "/ai/chat",
        data={
            "ai_enabled": "1",
            "ai_base_url": "https://ai.example",
            "ai_model": "test-model",
            "context_type": "workspace",
            "entity_id": "workspace",
            "user_message": "Hello",
        },
    )

    assert response.status_code == 200
    assert "HTTP 403 Forbidden" in response.text
    assert "Key is valid but not active" in response.text


def test_composer_and_initial_main_do_not_block_on_git_or_reload_tasks(
    monkeypatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    ensure_workspace(workspace)
    project = upsert_project(workspace, "Fast project")
    assert project is not None

    def unexpected_work(*_args, **_kwargs):
        raise AssertionError("interaction path performed expensive synchronous work")

    monkeypatch.setattr(app_module, "git_status", unexpected_work)
    client = TestClient(create_app(workspace))

    main = client.get("/partials/main?view=list", headers={"HX-Request": "true"})
    assert main.status_code == 200
    assert "Checking" in main.text

    monkeypatch.setattr(app_module, "load_all_tasks", unexpected_work)
    composer = client.get(f"/new/task/panel?view=list&project={project.id}")
    assert composer.status_code == 200
    assert f'name="project" value="{project.id}"' in composer.text
