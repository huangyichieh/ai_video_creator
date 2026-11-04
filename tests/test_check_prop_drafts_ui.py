import json
import pytest
from streamlit.testing.v1 import AppTest

from story_channel.files import atomic_write,input_hash
from story_channel.library_media import LibraryMedia
from story_channel.prop_drafts import save
from test_scene_production import episode
from test_studio import world


def test_assets_prop_title_is_draft_until_every_mode_has_an_intact_visual(world):
    media = LibraryMedia(world.manager,world.project_id)
    save(media,"case","Case","A leather case",["closed","open"],expected=input_hash(None))
    def display(manager,pid):
        from story_channel.check_prop_drafts_ui import render_library
        from story_channel.library_media import LibraryMedia
        render_library(LibraryMedia(manager,pid))
    app = AppTest.from_function(display,args=(world.manager,world.project_id),default_timeout=25).run()
    key = f"asset-prop-editor-{world.project_id}-case"
    def title():
        assert not app.exception
        return next(panel for panel in app.expander if panel.key == key).label
    assert title().endswith(":orange[Draft]")
    media.image("prop:case:closed","Closed leather case")
    app.run()
    assert title().endswith(":orange[Draft]")
    media.image("prop:case:open","Open leather case")
    app.run()
    assert title().endswith(":green[Ready]")
    version = media.load()["visuals"]["prop:case:open"][-1]
    media.assets.local_path(version["asset"]["filePath"]).unlink()
    app.run()
    assert title().endswith(":orange[Draft]")


def declaration(episode):
    world,project,pipeline = episode
    board = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    board["props"] = [{"propId":"briefcase","title":"Briefcase","description":"A worn leather briefcase","modes":["closed","open"]}]
    board["scenes"][0]["propIds"] = ["briefcase"]
    atomic_write(pipeline.path/"scenes.json",json.dumps(board))
    return world,project,pipeline


def test_missing_prop_modes_share_one_collapsed_editor_and_save_to_world(episode,monkeypatch):
    world,project,pipeline = declaration(episode)
    before = (pipeline.path/"scenes.json").read_bytes()
    def display(manager,project_id):
        from story_channel.storyboard_asset_check_ui import render
        render(manager,project_id)
    app = AppTest.from_function(display,args=(world.manager,project.project_id),default_timeout=25).run()
    key = f"check-prop-editor-{project.project_id}-briefcase"
    assert not app.exception
    panels = [panel for panel in app.expander if panel.key == key]
    assert len(panels) == 1 and not panels[0].proto.expanded
    assert not any(field.label == "Prop description" for field in app.text_area)
    app.session_state[key] = True
    app.run()
    assert not app.exception
    assert next(field for field in app.text_area if field.label == "Prop description").value == "A worn leather briefcase"
    monkeypatch.setattr("story_channel.check_prop_drafts_ui.start",lambda media,episode_id,pid,callback,*args,**kwargs:callback())
    next(button for button in app.button if button.label == "Add prop draft").click().run()
    assert not app.exception
    media = LibraryMedia(world.manager,world.project_id)
    assert media.load()["props"]["briefcase"]["modes"] == ["closed","open"]
    next(field for field in app.text_area if field.label == "Prop description").set_value("A black leather briefcase").run()
    next(button for button in app.button if button.label == "Save prop draft").click().run()
    assert not app.exception
    assert media.load()["props"]["briefcase"]["description"] == "A black leather briefcase"
    assert (pipeline.path/"scenes.json").read_bytes() == before
    assert len([button for button in app.button if button.label == "Generate mode visual"]) == 2
    calls = []
    monkeypatch.setattr(LibraryMedia,"image",lambda self,asset_key,prompt,*args,**kwargs:calls.append((self.project_id,asset_key,prompt)))
    next(button for button in app.button if button.key and button.key.endswith("-generate-open")).click().run()
    assert not app.exception
    assert calls[0][0:2] == (world.project_id,"prop:briefcase:open")
    assert "black leather briefcase" in calls[0][2]


def test_prop_edits_preserve_visual_history_and_reject_stale_save(episode):
    world,project,pipeline = declaration(episode)
    media = LibraryMedia(world.manager,world.project_id)
    prop = save(media,"briefcase","Briefcase","Brown leather",["closed","open"],expected=input_hash(None))
    state = media.load()
    state["visuals"]["prop:briefcase:closed"] = [{"revision":1,"asset":{"filePath":"saved.png"}}]
    media.save(state)
    save(media,"briefcase","Briefcase","Black leather",["closed","open"],expected=input_hash(prop))
    assert media.load()["visuals"]["prop:briefcase:closed"] == state["visuals"]["prop:briefcase:closed"]
    with pytest.raises(ValueError,match="another session"):
        save(media,"briefcase","Briefcase","Overwritten",["closed"],expected=input_hash(prop))


