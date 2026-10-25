from test_workspace_state import workspace

from story_channel.pipeline import Pipeline


def finish_save(app):
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=10)
        app.run()
    assert not app.exception


def test_outline_dialog_saves_and_invalidates(tmp_path, monkeypatch):
    manager, project, app = workspace(tmp_path, monkeypatch)
    pipeline = Pipeline(manager, project.project_id)
    pipeline.generate("world")
    app.run()
    app.button(key="edit-story-outline").click().run()
    key = f"outline-dialog-draft-{project.project_id}"
    assert app.text_area(key=key).value == manager.read_outline(project.project_id)
    app.text_area(key=key).set_value("A keeper discovers a lost city.").run()
    app.button(key="save-outline-dialog").click().run()
    finish_save(app)
    assert manager.read_outline(project.project_id) == "A keeper discovers a lost city."
    assert app.text_area(key=f"outline-{project.project_id}").value == "A keeper discovers a lost city."
    assert manager.load(project.project_id).steps["world"].status == "stale"
    assert list((pipeline.path / "history" / "outline").glob("*.md"))
    assert not any(button.key == "save-outline-dialog" for button in app.button)


def test_outline_dialog_preserves_drafts_and_rejects_empty(tmp_path, monkeypatch):
    manager, project, app = workspace(tmp_path, monkeypatch)
    baseline = manager.read_outline(project.project_id)
    tab_key = f"outline-{project.project_id}"
    app.text_area(key=tab_key).set_value("Unsaved tab draft").run()
    app.button(key="edit-story-outline").click().run()
    key = f"outline-dialog-draft-{project.project_id}"
    assert app.text_area(key=key).value == "Unsaved tab draft"
    app.text_area(key=key).set_value("  ").run()
    app.button(key="save-outline-dialog").click().run()
    assert any("must not be empty" in error.value for error in app.error)
    assert manager.read_outline(project.project_id) == baseline
    app.button(key="cancel-outline-dialog").click().run()
    assert app.text_area(key=tab_key).value == "Unsaved tab draft"
    assert not app.exception


def test_outline_dialog_rejects_concurrent_edits(tmp_path, monkeypatch):
    manager, project, app = workspace(tmp_path, monkeypatch)
    app.button(key="edit-story-outline").click().run()
    key = f"outline-dialog-draft-{project.project_id}"
    app.text_area(key=key).set_value("Dialog draft").run()
    Pipeline(manager, project.project_id).edit("outline", "Saved in another session")
    app.button(key="save-outline-dialog").click().run()
    app.run()
    assert not app.exception
    assert manager.read_outline(project.project_id) == "Saved in another session"
    assert app.text_area(key=key).value == "Dialog draft"
    assert any("changed in another session" in error.value for error in app.error)
