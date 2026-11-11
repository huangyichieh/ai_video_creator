from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from story_channel.files import input_hash
from story_channel.scene_production import SceneProduction
from test_scene_video_edits import video
from test_animated_scene_cards_ui import prepare
from test_scene_production import episode
from test_studio import world


def display(manager,pid):
    import streamlit as st
    from story_channel.dialog_ui import show_pending_dialog
    from story_channel.animated_scene_cards_ui import render
    st.session_state["generation-context-project"]=pid
    show_pending_dialog()
    render(manager,pid)


def test_video_preview_does_not_select_and_apply_updates_card(video):
    service,sid,options=video
    first=service.visuals()[sid]
    second=service.generate_visual(sid,options,force=True)
    target=f"animated-scene-card-{service.project_id}-{sid}"
    app=AppTest.from_function(display,args=(service.manager,service.project_id),default_timeout=30)
    app.session_state[target+"-revisions"]=True
    app.run()
    assert not app.exception
    from story_channel.revision_labels import generation_date
    assert any(generation_date(first) in label for label in app.selectbox(key=target+"-revision-choice").options)
    app.selectbox(key=target+"-revision-choice").select(first["asset"]["assetId"]).run()
    assert not app.exception
    assert service.visuals()[sid]["asset"]["assetId"]==second["asset"]["assetId"]
    app.button(key=target+"-apply-revision").click().run()
    app.session_state["workspace-task"]["future"].result(timeout=5)
    app.run()
    assert not app.exception
    assert service.visuals()[sid]["asset"]["assetId"]==first["asset"]["assetId"]
    assert app.button(key=target+"-apply-revision").disabled
    assert any("revision 1" in p.value for p in app.caption)


def test_modify_dialog_selection_prompt_confirm_and_cancel(video,monkeypatch):
    service,sid,options=video
    target=f"animated-scene-card-{service.project_id}-{sid}"
    rectangle={"left":.2,"top":.2,"right":.7,"bottom":.8}
    received=[]
    def picker(**kwargs):
        import streamlit as st
        received.append(kwargs["data"])
        def drag():
            st.session_state[kwargs["key"]]={"range":{"start":.25,"end":1.5,"time":.75}}
            kwargs["on_range_change"]()
        st.button("Simulate combined timeline drag",key="simulate-combined-drag",on_click=drag)
        return SimpleNamespace(selection={"rectangle":rectangle,"time":0})
    monkeypatch.setattr("story_channel.scene_video_modify_ui.picker",picker)
    calls=[]
    from threading import Event
    release=Event()
    def modify(self,*args,**kwargs):
        calls.append((args,kwargs))
        assert release.wait(20),"Test must release the edit task"
        return self.visuals()[sid]
    monkeypatch.setattr(SceneProduction,"modify_region",modify)
    app=AppTest.from_function(display,args=(service.manager,service.project_id),default_timeout=30).run()
    app.button(key=target+"-modify-region").click().run()
    assert not app.exception and app.get("dialog")
    assert app.button(key="confirm-scene-video-region").disabled
    app.button(key="cancel-scene-video-region").click().run()
    assert not app.exception and not calls
    assert not app.get("dialog")
    assert "scene-video-modify-target" not in app.session_state
    app.button(key=target+"-modify-region").click().run()
    next(w for w in app.text_area if w.label=="Replacement prompt").set_value("Replace with a blue cabinet").run()
    assert not app.exception
    assert not app.button(key="confirm-scene-video-region").disabled
    app.button(key="simulate-combined-drag").click().run()
    assert not app.exception
    assert next(w for w in app.text_input if w.label=="Edit start (HH:MM:SS)").value=="00:00:00.250"
    assert next(w for w in app.text_input if w.label=="Edit end (HH:MM:SS)").value=="00:00:01.500"
    assert received[-1]["start"]==.25 and received[-1]["end"]==1.5 and received[-1]["time"]==.75
    next(w for w in app.text_input if w.label=="Edit start (HH:MM:SS)").set_value("00:00:02").run()
    next(w for w in app.text_input if w.label=="Edit end (HH:MM:SS)").set_value("00:00:01").run()
    assert app.button(key="confirm-scene-video-region").disabled
    next(w for w in app.text_input if w.label=="Edit start (HH:MM:SS)").set_value("00:00:00.500").run()
    assert not app.button(key="confirm-scene-video-region").disabled
    assert received[-1]["start"]==.5 and received[-1]["end"]==1
    app.button(key="confirm-scene-video-region").click().run()
    try:
        assert not app.exception and not app.get("dialog")
        assert any("Generating..." in value.value for value in app.caption)
        assert any("Generating" in button.label for button in app.button)
        assert not app.session_state["workspace-task"]["future"].done()
    finally:
        release.set()
    app.session_state["workspace-task"]["future"].result(timeout=5)
    assert len(calls)==1 and calls[0][1]["rectangle"]==rectangle
    assert calls[0][1]["prompt"]=="Replace with a blue cabinet"
    assert calls[0][1]["start_time"]==.5 and calls[0][1]["end_time"]==1
    assert "scene-video-modify-target" not in app.session_state
