import io
import math

from PIL import Image
import pytest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.location_references import LocationReferenceResolver, camera_segments, set_keys
from test_studio import world


@pytest.mark.parametrize("kind,count", [("KEY_360", 12), ("ONE_TIME", 3)])
def test_location_generation_counts_master_dependencies_and_angle_selection(world, kind, count):
    if kind == "KEY_360":
        from test_dual_panorama import prepare
        engine, lid, provider = prepare(world)
        state = engine.generate("backgrounds", subject=lid)
        library = BackgroundLibrary(engine)
        calls = provider.calls
    else:
        world.generate_characters()
        engine = AssetPipeline(world.manager, world.project_id, demo=True)
        library = BackgroundLibrary(engine)
        lid = library.add("Office", "Reception desk beside the east window", reference_type=kind,
            floor_plan="Fixed center; north windows; west door", object_layout={"reception desk": "east wall"}).location_id
        calls = []
        original = engine.image_provider.generate
        def capture(prompt, refs, config):
            calls.append((prompt, list(refs)))
            return original(prompt, refs, config)
        engine.image_provider.generate = capture
        state = engine.generate("backgrounds", subject=lid)
    keys = set_keys(state, lid)
    assert len(keys) == count and len(calls) == (4 if kind == "KEY_360" else 3)
    assert all(state.records[key].status == "succeeded" for key in keys)
    assert len(calls[0][1]) == (2 if kind == "KEY_360" else 0)
    master = engine.local_path(state.records[keys[0]].asset.file_path)
    assert all(master in refs for _,refs in (calls[2:] if kind == "KEY_360" else calls[1:]))
    assert all("east wall" in prompt for prompt,_ in (calls[2:] if kind == "KEY_360" else calls))
    resolver = LocationReferenceResolver(engine, state)
    selected = resolver.resolve(lid, 85, 135, "reception desk")
    assert 1 <= len(selected) <= 3
    if kind == "KEY_360":
        assert keys[6] in selected  # view_090
        assert keys[7] in selected  # view_135
    else:
        assert any(key.endswith("__focal") for key in selected)
        assert set(key.rsplit("__", 1)[1] for key in keys) == {"master_wide", "reverse", "focal"}
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "green").save(output, "PNG")
    library.approve(lid)
    assert library.approved(lid, engine.load())
    library.add_detail_reference(lid, "reception desk", output.getvalue())
    state = engine.load()
    assert not library.approved(lid, state)
    selected = LocationReferenceResolver(engine, state).resolve(lid, 90, 135, "reception desk")
    assert "__detail_" in selected[0] and len(selected) <= 3
    library.approve(lid)
    assert library.approved(lid, engine.load())


