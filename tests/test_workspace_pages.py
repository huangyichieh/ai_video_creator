from test_workspace_state import workspace


def test_project_settings_and_provider_drafts_survive_navigation(tmp_path, monkeypatch):
    manager, project, app = workspace(tmp_path, monkeypatch)
    prefix = f"project-setting-{project.project_id}-"
    app.text_input(key=prefix + "title").set_value("Edited project").run()
    app.number_input(key=prefix + "minutes").set_value(2).run()
    app.button(key="nav-assets").click().run()
    app.button(key="nav-story-settings").click().run()
    assert not app.exception
    assert app.text_input(key=prefix + "title").value == "Edited project"
    assert manager.load(project.project_id).title == "Navigation"
    next(b for b in app.button if b.label == "Save project settings").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=10)
        app.run()
    assert not app.exception
    saved = manager.load(project.project_id)
    assert saved.title == "Edited project" and saved.target_duration_sec == 150
    app.selectbox(key=prefix + "generation-mode").select("OpenAI").run()
    app.text_input(key=prefix + "llm-model").set_value("saved-model-draft").run()
    app.button(key="nav-analytics").click().run()
    app.button(key="nav-story-settings").click().run()
    assert app.text_input(key=prefix + "llm-model").value == "saved-model-draft"
    assert not (manager.project_path(project.project_id) / "usage.jsonl").exists()


def test_old_outline_page_migrates_without_losing_the_outline(tmp_path, monkeypatch):
    manager, project, app = workspace(tmp_path, monkeypatch)
    app.session_state["workspace_page"] = "Outline"
    app.run()
    assert not app.exception
    assert app.session_state["workspace_page"] == "Story & Settings"
    assert app.text_area(key=f"outline-{project.project_id}").value == manager.read_outline(project.project_id)
