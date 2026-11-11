from pathlib import Path
from threading import Event
from concurrent.futures import Future
from uuid import uuid4

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel import scene_keyframes as frames
from story_channel.files import input_hash
from story_channel.models import GeneratedAsset
from story_channel.costs import CostTracker
from test_scene_production import episode
from test_studio import world


@pytest.fixture
def scene(episode):
    _,project,pipeline=episode
    engine=AssetPipeline(pipeline.manager,project.project_id)
    sid=pipeline.artifact("storyboard").scenes[0].scene_id
    engine.generate("scene_images",subject=sid)
    return engine,sid


class ImageProvider:
    last_usage={}
    def __init__(self):
        self.calls=[]
    def generate(self,prompt,references,config):
        self.calls.append((prompt,list(references),config))
        output=Path(config.options["output_path"])
        with Image.open(references[0]) as image:
            image.save(output)
        self.last_usage={"image_count":1}
        return GeneratedAsset(type="image",provider=config.provider,model=config.model,file_path=str(output),prompt_hash=input_hash(prompt))


def test_generated_keyframes_use_scene_reference_assets_and_confirm_without_replacing_primary(scene):
    engine,sid=scene
    state=engine.load().model_dump_json()
    token=uuid4().hex
    draft=frames.start(engine,sid,token)
    primary=draft["frames"][0]
    identity=uuid4().hex
    provider=ImageProvider()
    generated=frames.generate(engine,token,identity,primary["id"],"The same character raises one hand",provider=provider)
    assert generated["status"]=="ready" and generated["referenceId"]==primary["id"]
    assert not (engine.path/"scene_image_keyframes.json").exists()
    assert engine.load().model_dump_json()==state
    assert len(provider.calls)==1
    prompt,refs,config=provider.calls[0]
    assert "raises one hand" in prompt
    assert refs[0]==engine.local_path(primary["asset"]["filePath"])
    assert prompt.startswith("EDIT IMAGE 1")
    assert "CURRENT STORYBOARD SCENE: this is authoritative" not in prompt
    assert config.options["scene_reference_roles"][0]["role"]=="scene-edit-base"
    assert generated["asset"]["generationParams"]["keyframeEdit"]["baseInputIndex"]==1
    assert any(role["role"]=="character" for role in config.options["scene_reference_roles"])
    assert any(role["role"]=="background" for role in config.options["scene_reference_roles"])
    result=frames.confirm(engine,token,{primary["id"]:0,identity:1.5})
    assert [item["timeSec"] for item in result["frames"]]==[0,1.5]
    assert engine.load().model_dump_json()==state
    reopened=frames.start(engine,sid,uuid4().hex)
    assert [item["id"] for item in reopened["frames"]]==[primary["id"],identity]
    usage=[row for row in CostTracker(engine.manager,engine.project_id).records() if row.step=="assets:scene_keyframes:"+identity]
    assert usage and usage[-1].image_count==1


def test_keyframe_times_validate_and_other_sessions_cannot_overwrite_saved_set(scene):
    engine,sid=scene
    first=frames.start(engine,sid,uuid4().hex)
    second=frames.start(engine,sid,uuid4().hex)
    identity=first["frames"][0]["id"]
    for value in (-1,999,float("nan"),"bad",True):
        with pytest.raises(ValueError):
            frames.confirm(engine,first["id"],{identity:value})
    frames.confirm(engine,first["id"],{identity:0})
    with pytest.raises(ValueError,match="Another session"):
        frames.confirm(engine,second["id"],{identity:1})
    with pytest.raises(ValueError,match="different keyframe"):
        frames.validate_times([{"id":"a"},{"id":"b"}],{"a":1,"b":1},3)


def test_failed_generation_reports_cost_and_changed_inputs_reject_before_paid_request(scene):
    engine,sid=scene
    draft=frames.start(engine,sid,uuid4().hex)
    primary=draft["frames"][0]
    identity=uuid4().hex
    class Failing:
        last_usage={}
        calls=0
        def generate(self,*args):
            self.calls+=1
            self.last_usage={"image_count":1}
            raise RuntimeError("Injected keyframe generation failure")
    provider=Failing()
    with pytest.raises(RuntimeError,match="Injected keyframe"):
        frames.generate(engine,draft["id"],identity,primary["id"],"Wave",provider=provider)
    failed=next(item for item in frames.load(engine,draft["id"])["frames"] if item["id"]==identity)
    assert failed["status"]=="failed" and "Injected keyframe" in failed["error"]
    assert any(engine.path.glob("error_reports/**/*.json"))
    usage=[item for item in CostTracker(engine.manager,engine.project_id).records() if item.step=="assets:scene_keyframes:"+identity]
    assert usage[-1].outcome=="failed" and usage[-1].image_count==1
    state=engine.load()
    state.style_prompts["scene_images:"+sid]="Updated scene style guidance"
    engine._save(state)
    with pytest.raises(ValueError,match="changed"):
        frames.generate(engine,draft["id"],uuid4().hex,primary["id"],"Wave",provider=provider)
    assert provider.calls==1