def test_prop_generation_syncs_only_that_prop_and_approval_is_episode_owned(episode):
    from story_channel.check_prop_assets import sync,approve,digest
    from story_channel.storyboard_asset_check import check
    world,project,pipeline = declaration(episode)
    media = LibraryMedia(world.manager,world.project_id)
    save(media,"briefcase","Briefcase","Brown leather",["closed","open"],expected=input_hash(None))
    production_before = (pipeline.path/"production.json").read_bytes()
    board_before = (pipeline.path/"scenes.json").read_bytes()
    for mode in ("closed","open"):
        media.image("prop:briefcase:"+mode,"Brown leather briefcase, "+mode)
    assert not any(row["Ready"] for row in check(world.manager,project.project_id) if row["Category"] == "Props")
    sync(media,project.project_id,"briefcase")
    from story_channel.identity import IdentityManager
    assert IdentityManager(world.manager,project.project_id).load().props["briefcase"].description == "Brown leather"
    rows = [row for row in check(world.manager,project.project_id) if row["Category"] == "Props"]
    assert len(rows) == 2 and all(row["Ready"] and row["Approved"] for row in rows)
    expected = digest(media.load(),"briefcase",["closed","open"])
    approve(media,project.project_id,"briefcase",["closed","open"],expected=expected)
    assert all(row["Approved"] for row in check(world.manager,project.project_id) if row["Category"] == "Props")
    assert not (media.path/"storyboard_asset_approvals.json").exists()
    assert (pipeline.path/"production.json").read_bytes() == production_before
    assert (pipeline.path/"scenes.json").read_bytes() == board_before
    media.image("prop:briefcase:closed","New briefcase revision")
    with pytest.raises(ValueError,match="prop changed"):
        approve(media,project.project_id,"briefcase",["closed","open"],expected=expected)
    sync(media,project.project_id,"briefcase")
    rows = [row for row in check(world.manager,project.project_id) if row["Category"] == "Props"]
    assert next(row for row in rows if row["ID"].endswith(":open"))["Approved"]
    assert next(row for row in rows if row["ID"].endswith(":closed"))["Approved"]


def test_single_prop_card_has_generation_and_automatic_readiness_on_check_page(episode):
    world,project,pipeline = declaration(episode)
    media = LibraryMedia(world.manager,world.project_id)
    save(media,"briefcase","Briefcase","Brown leather",["closed","open"],expected=input_hash(None))
    def display(manager,project_id):
        from story_channel.storyboard_asset_check_ui import render
        render(manager,project_id)
    app = AppTest.from_function(display,args=(world.manager,project.project_id),default_timeout=25).run()
    key = f"check-prop-editor-{project.project_id}-briefcase"
    app.session_state[key] = True
    app.run()
    assert not app.exception and not any(button.label == "Approve prop" for button in app.button)
    assert next(panel for panel in app.expander if panel.key == key).label.startswith("🔴 ")
    assert not any("Review / Approve" in panel.label and "Briefcase" in panel.label for panel in app.expander)
    for mode in ("closed","open"):
        media.image("prop:briefcase:"+mode,"Brown leather briefcase, "+mode)
    app.run()
    assert not app.exception and not any(button.label == "Approve prop" for button in app.button)
    assert next(panel for panel in app.expander if panel.key == key).label.startswith("🟢 ")
    assert next(panel for panel in app.expander if panel.key == key).proto.expanded
    from story_channel.storyboard_asset_check import check
    assert all(row["Approved"] for row in check(world.manager,project.project_id) if row["Category"] == "Props")
    def library_display(manager,project_id):
        from story_channel.check_prop_drafts_ui import render_library
        from story_channel.library_media import LibraryMedia
        render_library(LibraryMedia(manager,project_id))
    library_app = AppTest.from_function(library_display,args=(world.manager,world.project_id),default_timeout=25).run()
    library_app.session_state[f"asset-prop-editor-{world.project_id}-briefcase"] = True
    library_app.run()
    assert not library_app.exception
    assert len([button for button in library_app.button if button.label == "Generate mode visual"]) == 2
    assert not any(button.label == "Approve prop" for button in library_app.button)
