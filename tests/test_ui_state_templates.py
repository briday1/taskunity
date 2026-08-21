from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import taskunity.app as app_module
from taskunity.app import create_app
from taskunity.task_store import create_task, ensure_workspace, load_task, save_task, upsert_project


def _make_client(workspace: Path) -> TestClient:
    ensure_workspace(workspace)
    return TestClient(create_app(workspace))


def test_git_sync_route_preserves_open_task_panel(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = create_task(workspace, "Keep task panel open")

    monkeypatch.setattr(app_module, "git_sync", lambda _: {"ok": True, "message": "Synced cleanly."})

    client = _make_client(workspace)
    response = client.post(
        "/git/sync",
        headers={"HX-Request": "true"},
        data={
            "f_view": "list",
            "f_panel_task": task.id,
        },
    )

    assert response.status_code == 200
    assert 'class="git-toast success"' in response.text
    assert "Synced cleanly." in response.text
    assert "Keep task panel open" in response.text
    assert 'id="git-chip-slot" hx-swap-oob="true"' in response.text


def test_git_sync_route_preserves_calendar_filters_and_error_state(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(app_module, "git_sync", lambda _: {"ok": False, "message": "Push failed."})

    client = _make_client(workspace)
    response = client.post(
        "/git/sync",
        data={
            "f_view": "calendar",
            "f_hide_done": "1",
            "f_calendar_month": "5",
            "f_calendar_year": "2027",
        },
    )

    assert response.status_code == 200
    assert 'class="git-toast error"' in response.text
    assert 'name="f_calendar_month" value="5"' in response.text
    assert 'name="f_calendar_year" value="2027"' in response.text
    assert 'name="f_hide_done" value="1"' in response.text


def test_projects_view_click_filters_to_task_list_without_show_only_button(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = upsert_project(workspace, "Apollo", description="Moonshot")
    assert project is not None

    task = create_task(workspace, "Linked task")
    task.project_id = project.id
    task.project = project.name
    save_task(workspace, task)

    client = _make_client(workspace)

    projects_response = client.get("/partials/main?view=projects")
    assert projects_response.status_code == 200
    assert 'class="project-open-form"' in projects_response.text
    assert 'name="view" value="list"' in projects_response.text
    assert f'name="project" value="{project.id}"' in projects_response.text
    assert 'title="Edit Apollo"' in projects_response.text

    panel_response = client.get(f"/projects/{project.id}/panel?view=projects")
    assert panel_response.status_code == 200
    assert "Show only this project" not in panel_response.text


def test_bundled_client_supports_git_sync_contract(tmp_path: Path) -> None:
    client = _make_client(tmp_path / "workspace")

    response = client.get("/static/htmx.min.js")

    assert response.status_code == 200
    script = response.text
    assert "selector === 'this'" in script
    assert "window.htmx.trigger" in script
    assert "[hx-swap-oob]" in script
    assert "'HX-Request': 'true'" in script
    assert "load') === 0" in script
    assert "every ') === 0" in script


def test_task_activity_update_refreshes_current_list(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = create_task(workspace, "Refresh this row")
    client = _make_client(workspace)

    response = client.post(
        f"/tasks/{task.id}/update",
        headers={"HX-Request": "true"},
        data={
            "progress_after": "65",
            "status": "working",
            "priority": "critical",
            "f_view": "list",
            "f_hide_done": "1",
        },
    )

    assert response.status_code == 200
    assert '<main id="app-main"' in response.text
    assert 'class="task-row working"' in response.text
    assert '<span class="priority-tag critical">critical</span>' in response.text
    assert 'title="65% complete"' in response.text
    saved = load_task(workspace, task.id)
    assert saved.status == "working"
    assert saved.priority == "critical"
    assert saved.percent_complete == 65


def test_task_activity_update_refreshes_calendar_after_date_change(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = create_task(workspace, "Move onto calendar")
    client = _make_client(workspace)

    response = client.post(
        f"/tasks/{task.id}/update",
        headers={"HX-Request": "true"},
        data={
            "progress_after": "20",
            "status": "working",
            "priority": "high",
            "save_due_date": "2027-05-18",
            "f_sort": "due_date",
            "f_sort_dir": "desc",
            "f_view": "calendar",
            "f_hide_done": "1",
            "f_calendar_month": "5",
            "f_calendar_year": "2027",
        },
    )

    assert response.status_code == 200
    assert "May 2027" in response.text
    assert 'class="cal-task working"' in response.text
    assert "Move onto calendar" in response.text
    assert 'name="f_sort" value="due_date"' in response.text
    assert 'name="f_sort_dir" value="desc"' in response.text
    assert 'name="f_calendar_month" value="5"' in response.text
    assert 'name="f_calendar_year" value="2027"' in response.text


def test_task_completed_from_activity_update_is_hidden_by_default(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = create_task(workspace, "Finish and hide")
    client = _make_client(workspace)

    response = client.post(
        f"/tasks/{task.id}/update",
        headers={"HX-Request": "true"},
        data={
            "progress_after": "100",
            "status": "done",
            "priority": "normal",
            "f_view": "list",
            "f_hide_done": "1",
        },
    )

    assert response.status_code == 200
    assert "No tasks match the current filters." in response.text
    assert 'class="task-row done"' not in response.text
    assert 'name="f_hide_done" value="1"' in response.text
    assert load_task(workspace, task.id).status == "done"