def test_selected_additional_image_is_edit_base_and_old_storyboard_action_is_not_reused(scene):
    engine,sid=scene
    draft=frames.start(engine,sid,uuid4().hex)
    primary=draft["frames"][0]
    provider=ImageProvider()
    secondary_id=uuid4().hex
    secondary=frames.generate(engine,draft["id"],secondary_id,primary["id"],"Raise the left hand",provider=provider)
    final=frames.generate(engine,draft["id"],uuid4().hex,secondary_id,"Close only the raised hand",provider=provider,preview=True)
    prompt,refs,config=provider.calls[-1]
    assert refs[0]==engine.local_path(secondary["asset"]["filePath"])
    assert refs[0]!=engine.local_path(primary["asset"]["filePath"])
    assert "Close only the raised hand" in prompt
    assert "CURRENT STORYBOARD SCENE: this is authoritative" not in prompt
    assert "Depict the action's key moment" not in prompt
    assert all(f"Image {index}={role['role']}:{role['id']}" in prompt
        for index,role in enumerate(config.options["scene_reference_roles"],1))
    assert final["asset"]["generationParams"]["keyframeEdit"]["referenceId"]==secondary_id
    assert final["asset"]["generationParams"]["keyframeEdit"]["referenceFileHash"]==secondary["fileHash"]


def display(engine):
    import streamlit as st
    from story_channel.dialog_ui import show_pending_dialog
    from story_channel.scene_image_cards_ui import render
    st.session_state["generation-context-project"]=engine.project_id
    show_pending_dialog()
    render(engine)


def test_gallery_add_reference_prompt_loading_confirm_times_and_cancel(scene,monkeypatch):
    engine,sid=scene
    original=frames.generate
    release=Event()
    provider=ImageProvider()
    def generate(*args,**kwargs):
        assert release.wait(20)
        return original(*args,**kwargs,provider=provider)
    monkeypatch.setattr(frames,"generate",generate)
    app=AppTest.from_function(display,args=(engine,),default_timeout=30).run()
    button=f"scene-image-card-{engine.project_id}-{sid}-multiple-images"
    app.button(key=button).click().run()
    assert not app.exception and app.get("dialog")
    target=app.session_state["scene-keyframes-target"]
    key="scene-keyframes-"+target["token"]
    assert not any(button.key==key+"-plus" for button in app.button)
    assert app.button(key=key+"-add").disabled
    assert app.selectbox(key=key+"-reference").options==["Image 1"]
    app.text_area(key=key+"-prompt").set_value("The character raises one hand").run()
    app.button(key=key+"-generate-preview").click().run()
    try:
        assert not app.exception
        assert app.button(key=key+"-save").disabled
        assert app.button(key=key+"-add").disabled
        assert len(app.number_input)==1
        assert len(app.session_state["scene-keyframes-target"]["requests"])==1
        assert not app.session_state["workspace-task"]["future"].done()
    finally:
        release.set()
    app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception
    assert len(app.number_input)==1  # A generated preview is not automatically added.
    assert not app.button(key=key+"-add").disabled
    assert app.button(key=key+"-generate-preview").label=="Regenerate image"
    first_preview=app.session_state["scene-keyframes-target"]["preview_id"]
    app.text_area(key=key+"-prompt").set_value("The character lowers one hand").run()
    assert app.button(key=key+"-add").disabled
    app.button(key=key+"-generate-preview").click().run()
    app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception and len(app.number_input)==1
    new_id=app.session_state["scene-keyframes-target"]["preview_id"]
    assert new_id!=first_preview
    app.button(key=key+"-add").click().run()
    assert not app.exception and len(app.number_input)==2
    assert app.text_area(key=key+"-prompt").value==""
    app.number_input(key=key+"-time-"+new_id).set_value(1.).run()
    assert not app.button(key=key+"-save").disabled
    app.button(key=key+"-save").click().run()
    assert not app.exception and "scene-keyframes-target" in app.session_state
    assert any("saved" in success.value for success in app.success)
    app.run()  # Named-fragment deltas omit the containing dialog in AppTest.
    assert app.get("dialog")
    saved=frames.read(engine.path/"scene_image_keyframes.json",{})[sid]
    assert len(saved["frames"])==2 and saved["frames"][1]["timeSec"]==1
    # Saving refreshes the concurrency baseline and permits another save in place.
    app.number_input(key=key+"-time-"+new_id).set_value(1.5).run()
    app.button(key=key+"-save").click().run()
    assert not app.exception and "scene-keyframes-target" in app.session_state
    app.run()
    assert app.get("dialog")
    saved=frames.read(engine.path/"scene_image_keyframes.json",{})[sid]
    assert saved["frames"][1]["timeSec"]==1.5
    app.button(key=key+"-close").click().run()
    assert not app.exception and not app.get("dialog")
    app.button(key=button).click().run()
    target=app.session_state["scene-keyframes-target"]
    key="scene-keyframes-"+target["token"]
    app.button(key=key+"-remove-"+new_id).click().run()
    assert not app.exception and len(app.number_input)==1
    app.button(key=key+"-close").click().run()
    assert not app.exception and not app.get("dialog")
    assert frames.read(engine.path/"scene_image_keyframes.json",{})[sid]==saved
    app.button(key=button).click().run()
    target=app.session_state["scene-keyframes-target"]
    key="scene-keyframes-"+target["token"]
    app.button(key=key+"-remove-"+new_id).click().run()
    app.button(key=key+"-save").click().run()
    assert not app.exception and "scene-keyframes-target" in app.session_state
    assert len(frames.read(engine.path/"scene_image_keyframes.json",{})[sid]["frames"])==1


