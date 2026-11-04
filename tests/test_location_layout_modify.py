import json
from threading import Event

import pytest

from story_channel.background_library import BackgroundLibrary
from story_channel.floor_plan_guides import CameraPlan,publish_guides
from story_channel.location_layout import LocationLayout,LayoutObject,generate_location_layout
from story_channel.object_orientation import with_orientation,orientation
from test_object_orientation import spec
from test_location_master_pack import master_pack
from test_studio import world


def setup(master_pack):
    engine,lid,_,_ = master_pack
    data = spec()
    BackgroundLibrary(engine).configure_references(lid,"KEY_360",data["floor_plan"],data["object_layout"],camera_plan=data["camera_plan"])
    publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    return engine,lid,data


def modified(data):
    plan = CameraPlan.model_validate(data["camera_plan"])
    plan.objects[0].front_yaw = 90
    return LocationLayout(floor_plan="Chair in northeast corner, facing east",
        objects=[LayoutObject(name="chair",position=with_orientation("northeast corner",90,"Seated person's gaze"))],camera_plan=plan)


def test_prompt_modification_changes_facing_and_returns_consistent_unsaved_draft(master_pack):
    engine,lid,data = setup(master_pack)
    before = engine.load().location_reference_sets[lid]
    class Provider:
        calls = 0
        last_usage = {"input_tokens":100,"output_tokens":60}
        def generate(self,prompt,schema,config):
            self.calls += 1
            assert "Turn the chair to face east" in prompt
            assert "do not restore the original orientation" in prompt
            return modified(data)
    provider = Provider()
    result = generate_location_layout(engine,lid,"KEY_360",data["floor_plan"],data["object_layout"],
        camera_plan=data["camera_plan"],update="modify",instructions="Turn the chair to face east",provider=provider,render_guides=False)
    assert provider.calls == 1
    assert orientation(result.object_layout()["chair"])[0] == 90
    assert result.camera_plan.objects[0].front_yaw == 90
    assert engine.load().location_reference_sets[lid] == before
    usage = json.loads((engine.path/"usage.json").read_text(encoding="utf-8"))
    assert usage[-1]["inputTokens"] == 100 and usage[-1]["outcome"] == "succeeded"
    with pytest.raises(ValueError,match="modification prompt"):
        generate_location_layout(engine,lid,"KEY_360",data["floor_plan"],data["object_layout"],update="modify",provider=provider)
    assert provider.calls == 1


def test_dialog_opens_before_request_shows_modifying_then_applies_to_draft(master_pack,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext
    timers,controls = [],[]
    original_enqueue = ScriptRunContext.enqueue
    def enqueue(context,message):
        if message.WhichOneof("type") == "auto_rerun":
            timers.append((message.auto_rerun.fragment_id,message.auto_rerun.interval))
        elif message.WhichOneof("type") == "delta" and message.delta.WhichOneof("type") == "new_element":
            if message.delta.new_element.WhichOneof("type") in ("text_area","button"):
                controls.append(message.delta.fragment_id)
        original_enqueue(context,message)
    monkeypatch.setattr(ScriptRunContext,"enqueue",enqueue)
    engine,lid,data = setup(master_pack)
    release = Event()
    calls = []
    def generate(*args,**kwargs):
        calls.append(kwargs)
        assert release.wait(20)
        return modified(data)
    monkeypatch.setattr("story_channel.location_layout.generate_location_layout",generate)
    def display(engine,lid):
        from story_channel.background_assets_ui import reference_set_settings
        from story_channel.background_library import BackgroundLibrary
        reference_set_settings(engine,BackgroundLibrary(engine),lid,engine.load())
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    prefix = f"location-reference-set-{engine.project_id}-{lid}"
    app.button(key=prefix+"-modify-prompt").click().run()
    assert not app.exception and not calls
    target = app.session_state["layout-modify-target"]
    key = "layout-modify-"+target["token"]
    app.text_area(key=key+"-prompt").set_value("Turn the chair to face east").run()
    assert not timers  # Idle dialog never schedules timed editor redraws.
    controls.clear()
    try:
        app.button(key=key+"-generate").click().run()
        assert not app.exception
        assert app.button(key=key+"-generate").label == "Modifying…"
        assert app.button(key=key+"-generate").disabled
        assert app.session_state["layout-modify-target"] == target
        assert timers and all(interval == 1 for _,interval in timers)
        assert not {identity for identity,_ in timers}.intersection(controls)
    finally:
        release.set()
    app.session_state["workspace-task"]["future"].result(timeout=10)
    timers.clear()
    app.run()
    assert not app.exception
    assert not timers  # Polling stops once the result is displayed.
    assert calls[0]["update"] == "modify" and calls[0]["instructions"] == "Turn the chair to face east"
    assert app.text_area(key=prefix+"-floor").value == data["floor_plan"]
    app.text_area(key=prefix+"-floor").set_value("Newer layout draft").run()
    app.button(key=key+"-apply").click().run()
    assert not app.exception
    assert any("layout draft changed" in item.value for item in app.error)
    assert app.text_area(key=prefix+"-floor").value == "Newer layout draft"
    app.text_area(key=prefix+"-floor").set_value(data["floor_plan"]).run()
    app.button(key=key+"-apply").click().run()
    assert not app.exception
    assert app.text_area(key=prefix+"-floor").value == modified(data).floor_plan
    values = json.loads(app.text_area(key=prefix+"-layout").value)
    assert orientation(values["chair"])[0] == 90
    assert engine.load().location_reference_sets[lid]["floor_plan"] == modified(data).floor_plan


def test_cancel_dialog_keeps_original_draft_without_request(master_pack):
    from streamlit.testing.v1 import AppTest
    engine,lid,data = setup(master_pack)
    def display(engine,lid):
        from story_channel.background_assets_ui import reference_set_settings
        from story_channel.background_library import BackgroundLibrary
        reference_set_settings(engine,BackgroundLibrary(engine),lid,engine.load())
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    prefix = f"location-reference-set-{engine.project_id}-{lid}"
    app.button(key=prefix+"-modify-prompt").click().run()
    assert not app.exception
    app.button(key="cancel-layout-modification").click().run()
    assert not app.exception
    assert app.text_area(key=prefix+"-floor").value == data["floor_plan"]
    assert engine.load().location_reference_sets[lid]["floor_plan"] == data["floor_plan"]


def test_autosave_retains_revisions_and_rejects_overwriting_newer_settings(master_pack):
    from story_channel.location_layout import save_layout_result
    from story_channel.floor_plan_revisions import signature,revisions
    engine,lid,data = setup(master_pack)
    original = engine.load().location_reference_sets[lid]
    expected = signature(original)
    result = modified(data)
    save_layout_result(engine,lid,result,expected_layout=expected)
    saved = engine.load().location_reference_sets[lid]
    assert saved["floor_plan"] == result.floor_plan
    assert orientation(saved["object_layout"]["chair"])[0] == 90
    assert {expected,signature(saved)} <= {item["id"] for item in revisions(engine,lid)}
    with pytest.raises(ValueError,match="changed in another session"):
        save_layout_result(engine,lid,LocationLayout(floor_plan="Outdated result",objects=result.objects,camera_plan=result.camera_plan),
            expected_layout=expected)
    assert engine.load().location_reference_sets[lid] == saved
