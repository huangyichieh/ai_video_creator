import json
from concurrent.futures import Future
from threading import Event

from streamlit.testing.v1 import AppTest

from story_channel.studio import Episodes
from test_studio import world


def app_for(world):
    service = Episodes(world.manager,world.project_id)
    first = service.create("Episode One","First episode outline only",65)
    second = service.create("Episode Two","Second episode outline only",125)
    world.manager.save(first)  # Editing an older episode must not move it above newer creations.
    def display(manager,world_id):
        from story_channel.ui_tasks import preserve_page_widgets
        from story_channel.studio_ui import episode_page
        preserve_page_widgets()
        episode_page(manager,world_id)
    app = AppTest.from_function(display,args=(world.manager,world.project_id),default_timeout=25)
    app.session_state[f"selected-episode-{world.project_id}"] = first.project_id
    app.run()
    return app,first,second


def select_row(app,index):
    frame = next(frame for frame in app.dataframe if "Episode" in frame.value.columns and "Title" in frame.value.columns)
    states = app._tree.get_widget_states()
    state = next((item for item in states.widgets if item.id == frame.proto.id),None)
    if state is None:
        state = states.widgets.add(id=frame.proto.id)
    state.string_value = json.dumps({"selection":{"rows":[index],"columns":[],"cells":[]}})
    app._run(states)


def test_episode_list_switches_only_selected_content_and_preserves_drafts(world):
    app,first,second = app_for(world)
    assert not app.exception
    rows = app.dataframe[0].value
    assert list(rows["Title"]) == ["Episode Two","Episode One"]
    assert list(rows["Target length"]) == ["00:02:05","00:01:05"]
    first_key = f"studio-draft-episode-{first.project_id}-outline"
    second_key = f"studio-draft-episode-{second.project_id}-outline"
    app.text_area(key=first_key).input("Unsaved draft for episode one").run()
    select_row(app,0)
    assert not app.exception
    assert app.session_state[f"selected-episode-{world.project_id}"] == second.project_id
    assert app.text_area(key=second_key).value == "Second episode outline only"
    assert not any(field.key == first_key for field in app.text_area)
    assert all(second.project_id in tab.key for tab in app.tabs if tab.key)
    select_row(app,1)
    assert not app.exception
    assert app.text_area(key=first_key).value == "Unsaved draft for episode one"


def test_selection_during_generation_keeps_task_bound_to_original_episode(world):
    app,first,second = app_for(world)
    future = Future()
    task = {"future":future,"project_id":first.project_id,"label":"Generating episode story","stop_event":Event()}
    app.session_state["workspace-task"] = task
    app.session_state["studio-modify-target"] = {"project_id":first.project_id,"kind":"story"}
    select_row(app,0)
    assert not app.exception
    assert app.session_state[f"selected-episode-{world.project_id}"] == second.project_id
    assert app.session_state["workspace-task"]["future"] is future
    assert app.session_state["workspace-task"]["project_id"] == first.project_id
    assert "studio-modify-target" not in app.session_state
    future.set_result(None)


def test_empty_episode_list_shows_creation_hint_without_content_tabs(world):
    def display(manager,world_id):
        from story_channel.studio_ui import episode_page
        episode_page(manager,world_id)
    app = AppTest.from_function(display,args=(world.manager,world.project_id),default_timeout=25).run()
    assert not app.exception
    assert not app.tabs
    assert any("No episodes yet" in item.value for item in app.caption)


def test_list_and_sidebar_share_selection_and_project_context(world,monkeypatch):
    from pathlib import Path
    service = Episodes(world.manager,world.project_id)
    first = service.create("Episode One","First episode outline",60)
    second = service.create("Episode Two","Second episode outline",120)
    for key,value in {"STORY_DATA_ROOT":str(world.manager.root),"STORY_LLM_PROVIDER":"demo",
                      "STORY_ASSET_PROVIDER":"demo","OPENAI_ADMIN_KEY":""}.items():
        monkeypatch.setenv(key,value)
    app = AppTest.from_file(str(Path(__file__).parents[1]/"app.py"),default_timeout=25)
    app.session_state[f"selected-episode-{world.project_id}"] = first.project_id
    app.run()
    app.button(key="nav-story-settings").click().run()
    assert not app.exception
    select_row(app,0)
    assert not app.exception
    assert app.selectbox(key=f"episode-selector-{world.project_id}").value == second.project_id
    assert app.session_state["generation-context-project"] == second.project_id
    assert any(title.value == "Episode Two" for title in app.subheader)
    app.selectbox(key=f"episode-selector-{world.project_id}").select(first.project_id).run()
    assert not app.exception
    assert app.session_state[f"selected-episode-{world.project_id}"] == first.project_id
    assert app.text_area(key=f"studio-draft-episode-{first.project_id}-outline").value == "First episode outline"