def test_previews_require_explicit_add_limit_five_and_remove_preserves_original_files(scene):
    engine,sid=scene
    draft=frames.start(engine,sid,uuid4().hex)
    primary=draft["frames"][0]
    provider=ImageProvider()
    with pytest.raises(ValueError,match="Generate an image"):
        frames.add_generated(engine,draft["id"],"missing")
    with pytest.raises(ValueError,match="at least one"):
        frames.remove_image(engine,draft["id"],primary["id"])
    previews=[]
    for _ in range(5):
        identity=uuid4().hex
        frames.generate(engine,draft["id"],identity,primary["id"],"A new moment",provider=provider,preview=True)
        previews.append(identity)
    assert len(frames.load(engine,draft["id"])["frames"])==1
    for identity in previews[:4]:
        frames.add_generated(engine,draft["id"],identity)
    assert len(frames.load(engine,draft["id"])["frames"])==5
    with pytest.raises(ValueError,match="at most 5"):
        frames.add_generated(engine,draft["id"],previews[4])
    frames.remove_image(engine,draft["id"],primary["id"])
    assert engine.local_path(primary["asset"]["filePath"]).is_file()
    frames.add_generated(engine,draft["id"],previews[4])
    assert len(frames.load(engine,draft["id"])["frames"])==5


def polling_display():
    import streamlit as st
    from story_channel.scene_keyframes_ui import watch
    st.session_state["parent-renders"]=st.session_state.get("parent-renders",0)+1
    target=st.session_state.setdefault("poll-target",{"version":0})
    def panel():
        st.session_state["panel-renders"]=st.session_state.get("panel-renders",0)+1
        baseline=target["version"]
        st.caption(f"Preview version {baseline}")
        watch(None,target,"polling-gallery",baseline)
    st.fragment(key="polling-gallery")(panel)()


def test_gallery_poll_refreshes_named_fragment_without_callback_only_rerun(monkeypatch):
    def changed_signature(engine,target):
        # Simulate a worker completing after the panel captured its baseline.
        target["version"]=1
        return target["version"]
    monkeypatch.setattr("story_channel.scene_keyframes_ui.signature",changed_signature)
    monkeypatch.setattr("story_channel.scene_image_cards_ui.polling_tick",lambda:True)
    app=AppTest.from_function(polling_display,default_timeout=15).run()
    assert not app.exception
    assert app.session_state["parent-renders"]==1
    assert app.session_state["panel-renders"]==2
    assert any(caption.value=="Preview version 1" for caption in app.caption)


