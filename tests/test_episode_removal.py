from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.studio import Episodes,WorldStudio
from story_channel.locking import project_lock,ProjectBusyError
from test_studio import world


def test_exact_title_and_world_scope_preserve_siblings_and_parent(world):
    service = Episodes(world.manager,world.project_id)
    first = service.create("Episode One","First outline",60)
    sibling = service.create("Episode Two","Second outline",60)
    other = WorldStudio.create(world.manager,"Another independent World")
    with pytest.raises(ValueError,match="full project name"):
        service.remove(first.project_id,confirmation="Episode One ")
    with pytest.raises(ValueError,match="does not belong"):
        Episodes(world.manager,other.project_id).remove(first.project_id,confirmation=first.title)
    with project_lock(world.manager.project_path(first.project_id)):
        with pytest.raises(ProjectBusyError):
            service.remove(first.project_id,confirmation=first.title)
    assert world.manager.load(first.project_id)
    assert service.remove(first.project_id,confirmation=first.title) == [first.project_id]
    assert not world.manager.project_path(first.project_id).exists()
    assert world.manager.load(world.project_id)
    assert world.manager.load(sibling.project_id)
    assert world.manager.load(other.project_id)


def test_interrupted_episode_removal_can_retry(world,monkeypatch):
    import shutil
    service = Episodes(world.manager,world.project_id)
    episode = service.create("Episode One","Outline",60)
    with monkeypatch.context() as patch:
        patch.setattr(shutil,"rmtree",lambda path:(_ for _ in ()).throw(OSError("Injected removal failure")))
        with pytest.raises(OSError,match="Injected"):
            service.remove(episode.project_id,confirmation=episode.title)
    service.remove(episode.project_id,confirmation=episode.title)
    assert not world.manager.project_path(episode.project_id).exists()
    assert world.manager.load(world.project_id)


@pytest.mark.parametrize("has_sibling",[False,True])
def test_outline_dialog_cancel_wrong_title_and_confirmed_removal(world,monkeypatch,has_sibling):
    service = Episodes(world.manager,world.project_id)
    episode = service.create("Episode One","Outline to delete",60)
    sibling = service.create("Episode Two","Preserved sibling outline",60) if has_sibling else None
    for key,value in {"STORY_DATA_ROOT":str(world.manager.root),"STORY_LLM_PROVIDER":"demo",
                      "STORY_ASSET_PROVIDER":"demo","OPENAI_ADMIN_KEY":""}.items():
        monkeypatch.setenv(key,value)
    app = AppTest.from_file(str(Path(__file__).parents[1]/"app.py"),default_timeout=25)
    app.session_state[f"selected-episode-{world.project_id}"] = episode.project_id
    app.run()
    app.button(key="nav-story-settings").click().run()
    app.button(key="episode-remove-"+episode.project_id).click().run()
    assert not app.exception
    assert app.button(key="confirm-remove-episode").disabled
    title_key = "remove-episode-title-"+episode.project_id
    app.text_input(key=title_key).input(episode.title+" ").run()
    assert app.button(key="confirm-remove-episode").disabled
    app.button(key="cancel-remove-episode").click().run()
    assert not app.exception
    assert world.manager.load(episode.project_id)
    app.button(key="episode-remove-"+episode.project_id).click().run()
    assert app.text_input(key=title_key).value == ""
    app.text_input(key=title_key).input(episode.title).run()
    assert not app.button(key="confirm-remove-episode").disabled
    app.button(key="confirm-remove-episode").click().run()
    assert not app.exception
    assert not world.manager.project_path(episode.project_id).exists()
    assert app.session_state["selected_project"] == world.project_id
    assert app.session_state["workspace_page"] == "Story/Episode"
    if sibling:
        assert world.manager.load(sibling.project_id)
        assert app.session_state[f"selected-episode-{world.project_id}"] == sibling.project_id
        assert app.text_area(key=f"studio-draft-episode-{sibling.project_id}-outline").value == "Preserved sibling outline"
    else:
        assert not app.tabs
        assert any("No episodes yet" in caption.value for caption in app.caption)
