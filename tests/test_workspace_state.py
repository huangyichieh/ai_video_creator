from pathlib import Path
from threading import Event

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.config import Settings
from story_channel.costs import CostTracker
from story_channel.demo import DemoLLMProvider
from story_channel.llm import ProviderError
from story_channel.pipeline import Pipeline
from story_channel.projects import ProjectManager


def workspace(tmp_path, monkeypatch):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo"))
    outline = tmp_path / "outline.md"
    outline.write_text("A keeper restores memories.", encoding="utf-8")
    project = manager.create("Navigation", outline, target_duration_sec=30)
    monkeypatch.setenv("STORY_DATA_ROOT", str(manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    return manager, project, app


def test_other_session_lock_keeps_story_visible_read_only_and_recovers(tmp_path, monkeypatch):
    from story_channel.locking import project_lock
    manager, project, app = workspace(tmp_path, monkeypatch)
    outline_key = f"outline-{project.project_id}"
    app.text_area(key=outline_key).set_value("My unsaved draft").run()
    with project_lock(manager.project_path(project.project_id)):
        app.run()
        assert not app.exception
        assert not any("busy in another session" in item.value for item in app.error)
        assert app.session_state["external-project-busy"] is True
        assert app.text_area(key=outline_key).value == "My unsaved draft"
        assert app.button(key="generate-next-stage").disabled
        assert next(button for button in app.button if button.label == "Save outline").disabled
        assert any("Showing saved data" in item.value for item in app.caption)
    app.run()
    assert not app.exception
    assert "external-project-busy" not in app.session_state
    assert not app.button(key="generate-next-stage").disabled
    assert app.text_area(key=outline_key).value == "My unsaved draft"


def test_manifest_permission_error_shows_retry_instead_of_traceback(tmp_path, monkeypatch):
    from story_channel.projects import ProjectManager
    from story_channel.pipeline import Pipeline
    manager, project, app = workspace(tmp_path, monkeypatch)
    original = Pipeline.__init__
    def denied(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", "manifest.json")
    monkeypatch.setattr(Pipeline, "__init__", denied)
    app.run()
    assert not app.exception
    assert any("Project files cannot be accessed" in item.value for item in app.error)
    assert app.button(key="retry-project-file-access")
    monkeypatch.setattr(Pipeline, "__init__", original)
    app.button(key="retry-project-file-access").click().run()
    assert not app.exception
    assert app.button(key="generate-next-stage")


def test_unsaved_outline_and_dialog_survive_navigation(tmp_path, monkeypatch):
    manager, project, app = workspace(tmp_path, monkeypatch)
    Pipeline(manager, project.project_id).generate("world")
    app.run()
    app.button(key="nav-story-settings").click().run()
    app.text_area(key=f"outline-{project.project_id}").set_value("Unsaved outline").run()
    app.button(key="nav-final").click().run()
    app.button(key="nav-story-settings").click().run()
    assert app.text_area(key=f"outline-{project.project_id}").value == "Unsaved outline"
    assert manager.read_outline(project.project_id) != "Unsaved outline"
    app.button(key="nav-story-settings").click().run()
    app.button(key="browse-world").click().run()
    key = f"browser-draft-{project.project_id}-world-world"
    draft = app.text_area(key=key).value + "\n "
    app.text_area(key=key).set_value(draft).run()
    app.button(key="nav-analytics").click().run()
    app.button(key="nav-story-settings").click().run()
    assert not app.exception
    assert app.text_area(key=key).value == draft


@pytest.mark.parametrize("fail", [False, True])
def test_request_continues_while_navigating_and_records_usage(tmp_path, monkeypatch, fail):
    manager, project, app = workspace(tmp_path, monkeypatch)
    release = Event()
    original = DemoLLMProvider.generate

    def delayed(self, *args, **kwargs):
        assert release.wait(timeout=20)
        if fail:
            raise ProviderError("Simulated API failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DemoLLMProvider, "generate", delayed)
    try:
        app.button(key="generate-next-stage").click().run()
        task = app.session_state["workspace-task"]
        assert not task["future"].done()
        app.button(key="nav-story-settings").click().run()
        assert not app.exception
        app.text_area(key=f"outline-{project.project_id}").set_value("Draft while generating").run()
        assert next(button for button in app.button if button.label == "Save outline").disabled
        app.button(key="nav-assets").click().run()
        assert not app.exception
        app.button(key="nav-story-settings").click().run()
        assert app.button(key="generate-next-stage").disabled
        release.set()
        if fail:
            with pytest.raises(ProviderError):
                task["future"].result(timeout=10)
        else:
            task["future"].result(timeout=10)
        app.run()
        assert not app.exception
        assert manager.load(project.project_id).steps["world"].status == ("failed" if fail else "succeeded")
        if fail:
            assert any("Simulated API failure" in error.value for error in app.error)
        assert len(CostTracker(manager, project.project_id).records()) == 1
        assert not app.button(key="generate-next-stage").disabled
        app.button(key="nav-story-settings").click().run()
        assert app.text_area(key=f"outline-{project.project_id}").value == "Draft while generating"
    finally:
        release.set()


def test_project_selection_survives_reruns_new_projects_and_creation_mode(tmp_path,monkeypatch):
    manager,first,app=workspace(tmp_path,monkeypatch)
    source=tmp_path/'second.md'
    source.write_text('Second outline',encoding='utf-8')
    second=manager.create('Second story',source)
    app.run()
    for target in (second,first,second,first):
        app.selectbox(key='project-selector').select(target.project_id).run()
        assert not app.exception
        assert app.session_state['selected_project']==target.project_id
        for _ in range(2):
            app.run()
            assert app.selectbox(key='project-selector').value==target.project_id
            assert any(h.value==target.title for h in app.subheader)
    # A changed options list must not reset the selection to the newest project.
    manager.create('Newly added story',source)
    app.run()
    assert app.selectbox(key='project-selector').value==first.project_id
    next(b for b in app.button if b.label.endswith('New project')).click().run()
    assert any(h.value=='New project' for h in app.header)
    app.selectbox(key='project-selector').select(second.project_id).run()
    assert not app.session_state['creating_project']
    assert any(h.value==second.title for h in app.subheader)
    # Creating another project selects the new project without widget-state conflicts.
    next(b for b in app.button if b.label.endswith('New project')).click().run()
    next(t for t in app.text_input if t.label=='Title').set_value('Created in UI')
    next(t for t in app.text_area if t.label=='Story outline').set_value('Brand new outline')
    next(b for b in app.button if b.label=='Create project').click().run()
    assert not app.exception
    assert any(h.value=='Created in UI' for h in app.subheader)
    app.run()
    assert manager.load(app.selectbox(key='project-selector').value).title=='Created in UI'


def test_project_switch_during_background_generation_stays_selected(tmp_path,monkeypatch):
    manager,first,app=workspace(tmp_path,monkeypatch)
    source=tmp_path/'other.md'
    source.write_text('Another story',encoding='utf-8')
    second=manager.create('Other story',source)
    app.run()
    app.selectbox(key='project-selector').select(first.project_id).run()
    release=Event()
    original=DemoLLMProvider.generate
    def delayed(self,*args,**kwargs):
        assert release.wait(timeout=20)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(DemoLLMProvider,'generate',delayed)
    try:
        app.button(key='generate-next-stage').click().run()
        task=app.session_state['workspace-task']
        app.selectbox(key='project-selector').select(second.project_id).run()
        app.run()
        assert not app.exception
        assert app.selectbox(key='project-selector').value==second.project_id
        release.set()
        task['future'].result(timeout=10)
        app.run()
        app.run()
        assert app.selectbox(key='project-selector').value==second.project_id
        assert any(h.value==second.title for h in app.subheader)
        assert manager.load(first.project_id).steps['world'].status=='succeeded'
        assert not manager.load(second.project_id).steps
    finally:
        release.set()
