from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

from story_channel.studio import Episodes, WorldStudio
from story_channel.locking import project_lock, ProjectBusyError
from test_studio import world


def test_delete_requires_exact_name_and_removes_only_world_and_episodes(world):
    manager = world.manager
    first = Episodes(manager, world.project_id).create("First episode", "A visitor arrives.", 30)
    second = Episodes(manager, world.project_id).create("Second episode", "The keeper rescues a memory.", 30)
    other = WorldStudio.create(manager, "Another independent World")
    title = manager.load(world.project_id).title
    with pytest.raises(ValueError, match="full project name"):
        manager.delete(world.project_id, confirmation=title + " ")
    assert manager.load(first.project_id)
    # No partial deletion if an episode is generating in another session.
    with project_lock(manager.project_path(first.project_id)):
        with pytest.raises(ProjectBusyError):
            manager.delete(world.project_id, confirmation=title)
    assert manager.load(world.project_id)
    removed = manager.delete(world.project_id, confirmation=title)
    assert set(removed) == {world.project_id, first.project_id, second.project_id}
    assert all(not manager.project_path(pid).exists() for pid in removed)
    assert manager.load(other.project_id)
    assert [p.project_id for p in manager.list_projects()] == [other.project_id]


def test_failed_removal_can_retry_and_cannot_be_written_again(world, monkeypatch):
    import shutil
    manager = world.manager
    saved = manager.load(world.project_id)
    with monkeypatch.context() as patch:
        patch.setattr(shutil, "rmtree", lambda path: (_ for _ in ()).throw(OSError("Injected removal failure")))
        with pytest.raises(OSError, match="Injected"):
            manager.delete(world.project_id, confirmation=saved.title)
    assert not manager.list_projects()
    with pytest.raises(FileNotFoundError, match="deleted"):
        manager.save(saved)
    with pytest.raises(FileNotFoundError, match="deleted"):
        with project_lock(manager.project_path(world.project_id)):
            pass
    manager.delete(world.project_id, confirmation=saved.title)
    assert not manager.project_path(world.project_id).exists()


def test_path_traversal_cannot_delete_another_folder(world, tmp_path):
    manager = world.manager
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError):
        manager.delete("../outside", confirmation="Anything")
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_delete_dialog_cancel_exact_name_and_last_project_reset(world, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="delete-project").click().run()
    assert not app.exception
    assert app.button(key="confirm-delete-project").disabled
    key = "delete-project-name-" + world.project_id
    app.text_input(key=key).set_value("Wrong name").run()
    assert app.button(key="confirm-delete-project").disabled
    app.button(key="cancel-delete-project").click().run()
    assert "delete-project-target" not in app.session_state
    assert world.manager.load(world.project_id)
    app.button(key="delete-project").click().run()
    assert app.text_input(key=key).value == ""
    app.text_input(key=key).set_value(world.manager.load(world.project_id).title).run()
    assert not app.button(key="confirm-delete-project").disabled
    app.button(key="confirm-delete-project").click().run()
    assert not app.exception
    assert not world.manager.list_projects()
    assert any(t.label == "Premise" for t in app.text_area)
    assert any(s.value == "Project deleted." for s in app.success)
