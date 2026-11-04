from streamlit.testing.v1 import AppTest

from story_channel.audio_assets import add_sfx
from story_channel.check_asset_sync import save_audio_settings,audio_settings
from story_channel.studio import Episodes
from test_studio import world


def test_saved_audio_settings_follow_both_pages_without_overwriting_unsaved_drafts(world):
    identity = add_sfx(world.manager,world.project_id,"Clock tick","A clock ticking")
    episode = Episodes(world.manager,world.project_id).create("Audio settings","An office scene",12)
    def display(manager,root,episode_id,identity):
        import streamlit as st
        from story_channel.storyboard_audio_ui import controls
        pid = root if st.session_state.get("where","assets") == "assets" else episode_id
        controls(manager,pid,{"Category":"SFX","ID":identity,"Asset":"Clock tick","Description":"A clock ticking"},"settings-"+pid)
    app = AppTest.from_function(display,args=(world.manager,world.project_id,episode.project_id,identity)).run()
    assert not app.exception and app.text_area[0].value == ""
    app.session_state["where"] = "check"
    app.run()
    app.text_area[0].set_value("Soft mechanical ticking").run()
    app.number_input[0].set_value(5.).run()
    app.checkbox[0].check().run()
    app.button(key="settings-"+episode.project_id+"-save-settings").click().run()
    assert not app.exception
    assert audio_settings(world.manager,world.project_id,"SFX",identity) == {"prompt":"Soft mechanical ticking","duration":5.,"loop":True}
    app.session_state["where"] = "assets"
    app.run()
    assert not app.exception
    assert app.text_area[0].value == "Soft mechanical ticking"
    assert app.number_input[0].value == 5 and app.checkbox[0].value is True
    app.text_area[0].set_value("Unsaved local draft").run()
    save_audio_settings(world.manager,episode.project_id,"SFX",identity,"New shared setting",7.,False)
    app.run()
    assert app.text_area[0].value == "Unsaved local draft"
    app.button(key="settings-"+world.project_id+"-revert-settings").click().run()
    assert not app.exception
    assert app.text_area[0].value == "New shared setting"
    assert app.number_input[0].value == 7 and app.checkbox[0].value is False
