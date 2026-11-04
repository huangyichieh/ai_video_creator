import json
from pathlib import Path
from threading import Event

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.location_layout import LocationLayout, LayoutObject, generate_location_layout
from test_studio import world


def layout():
    return LocationLayout(floor_plan="Fixed center; north windows, west door.",
                          objects=[LayoutObject(name="desk", position="east wall, 2m from center")])


@pytest.mark.parametrize("invalid", [False, True])
def test_layout_generation_tracks_cost_and_returns_draft_without_changing_saved_settings(world, invalid):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    location = BackgroundLibrary(engine).add("Office", "Small office with windows and a desk", reference_type="KEY")
    class Provider:
        last_usage = {"input_tokens": 120, "output_tokens": 80}
        def generate(self, prompt, schema, config):
            assert config.provider == "openai"
            assert schema is LocationLayout
            assert "Small office with windows and a desk" in prompt
            assert "four directional panorama masters" in prompt
            assert "front_yaw" in prompt and "front_definition" in prompt
            assert "northSouthRange" in prompt and "ONE object ID" in prompt
            return LocationLayout(floor_plan="", objects=[]) if invalid else layout()
    if invalid:
        with pytest.raises(ValueError, match="needs a floor plan"):
            generate_location_layout(engine, location.location_id, "KEY", provider=Provider())
    else:
        result = generate_location_layout(engine, location.location_id, "KEY_360", provider=Provider())
        assert json.loads(result.object_layout()["desk"])["position"] == "east wall, 2m from center"
        assert "frontYaw" in json.loads(result.object_layout()["desk"])
    spec = engine.load().location_reference_sets[location.location_id]
    assert spec["floor_plan"] == "" and spec["object_layout"] == {}
    records = json.loads((engine.path / "usage.json").read_text(encoding="utf-8"))
    record = records[-1]
    assert record["step"] == "modify:location_layout"
    assert record["outcome"] == ("failed" if invalid else "succeeded")
    assert record["inputTokens"] == 120 and record["outputTokens"] == 80


def test_new_office_draft_recovers_empty_objects_with_feedback_and_records_both_attempts(world):
    from test_shared_room import room_plan
    from story_channel.error_reports import reports
    from story_channel.room_geometry import scene_from_spec
    from story_channel.floor_plan_guides import source_signature
    world.generate_characters()
    engine = AssetPipeline(world.manager,world.project_id,demo=True)
    lid = BackgroundLibrary(engine).add("Office","A modern office for everyday work",reference_type="KEY_360",generation_method="shared_3d").location_id
    class Provider:
        last_usage = {}
        calls = 0
        def generate(self,prompt,schema,config):
            self.calls += 1
            self.last_usage = {"input_tokens":100*self.calls,"output_tokens":50}
            assert prompt.startswith("Create a COMPLETE new draft")
            assert "Empty fields and empty lists" in prompt
            assert "editable design proposals" in prompt
            if self.calls == 1:
                empty = room_plan().model_copy(update={"objects":[]})
                return LocationLayout(floor_plan="A detailed office room without registered furniture",objects=[],camera_plan=empty)
            assert "objects was empty" in prompt and "previous_output" in prompt
            return LocationLayout(floor_plan="Office with north and south cabinets",objects=[
                LayoutObject(name="north cabinet",position="north"),LayoutObject(name="south cabinet",position="south")],camera_plan=room_plan())
    provider = Provider()
    result = generate_location_layout(engine,lid,"KEY_360",provider=provider,render_guides=False)
    assert provider.calls == 2 and len(result.objects) == 2
    scene_from_spec({"floor_plan":result.floor_plan,"object_layout":result.object_layout(),"camera_plan":result.camera_plan.model_dump(),
                     "camera_plan_signature":source_signature(result.floor_plan,result.object_layout())})
    records = json.loads((engine.path/"usage.json").read_text(encoding="utf-8"))
    assert [row["outcome"] for row in records[-2:]] == ["failed","succeeded"]
    assert [row["inputTokens"] for row in records[-2:]] == [100,200]
    assert [row["retryCount"] for row in records[-2:]] == [0,1]
    assert reports(engine.path,f"assets:backgrounds:{lid}:layout") == []
    assert engine.load().location_reference_sets[lid]["floor_plan"] == ""


def test_empty_layout_retry_is_bounded_and_does_not_replace_saved_reference(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager,world.project_id,demo=True)
    engine.settings.llm_max_retries = 1
    lid = BackgroundLibrary(engine).add("Office","An office with a desk",reference_type="KEY_360").location_id
    class Provider:
        calls = 0
        last_usage = {"input_tokens":10,"output_tokens":10}
        def generate(self,*args):
            self.calls += 1
            return LocationLayout(floor_plan="Room",objects=[])
    provider = Provider()
    with pytest.raises(ValueError,match="objects was empty"):
        generate_location_layout(engine,lid,"KEY_360",provider=provider,render_guides=False)
    assert provider.calls == 2
    assert engine.load().location_reference_sets[lid]["object_layout"] == {}
    records = json.loads((engine.path/"usage.json").read_text(encoding="utf-8"))
    assert all(row["outcome"] == "failed" for row in records[-2:])


@pytest.mark.parametrize("action",["generate-layout","check-reference","generate-plan-image"])
def test_layout_button_shows_generating_and_populates_editable_fields(world, monkeypatch,action):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    location = BackgroundLibrary(engine).add("Office", "Office with a desk", reference_type="KEY")
    release = Event()
    def generate(*args, **kwargs):
        assert kwargs.get("update","both") == {"generate-layout":"both","check-reference":"check","generate-plan-image":"guide"}[action]
        assert release.wait(30)
        result = layout()
        if action == "generate-plan-image":
            from story_channel.floor_plan_guides import CameraPlan
            from test_floor_plan_guides import plan
            data = plan().model_dump()
            data["objects"] = [{**data["objects"][0],"source_name":"desk","object_id":"DESK_01"}]
            result.camera_plan = CameraPlan.model_validate(data)
        return result
    monkeypatch.setattr("story_channel.location_layout.generate_location_layout", generate)
    for key, value in {"STORY_DATA_ROOT": str(world.manager.root), "STORY_LLM_PROVIDER": "demo",
                       "STORY_ASSET_PROVIDER": "demo", "OPENAI_ADMIN_KEY": ""}.items():
        monkeypatch.setenv(key, value)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Background"
    app.run()
    prefix = "location-reference-set-" + world.project_id + "-" + location.location_id
    app.text_area(key=prefix+"-floor").input("North windows; west door").run()
    app.text_area(key=prefix+"-layout").input('{"desk":"east wall"}').run()
    assert not any("Update object layout" in button.label or "Update floor plan" in button.label for button in app.button)
    assert app.button(key=f"generate-location-pair-{location.location_id}-masters").disabled
    try:
        app.button(key=prefix + "-"+action).click().run()
        assert not app.exception
        assert app.button(key=prefix + "-"+action).label == "Generating…"
        assert app.button(key=prefix + "-"+action).disabled
    finally:
        release.set()
    app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception
    assert app.text_area(key=prefix + "-floor").value == layout().floor_plan
    assert json.loads(app.text_area(key=prefix + "-layout").value) == layout().object_layout()
    assert not app.text_area(key=prefix + "-floor").disabled
    assert engine.load().location_reference_sets[location.location_id]["floor_plan"] == layout().floor_plan
    assert not any(button.label == "Save reference settings" for button in app.button)
