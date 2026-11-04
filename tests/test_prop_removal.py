import pytest
from streamlit.testing.v1 import AppTest
from story_channel.library_media import LibraryMedia
from story_channel.files import input_hash
from story_channel.prop_drafts import save
from story_channel.prop_removal import remove,snapshot
from story_channel.identity import IdentityManager
from test_studio import world
from test_scene_production import episode


def create(world):
    world.generate_characters()
    media = LibraryMedia(world.manager,world.project_id)
    save(media,"case","Briefcase","Leather briefcase",["open","closed"],expected=input_hash(None))
    for mode in ("open","closed"):
        media.image("prop:case:"+mode,"Leather case "+mode)
    return media


def test_mode_deletion_keeps_other_modes_and_can_be_explicitly_recreated(world):
    media = create(world)
    before = media.load()
    removed_file = media.assets.local_path(before["visuals"]["prop:case:open"][0]["asset"]["filePath"])
    kept = before["visuals"]["prop:case:closed"]
    remove(media,"case",mode="open",expected=snapshot(before,"case","open"))
    state = media.load()
    assert state["props"]["case"]["modes"] == ["closed"]
    assert "prop:case:open" not in state["visuals"] and "prop:case:open" not in state["activeVisuals"]
    assert not removed_file.exists() and state["visuals"]["prop:case:closed"] == kept
    assert "case" in IdentityManager(world.manager,world.project_id).load().props
    with pytest.raises(ValueError,match="was removed"):
        media.image("prop:case:open","Queued old request")
    save(media,"case","Briefcase","Leather briefcase",["open","closed"],expected=input_hash(state["props"]["case"]))
    media.image("prop:case:open","Explicitly recreated")
    remove(media,"case",mode="closed",expected=snapshot(media.load(),"case","closed"))
    remove(media,"case",mode="open",expected=snapshot(media.load(),"case","open"))
    assert media.load()["props"]["case"]["modes"] == []


def test_whole_prop_requires_exact_name_and_removes_all_revisions(world):
    media = create(world)
    before = media.load()
    expected = snapshot(before,"case")
    with pytest.raises(ValueError,match="full prop name"):
        remove(media,"case",confirmation="case",expected=expected)
    assert media.load() == before
    media.image("prop:case:open","New revision")
    with pytest.raises(ValueError,match="changed"):
        remove(media,"case",confirmation="Briefcase",expected=expected)
    files = [media.assets.local_path(version["asset"]["filePath"]) for versions in media.load()["visuals"].values() for version in versions]
    remove(media,"case",confirmation="Briefcase",expected=snapshot(media.load(),"case"))
    state = media.load()
    assert "case" not in state["props"] and not state["visuals"] and not state["activeVisuals"]
    assert all(not file.exists() for file in files)
    assert "case" not in IdentityManager(world.manager,world.project_id).load().props


def test_sync_does_not_recreate_deleted_library_requirements(episode):
    world,project,pipeline = episode
    from test_storyboard_prop_removal import setup
    setup(pipeline)
    media = LibraryMedia(world.manager,world.project_id)
    prop = media.load()["props"]["briefcase"]
    from story_channel.storyboard_assets import sync
    remove(media,"briefcase",mode="open",expected=snapshot(media.load(),"briefcase","open"))
    sync(world.manager,world.project_id)
    assert media.load()["props"]["briefcase"]["modes"] == ["closed"]
    board_before = (pipeline.path/"scenes.json").read_bytes()
    remove(media,"briefcase",confirmation=prop["title"],expected=snapshot(media.load(),"briefcase"))
    sync(world.manager,world.project_id)
    assert "briefcase" not in media.load()["props"]
    assert (pipeline.path/"scenes.json").read_bytes() == board_before


def test_prop_removal_rejects_files_outside_owned_image_directory(world):
    media = create(world)
    state = media.load()
    state["visuals"]["prop:case:open"][0]["asset"]["filePath"] = "manifest.json"
    media.save(state)
    original = (media.path/"manifest.json").read_bytes()
    with pytest.raises(ValueError,match="outside its generated image directory"):
        remove(media,"case",confirmation="Briefcase",expected=snapshot(state,"case"))
    assert media.load() == state
    assert (media.path/"manifest.json").read_bytes() == original


def test_library_buttons_open_confirmation_and_require_prop_name(world):
    media = create(world)
    def display(media):
        import streamlit as st
        if st.session_state.get("remove-prop-target"):
            from story_channel.dialog_ui import show_pending_dialog
            show_pending_dialog()
        from story_channel.check_prop_drafts_ui import render_library
        render_library(media)
    app = AppTest.from_function(display,args=(media,),default_timeout=25).run()
    key = f"asset-prop-editor-{world.project_id}-case"
    app.session_state[key] = True
    app.run()
    assert not app.exception
    app.button(key=key+"-remove").click().run()
    assert not app.exception and app.button(key="confirm-remove-prop").disabled
    field = next(field for field in app.text_input if field.label == "Type the full prop name to confirm")
    field.input("Briefcase").run()
    assert not app.button(key="confirm-remove-prop").disabled
    app.button(key="cancel-remove-prop").click().run()
    assert "case" in media.load()["props"]
    app.button(key=key+"-remove-mode-open").click().run()
    assert not app.exception and not app.button(key="confirm-remove-prop").disabled
    assert not any(field.label == "Type the full prop name to confirm" for field in app.text_input)
    app.button(key="confirm-remove-prop").click().run()
    assert not app.exception and media.load()["props"]["case"]["modes"] == ["closed"]
