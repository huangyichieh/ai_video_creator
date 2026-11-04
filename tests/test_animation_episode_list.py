from pathlib import Path

from streamlit.testing.v1 import AppTest

from story_channel.studio import Episodes
from test_studio import world
from test_scene_production import episode
from test_episode_list_ui import select_row


def open_app(world,monkeypatch,selected=None):
    for key,value in {"STORY_DATA_ROOT":str(world.manager.root),"STORY_LLM_PROVIDER":"demo",
                      "STORY_ASSET_PROVIDER":"demo","OPENAI_ADMIN_KEY":""}.items():
        monkeypatch.setenv(key,value)
    app = AppTest.from_file(str(Path(__file__).parents[1]/"app.py"),default_timeout=30)
    if selected:
        app.session_state[f"selected-episode-{world.project_id}"] = selected
    app.run()
    app.button(key="nav-animations").click().run()
    return app


def test_animations_list_available_for_unapproved_episodes_and_selection_is_shared(world,monkeypatch):
    service = Episodes(world.manager,world.project_id)
    first = service.create("Older episode","Older outline",60)
    second = service.create("Newer episode","Newer outline",120)
    app = open_app(world,monkeypatch,first.project_id)
    assert not app.exception
    assert list(app.dataframe[0].value["Title"]) == ["Newer episode","Older episode"]
    select_row(app,0)
    assert not app.exception
    assert app.session_state["workspace_page"] == "Animations"
    assert app.session_state["generation-context-project"] == second.project_id
    assert app.selectbox(key=f"episode-selector-{world.project_id}").value == second.project_id
    app.button(key="nav-story-settings").click().run()
    assert app.text_area(key=f"studio-draft-episode-{second.project_id}-outline").value == "Newer outline"


def test_selecting_draft_episode_hides_previous_episode_animation_controls(episode,monkeypatch):
    world,ready,_ = episode
    draft = Episodes(world.manager,world.project_id).create("Unapproved episode","Draft outline",30)
    app = open_app(world,monkeypatch,ready.project_id)
    assert not app.exception
    assert any(tab.label == "Scene images" for tab in app.tabs)
    select_row(app,0)
    assert not app.exception
    assert app.session_state["generation-context-project"] == draft.project_id
    assert not any(tab.label == "Scene images" for tab in app.tabs)
    assert any("Approve this episode" in message.value for message in app.info)


def test_no_episodes_shows_list_empty_state(world,monkeypatch):
    app = open_app(world,monkeypatch)
    assert not app.exception
    assert any("No episodes yet" in message.value for message in app.caption)