def test_key_location_requires_layout_before_any_generation(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    location = BackgroundLibrary(engine).add("Office", "A temporary empty office", reference_type="KEY")
    with pytest.raises(ValueError, match="floor plan"):
        engine.generate("backgrounds", subject=location.location_id)
    assert not any(record.asset for record in engine.load().records.values())


def test_orbit_segments_preserve_direction_wrap_and_limit():
    for start, end, count in [(0,360,8), (315,405,2), (0,-180,4), (0,0,1)]:
        segments = camera_segments(start,end)
        assert len(segments) == count
        assert segments[0][0] == start and segments[-1][1] == end
        assert all(abs(b-a) <= 45 for a,b in segments)
        assert all(segments[i][1] == segments[i+1][0] for i in range(len(segments)-1))
    with pytest.raises(ValueError):
        camera_segments(math.nan, 90)


def test_orbit_render_selects_each_segment_and_chains_cut_frames(world, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from story_channel.animation_models import AnimationPlan, AnimationState, LocationBible, ShotPlan
    from story_channel.animation_rendering import ShotRenderer
    from story_channel.asset_models import SceneAssets
    from story_channel.files import input_hash
    from story_channel.models import Scene, GeneratedAsset
    from story_channel.render_models import RenderOptions
    from test_dual_panorama import prepare
    engine, lid, _ = prepare(world)
    production = engine.generate("backgrounds", subject=lid)
    scene = Scene(scene_id="orbit", order=1, duration_sec=8, location_id=lid, camera_start_angle=0, camera_end_angle=360)
    selected = LocationReferenceResolver(engine, production).resolve(lid, 0, 360)
    pack = {key: {"file": record.asset.file_path, "sha256": record.file_hash, "assetId": record.asset.asset_id,
                  "referenceHashes": {}} for key, record in production.records.items() if record.asset}
    pack["scene_images:orbit"] = {**pack[selected[0]], "referenceHashes": {key: pack[key]["sha256"] for key in selected}}
    production.scenes["orbit"] = SceneAssets(scene_id="orbit", image=pack[selected[0]]["file"], duration=8,
                                            audio="unused.wav", srt="unused.srt", vtt="unused.vtt", timing="unused.json")
    state = AnimationState(plan=AnimationPlan(source_hash="test", voices={}, locations={lid: LocationBible(location_id=lid, description="Room")},
        performances=[], shots=[ShotPlan(shot_id="s", scene_id="orbit", order=1, action="Hold the room geometry", camera="Full orbit",
                                      camera_start_angle=0, camera_end_angle=360, chain_previous=False)]))
    original = engine.creative.artifact
    monkeypatch.setattr(engine.creative, "artifact", lambda stage: SimpleNamespace(scenes=[scene]) if stage == "storyboard" else original(stage))
    director = SimpleNamespace(manager=world.manager, project_id=world.project_id, path=engine.path, assets=engine,
        require_ready=lambda: state, load=lambda: state, references=lambda: pack, save_state=lambda _: None)
    calls = []
    class Provider:
        last_usage = {}
        def generate_clip(self, prompt, references, config):
            calls.append((references, config))
            Path(config.options["output_path"]).write_bytes(b"clip")
            return GeneratedAsset(type="video", provider="runway", model=config.model, file_path=config.options["output_path"], prompt_hash=input_hash(prompt))
    class FFmpeg:
        def validate(self, path): return 8
        def cut_frame(self, clip, frame, at):
            Image.new("RGB", (32,32), (len(calls)*20,0,0)).save(frame)
        def join_visuals(self, paths, durations, destination, options):
            assert len(paths) == 8 and sum(durations) == 8
            destination.write_bytes(b"joined")
    options = RenderOptions(provider="runway", video_model="klingO3_pro", continuity_animation=True)
    ShotRenderer(director, FFmpeg(), Provider()).render(scene, production, options, engine.path / "orbit.mp4")
    assert len(calls) == 8
    assert all(len([key for key in config.options["reference_pack"] if key.startswith("backgrounds:")]) <= 3 for _, config in calls)
    assert all(Path(references[0]).name == "cut_frame.png" for references, _ in calls[1:])
    assert len({tuple(config.options["reference_pack"]) for _, config in calls}) > 1
    assert all(abs(record.asset.generation_params["cameraEndAngle"]-record.asset.generation_params["cameraStartAngle"]) <= 45 for record in state.shots.values())


def test_new_background_form_only_needs_title_description_type_then_layout_is_saved_under_generation(world, monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    world.generate_characters()
    for key, value in {"STORY_DATA_ROOT": str(world.manager.root), "STORY_LLM_PROVIDER": "demo",
                       "STORY_ASSET_PROVIDER": "demo", "OPENAI_ADMIN_KEY": ""}.items():
        monkeypatch.setenv(key, value)
    app = AppTest.from_file(str(Path(__file__).parents[1]/"app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Background"
    app.run()
    app.text_input(key="background-new-name-" + world.project_id).set_value("Recurring office")
    app.text_area(key="background-new-description-" + world.project_id).set_value("An office with a reception desk")
    app.selectbox(key="background-new-type-" + world.project_id).set_value("KEY_360")
    assert not any(field.key in ("background-new-floor-" + world.project_id, "background-new-layout-" + world.project_id)
                   for field in app.text_area)
    next(button for button in app.button if button.label == "Add draft").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    added = next(iter(engine.load().location_reference_sets.items()))
    assert added[1]["reference_type"] == "KEY_360" and added[1]["object_layout"] == {}
    assert added[1]["generation_method"] == "dual_panorama"
    master_button = f"generate-location-pair-{added[0]}-bridges"
    assert app.button(key=master_button).disabled
    prefix = "location-reference-set-" + world.project_id + "-" + added[0]
    app.text_area(key=prefix + "-floor").set_value("Center origin; north windows, west door")
    app.text_area(key=prefix + "-layout").set_value('{"desk":"east wall"}')
    app.button(key=prefix + "-save").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception
    assert engine.load().location_reference_sets[added[0]]["object_layout"] == {"desk":"east wall"}
    assert app.button(key=master_button).disabled
    from story_channel.location_layout import LocationLayout,LayoutObject
    from story_channel.floor_plan_guides import CameraPlan
    from test_floor_plan_guides import plan
    def spatial_draft(*args,**kwargs):
        data = plan().model_dump()
        data["objects"] = [{**data["objects"][2],"source_name":"desk","object_id":"DESK_01"}]
        return LocationLayout(floor_plan=args[3],objects=[LayoutObject(name=k,position=v) for k,v in args[4].items()],
                              camera_plan=CameraPlan.model_validate(data),references_match=True)
    monkeypatch.setattr("story_channel.location_layout.generate_location_layout",spatial_draft)
    app.button(key=prefix+"-generate-plan-image").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception
    assert not app.button(key=master_button).disabled
    reference_box = next(box for box in app.expander if box.label == "Floor plan, object layout & reference settings"
                         and any(field.key == prefix+"-type" for field in box.selectbox))
    assert len(reference_box.image) == 0
    preview_prefix = f"floor-guide-preview-{world.project_id}-{added[0]}-"
    assert len([button for button in reference_box.button if button.key and button.key.startswith(preview_prefix)]) == 5
    assert not any(box.label == "Floor plan & camera guide" for box in app.expander)
    app.button(key=preview_prefix+"layout").click().run()
    assert not app.exception and app.get("dialog")
    assert len(app.get("dialog")[0].image) == 1
    assert app.session_state["floor-plan-preview-target"]["title"] == "Visible floor plan \u00b7 complete object layout"
    assert app.button(key="previous-floor-plan-preview").proto.shortcut == "left"
    assert app.button(key="next-floor-plan-preview").proto.shortcut == "right"
    app.button(key="next-floor-plan-preview").click().run()
    assert not app.exception
    assert app.session_state["floor-plan-preview-target"]["title"] == "East Bridge camera guide"
    app.button(key="next-floor-plan-preview").click().run()
    assert app.session_state["floor-plan-preview-target"]["title"] == "West Bridge camera guide"
    app.button(key="next-floor-plan-preview").click().run()
    assert app.session_state["floor-plan-preview-target"]["title"] == "North-facing camera guide"
    assert any(item.value == "**North-facing camera guide**" for item in app.get("dialog")[0].markdown)
    app.button(key="next-floor-plan-preview").click().run()
    assert app.session_state["floor-plan-preview-target"]["title"] == "South-facing camera guide"
    app.button(key="next-floor-plan-preview").click().run()
    assert app.session_state["floor-plan-preview-target"]["file"].endswith("floor_plan.png")
    app.button(key="previous-floor-plan-preview").click().run()
    assert app.session_state["floor-plan-preview-target"]["title"] == "South-facing camera guide"
    app.button(key="close-floor-plan-preview").click().run()
    assert not app.exception and not app.get("dialog")
    app.text_area(key=prefix+"-floor").input("Changed room dimensions; north windows").run()
    assert app.button(key=master_button).disabled
