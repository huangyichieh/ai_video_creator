from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.creative import StoryboardBGM
from story_channel.bgm_library import add,attach,entries,scene_music,ready
from story_channel.storyboard_editor import save,inventory
from story_channel.storyboard_assets import sync,requests
from story_channel.media_utils import silence
from story_channel.modules import validate_output
from story_channel.studio import Episodes
from test_scene_production import episode
from test_studio import world


def test_storyboard_bgm_requirements_are_registered_and_preserve_attached_audio(episode):
    world,project,pipeline = episode
    board = pipeline.artifact("storyboard")
    board.bgm = [StoryboardBGM(bgm_id="office_theme",title="Office theme",description="Soft piano, light rhythm, no vocals")]
    board.scenes[0].bgm_id = "office_theme"
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    save(pipeline,board.model_dump(mode="json",by_alias=True),source)
    assert "office_theme" in entries(world.manager,world.project_id)
    assert requests(world.manager,world.project_id,"bgm")[0]["sources"][0]["sceneIds"] == [board.scenes[0].scene_id]
    item = attach(world.manager,world.project_id,"office_theme",silence(.2),"theme.wav","Original music, owned by creator")
    assert ready(world.path,item)
    sync(world.manager,world.project_id)
    assert entries(world.manager,world.project_id)["office_theme"] == item
    Episodes(world.manager,world.project_id).refresh_assets(project.project_id)
    music = scene_music(world.manager,project.project_id,board.scenes[0])
    assert music and (pipeline.path/music["file"]).is_file()
    assert music["source"] == "Original music, owned by creator"


def test_editor_can_select_existing_bgm_and_rejects_stale_saves(episode):
    world,project,pipeline = episode
    add(world.manager,world.project_id,"existing_theme","Existing theme","Strings and piano")
    attach(world.manager,world.project_id,"existing_theme",silence(.2),"theme.wav","Original music")
    data = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    data["scenes"][0].update(bgmId="existing_theme",action="Reach for the beacon switch")
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    save(pipeline,data,source)
    stored = pipeline.artifact("storyboard")
    assert stored.scenes[0].bgm_id == "existing_theme"
    assert stored.bgm[0].bgm_id == "existing_theme"
    assert stored.scenes[0].action == "Reach for the beacon switch"
    assert scene_music(world.manager,project.project_id,stored.scenes[0])
    assert "storyboard" not in world.manager.load(project.project_id).approvals
    assert world.manager.load(project.project_id).steps["prompts"].status == "stale"
    assert pipeline.inspect().steps["storyboard"].status == "succeeded"
    with pytest.raises(ValueError,match="another session"):
        save(pipeline,data,source)


def test_storyboard_editor_ui_has_asset_selectors_and_editable_scenes(episode):
    world,project,pipeline = episode
    add(world.manager,world.project_id,"draft_theme","Draft theme","Warm piano")
    def display(pipeline):
        from story_channel.ui_tasks import preserve_page_widgets
        from story_channel.storyboard_editor_ui import render
        preserve_page_widgets()
        render(pipeline)
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    assert not app.exception
    assert any(field.label == "Background" for field in app.selectbox)
    music = next(field for field in app.selectbox if field.label == "BGM")
    assert any("New / pending" in str(option) and "draft_theme" in str(option) for option in music.options)
    assert any(field.label == "Characters" for field in app.multiselect)
    assert any(field.label == "Props" for field in app.multiselect)
    action = next(field for field in app.text_area if field.label == "Action")
    original = action.value
    action.set_value("Temporary draft action").run()
    next(button for button in app.button if button.label == "Revert changes").click().run()
    assert not app.exception
    assert next(field for field in app.text_area if field.label == "Action").value == original
    action = next(field for field in app.text_area if field.label == "Action")
    action.set_value("Carefully inspect the beacon switch").run()
    next(button for button in app.button if button.label == "Save storyboard changes").click().run()
    assert not app.exception
    assert pipeline.artifact("storyboard").scenes[0].action == "Carefully inspect the beacon switch"


def test_new_bgm_is_in_readable_storyboard_requirements(episode):
    from story_channel.markdown_exports import storyboard_markdown
    world,project,pipeline = episode
    board = pipeline.artifact("storyboard")
    board.bgm = [StoryboardBGM(bgm_id="new_theme",title="New theme",description="Low strings, restrained percussion")]
    board.scenes[0].bgm_id = "new_theme"
    text = storyboard_markdown(board)
    assert "BGM asset:** new_theme" in text
    assert "BGM new_theme: **New theme**" in text