def test_preview_progress_explains_queue_provider_wait_and_detects_orphan(monkeypatch):
    from datetime import datetime,timedelta,timezone
    from story_channel.scene_keyframes_ui import preview,progress_text
    target={"token":"draft","preview_id":"image","errors":{},"requests":[{
        "id":"image","submitted":True,"requestedAt":(datetime.now(timezone.utc)-timedelta(seconds=65)).isoformat()}]}
    row={"id":"image","status":"generating","phase":"requesting"}
    monkeypatch.setattr(frames,"load",lambda *args:{"candidates":[row]})
    future=Future()
    job={"keyframe_id":"image","future":future,"phase":"queued"}
    tracked=[job]
    monkeypatch.setattr("story_channel.scene_keyframes_ui.jobs",lambda token:tracked)
    assert "Queued" in progress_text(None,target)
    job["phase"]="waiting"
    assert "Waiting for an OpenAI slot" in progress_text(None,target)
    job["phase"]="running"
    assert "Waiting for OpenAI image response" in progress_text(None,target)
    assert "01:05 elapsed" in progress_text(None,target)
    job["phase"]="saving"
    assert "Saving generated image" in progress_text(None,target)
    tracked.clear()
    assert preview(None,target)["status"]=="failed"
    assert "no longer running or tracked" in preview(None,target)["error"]
    assert progress_text(None,target) is None


def queue_polling_display(blocker,calls):
    import streamlit as st
    from concurrent.futures import Future
    from story_channel.scene_keyframes_ui import watch
    if "workspace-task" not in st.session_state:
        st.session_state["workspace-task"]={"future":blocker,"project_key":"same","allow_parallel":False}
        st.session_state["workspace-task-queue"]=[{"future":Future(),"phase":"queued","callback":lambda:calls.append("image"),
            "project_key":"same","allow_parallel":False,"initial_service":"openai"}]
    watch(None,{},"unused",0)


def test_gallery_timer_dispatches_queued_image_after_blocking_task_finishes(monkeypatch):
    monkeypatch.setattr("story_channel.scene_image_cards_ui.polling_tick",lambda:True)
    monkeypatch.setattr("story_channel.scene_keyframes_ui.signature",lambda *args:0)
    blocker=Future()
    calls=[]
    app=AppTest.from_function(queue_polling_display,args=(blocker,calls)).run()
    assert not app.exception and not calls
    assert len(app.session_state["workspace-task-queue"])==1
    blocker.set_result(None)
    app.run()
    assert not app.exception
    app.session_state["workspace-parallel-tasks"][0]["future"].result(timeout=5)
    assert calls==["image"] and not app.session_state["workspace-task-queue"]


def test_confirmed_images_and_keyframe_times_show_on_scene_card_and_only_own_signature_changes(scene,monkeypatch):
    import streamlit as st
    from story_channel.scene_image_cards_ui import signature
    engine,sid=scene
    token=uuid4().hex
    draft=frames.start(engine,sid,token)
    primary=draft["frames"][0]
    identity=uuid4().hex
    extra=frames.generate(engine,token,identity,primary["id"],"A later moment",provider=ImageProvider())
    frames.confirm(engine,token,{primary["id"]:0,identity:1.5})
    shown=[]
    original=st.image
    def image(value,*args,**kwargs):
        shown.append(value)
        return original(value,*args,**kwargs)
    monkeypatch.setattr(st,"image",image)
    app=AppTest.from_function(display,args=(engine,),default_timeout=30).run()
    assert not app.exception
    assert shown==[str(engine.local_path(primary["asset"]["filePath"])),str(engine.local_path(extra["asset"]["filePath"]))]
    assert any(caption.value=="Keyframe: 0s" for caption in app.caption)
    assert any(caption.value=="Keyframe: 1.5s" for caption in app.caption)
    other=next(item.scene_id for item in engine.creative.artifact("storyboard").scenes if item.scene_id!=sid)
    before,other_before=signature(engine,sid),signature(engine,other)
    saved=frames.read(engine.path/"scene_image_keyframes.json",{})
    saved[sid]["frames"][1]["timeSec"]=2
    from story_channel.files import atomic_write
    import json
    atomic_write(engine.path/"scene_image_keyframes.json",json.dumps(saved))
    assert signature(engine,sid)!=before
    assert signature(engine,other)==other_before
