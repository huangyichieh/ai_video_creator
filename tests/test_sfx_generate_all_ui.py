from concurrent.futures import Future
from streamlit.testing.v1 import AppTest


def test_generate_all_queues_each_sfx_once_with_current_settings(monkeypatch):
    monkeypatch.setattr("story_channel.check_asset_sync.save_audio_settings",lambda *args,**kwargs:None)
    import streamlit as st
    from types import SimpleNamespace
    calls,queued = [],[]
    def start(manager,pid,callback,label,service,resource):
        task = {"future":Future(),"phase":"queued","ui_owned_asset_project":pid,"resource":resource}
        st.session_state.setdefault("workspace-task-queue",[]).append(task)
        queued.append((task,callback,service))
        return task
    monkeypatch.setattr("story_channel.storyboard_audio_ui.start",start)
    monkeypatch.setattr("story_channel.storyboard_audio.generate",lambda manager,pid,kind,key,prompt,duration,**kwargs:calls.append((pid,kind,key,prompt,duration,kwargs["loop"])))
    def display():
        import streamlit as st
        from story_channel.storyboard_audio_ui import generate_all_sfx_button
        st.session_state.setdefault("check-sfx-episode-sound_a-generate-prompt","Rain against glass")
        st.session_state.setdefault("check-sfx-episode-sound_a-duration",7.)
        st.session_state.setdefault("check-sfx-episode-sound_a-loop",True)
        rows = [{"Category":"SFX","ID":"existing","Asset":"Existing unapproved sound","Ready":True,"Approved":False},
            {"Category":"SFX","ID":"sound_a","Asset":"Rain","Ready":False},
            {"Category":"SFX","ID":"sound_b","Asset":"Door closing","Ready":False},
            {"Category":"SFX","ID":"sound_a","Asset":"Rain","Ready":False},
            {"Category":"BGM","ID":"music","Asset":"Piano","Ready":False}]
        generate_all_sfx_button(None,"episode",rows)
    app = AppTest.from_function(display,default_timeout=10).run()
    app.button(key="check-sfx-generate-all-episode").click().run()
    assert not app.exception
    assert len(queued) == 2
    assert all(service == "elevenlabs" for _,_,service in queued)
    assert app.button(key="check-sfx-generate-all-episode").disabled
    assert any("2 queued" in item.value for item in app.caption)
    for task,callback,_ in queued:
        callback()
    assert calls == [("episode","SFX","sound_a","Rain against glass",7.,True),
        ("episode","SFX","sound_b","Door closing",3.,False)]


def test_generate_all_does_not_duplicate_an_existing_sound_task(monkeypatch):
    import streamlit as st
    calls = []
    monkeypatch.setattr("story_channel.storyboard_audio_ui.start",lambda *args,**kwargs:calls.append(args))
    def display():
        import streamlit as st
        from concurrent.futures import Future
        from story_channel.storyboard_audio_ui import generate_all_sfx_button
        if "workspace-task-queue" not in st.session_state:
            st.session_state["workspace-task-queue"] = [{"future":Future(),"phase":"queued",
                "ui_owned_asset_project":"episode","resource":"storyboard-audio:SFX:sound_a"}]
        generate_all_sfx_button(None,"episode",[{"Category":"SFX","ID":"sound_a","Asset":"Rain"}])
    app = AppTest.from_function(display,default_timeout=10).run()
    app.button(key="check-sfx-generate-all-episode").click().run()
    assert not app.exception
    assert not calls


def test_bulk_button_is_disabled_when_only_existing_assets_remain():
    def display():
        from story_channel.storyboard_audio_ui import generate_all_sfx_button
        generate_all_sfx_button(None,"episode",[
            {"Category":"SFX","ID":"existing","Asset":"Rain","FileExists":True,"Ready":False,"Approved":False}])
    app = AppTest.from_function(display).run()
    assert not app.exception
    button = app.button(key="check-sfx-generate-all-episode")
    assert button.label == "Generate all missing assets (ElevenLabs)" and button.disabled


def test_missing_filter_uses_current_files_instead_of_stale_checklist(monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    from story_channel.storyboard_audio_ui import missing_sfx
    monkeypatch.setattr("story_channel.audio_assets.binding",lambda manager,pid,kind,identity:{"exists":identity == "uploaded"})
    monkeypatch.setattr("story_channel.storyboard_asset_check.intact",lambda path,item:item["exists"])
    manager = SimpleNamespace(project_path=lambda pid:Path("unused"))
    rows = [{"Category":"SFX","ID":"uploaded","Ready":False},
        {"Category":"SFX","ID":"deleted","Ready":True}]
    assert [row["ID"] for row in missing_sfx(manager,"episode",rows)] == ["deleted"]
