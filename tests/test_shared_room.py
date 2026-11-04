import json

import numpy as np
from PIL import Image
import pytest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.background_panorama import project_view
from story_channel.floor_plan_guides import CameraPlan
from story_channel.room_geometry import scene_from_spec,render_panorama
from story_channel.shared_room import current,perspective_keys
from test_studio import world
from test_scene_production import episode


def room_plan():
    def box(x,y):
        return [{"x":x-.7,"y":y-.4},{"x":x+.7,"y":y-.4},{"x":x+.7,"y":y+.4},{"x":x-.7,"y":y+.4}]
    return CameraPlan(boundary=[{"x":-4,"y":-4},{"x":4,"y":-4},{"x":4,"y":4},{"x":-4,"y":4}],
        camera={"x":0,"y":0},objects=[
            {"source_name":"north cabinet","object_id":"CABINET_01","footprint":box(0,2.5),"height_m":2,"material_color":"#ff0000"},
            {"source_name":"south cabinet","object_id":"CABINET_02","footprint":box(0,-2.5),"height_m":2,"material_color":"#0000ff"}])


def make_room(world):
    world.generate_characters()
    class Forbidden:
        last_usage = {}
        def generate(self,*args):
            pytest.fail("Shared 3D backgrounds must not use an image generation API")
    engine = AssetPipeline(world.manager,world.project_id,demo=True,image_provider=Forbidden())
    # Exercise retained shared-room compatibility after the default-workflow migration.
    state = engine.load()
    state.location_workflow_version = 1
    engine._save(state)
    from story_channel.media_providers import DemoImageProvider
    engine.panorama_image_provider = DemoImageProvider()
    library = BackgroundLibrary(engine)
    lid = library.add("Room","Two fixed cabinets",reference_type="KEY_360",generation_method="shared_3d").location_id
    library.configure_references(lid,"KEY_360","Square room, fixed center",{"north cabinet":"north","south cabinet":"south"},
                                 camera_plan=room_plan().model_dump())
    return engine,library,lid


def approve_pack(engine,library,lid):
    state = engine.load()
    digest = library.digest(lid,state,engine.tasks(state))
    return library.approve(lid,review_choices={lid:{"signature":digest,"canonicalGeometryConfirmed":True}})


def test_one_shared_render_has_true_spherical_views_and_pitch(tmp_path):
    from story_channel.floor_plan_guides import source_signature
    objects = {"north cabinet":"north","south cabinet":"south"}
    spec = {"floor_plan":"square","object_layout":objects,"camera_plan":room_plan().model_dump(),
            "camera_plan_signature":source_signature("square",objects)}
    scene = scene_from_spec(spec)
    panorama = tmp_path/"panorama.png"
    pixels = render_panorama(scene,panorama,width=256)
    assert pixels.shape == (128,256,3)
    assert np.max(abs(pixels[:,0].astype(int)-pixels[:,-1].astype(int))) <= 1
    output = []
    for yaw,pitch in ((0,0),(180,0),(360,0),(0,80)):
        path = tmp_path/f"{yaw}-{pitch}.png"
        project_view(panorama,path,yaw=yaw,pitch=pitch,fov=70,size=(64,64))
        output.append(np.asarray(Image.open(path)))
    assert output[0][32,32,0] > 100 and output[0][32,32,2] == 0
    assert output[1][32,32,2] > 100 and output[1][32,32,0] == 0
    assert np.max(abs(output[0].astype(int)-output[2].astype(int))) <= 1
    assert min(output[3][32,32]) > 150
    with pytest.raises(InterruptedError):
        render_panorama(scene,tmp_path/"cancelled.png",width=64,stop_requested=lambda:True)
    assert not (tmp_path/"cancelled.png").exists()


def test_pipeline_local_approval_on_demand_views_and_layout_invalidation(world):
    engine,library,lid = make_room(world)
    state = engine.generate("backgrounds",subject=lid+"__room_panorama")
    record = current(engine,state,lid)
    assert record and record.asset.provider == "local"
    from story_channel.style_check import candidates
    assert not any(entry["key"] == "backgrounds:"+lid for entry in candidates(engine,state))
    assert len([t for t in engine.tasks(state) if t.subject.startswith(lid)]) == 17
    assert perspective_keys(engine,state,lid,37,42) == []
    engine.generate("backgrounds",subject=lid)
    state = approve_pack(engine,library,lid)
    assert library.approved(lid,state)
    with pytest.raises(ValueError,match="deterministic"):
        engine.modify_with_openai("backgrounds",lid+"__room_panorama","Move the cabinet")
    keys = perspective_keys(engine,state,lid,37,42,pitch=12,fov=55)
    assert len(keys) == 2
    first = state.records[keys[0]]
    metadata = first.asset.generation_params["roomProjection"]
    assert metadata["yaw"] == 37 and metadata["pitch"] == 12 and metadata["fov"] == 55
    stamp = engine.local_path(first.asset.file_path).stat().st_mtime_ns
    assert keys == perspective_keys(engine,state,lid,37,42,pitch=12,fov=55)
    assert engine.local_path(first.asset.file_path).stat().st_mtime_ns == stamp
    assert keys != perspective_keys(engine,state,lid,37,42,pitch=12,fov=80)
    full_turn = perspective_keys(engine,state,lid,0,360)
    assert len(full_turn) == len(set(full_turn)) == 2
    scene_file = engine.local_path(record.asset.generation_params["sharedRoom"]["sceneFile"])
    scene = json.loads(scene_file.read_text(encoding="utf-8"))
    assert len(scene["objects"]) == 2 and scene["camera"] == [0,0,1.6]
    scene_file.write_text("{}",encoding="utf-8")
    assert current(engine,engine.inspect(),lid) is None
    assert not library.approved(lid,engine.load())


def test_episode_snapshots_room_scene_and_generates_actual_shot_references(episode):
    from story_channel.studio import Episodes
    from story_channel.media_utils import file_hash
    world,child,pipeline = episode
    parent = AssetPipeline(world.manager,world.project_id,demo=True)
    lid = pipeline.artifact("storyboard").scenes[0].location_id
    library = BackgroundLibrary(parent)
    objects = {"north cabinet":"north","south cabinet":"south"}
    library.configure_references(lid,"KEY_360","Square room; fixed center",objects,
                                 camera_plan=room_plan().model_dump(),generation_method="shared_3d")
    parent.generate("backgrounds",subject=lid)
    approve_pack(parent,library,lid)
    episodes = Episodes(world.manager,world.project_id)
    episodes.refresh_assets(child.project_id)
    engine = AssetPipeline(world.manager,child.project_id,demo=True)
    state = engine.generate("scene_images")
    source = current(engine,state,lid)
    assert source
    job = next(task for task in engine.tasks(state) if task.stage == "scene_images" and task.subject == pipeline.artifact("storyboard").scenes[0].scene_id)
    keys = [ref for ref in job.refs if "__shot_" in ref]
    assert keys and all(state.records[key].asset.generation_params["roomProjection"] for key in keys)
    assert engine.inspect().records[job.key].status == "succeeded"
    metadata_file = engine.local_path(source.asset.generation_params["sharedRoom"]["sceneFile"])
    assert file_hash(metadata_file) == source.asset.generation_params["sharedRoom"]["sceneHash"]
    assert world.manager.load(child.project_id).shared_assets_hash
    previous_id = source.asset.asset_id
    changed = room_plan().model_dump()
    changed["floor_color"] = "#551111"
    library.configure_references(lid,"KEY_360","Square room; fixed center",objects,camera_plan=changed)
    parent.generate("backgrounds",subject=lid,force=True)
    episodes.import_assets(child.project_id)
    assert current(engine,engine.inspect(),lid).asset.asset_id == previous_id
    metadata_file.unlink()
    episodes.import_assets(child.project_id)
    assert current(engine,engine.inspect(),lid).asset.asset_id == previous_id


def test_camera_cannot_be_inside_a_solid_object():
    from story_channel.floor_plan_guides import source_signature
    plan = room_plan().model_dump()
    plan["camera"] = {"x":0,"y":2.5}
    spec = {"floor_plan":"room","object_layout":{"north cabinet":"north","south cabinet":"south"},
            "camera_plan":plan,"camera_plan_signature":source_signature("room",{"north cabinet":"north","south cabinet":"south"})}
    with pytest.raises(ValueError,match="outside"):
        scene_from_spec(spec)


def test_simple_type_retains_three_image_workflow(world):
    engine,library,lid = make_room(world)
    state = library.configure_references(lid,"ONE_TIME","Square room",{"north cabinet":"north","south cabinet":"south"})
    assert state.location_reference_sets[lid]["generation_method"] == "dual_panorama"
    assert {task.subject for task in engine.tasks(state) if task.subject.startswith(lid)} == {
        lid+"__master_wide",lid+"__reverse",lid+"__focal"}


def test_shared_room_ui_renders_with_loading_and_then_allows_approval(world,monkeypatch):
    from pathlib import Path
    from threading import Event
    from streamlit.testing.v1 import AppTest
    engine,library,lid = make_room(world)
    release = Event()
    real_render = render_panorama
    def delayed(scene,path,**kwargs):
        assert release.wait(30)
        return real_render(scene,path,width=128)
    monkeypatch.setattr("story_channel.shared_room.render_panorama",delayed)
    for key,value in {"STORY_DATA_ROOT":str(world.manager.root),"STORY_LLM_PROVIDER":"demo",
                      "STORY_ASSET_PROVIDER":"demo","OPENAI_ADMIN_KEY":""}.items():
        monkeypatch.setenv(key,value)
    app = AppTest.from_file(str(Path(__file__).parents[1]/"app.py"),default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Background"
    app.run()
    key = "render-shared-room-"+lid
    assert not app.button(key=key).disabled
    try:
        app.button(key=key).click().run()
        assert not app.exception
        assert app.button(key=key).label == "Generating…" and app.button(key=key).disabled
    finally:
        release.set()
    app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception
    assert app.button(key="approve-shared-room-"+lid).disabled
    for button in ("render-room-canonical-", "enhance-room-canonical-"):
        app.button(key=button+lid).click().run()
        if "workspace-task" in app.session_state:
            app.session_state["workspace-task"]["future"].result(timeout=30)
        app.run()
        assert not app.exception
    checkbox = next(c for c in app.checkbox if c.label.startswith("All eight enhanced"))
    checkbox.check().run()
    assert not app.button(key="approve-shared-room-"+lid).disabled
    app.button(key="approve-shared-room-"+lid).click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception
    assert library.approved(lid,engine.load())
    app.button(key="project-room-preview-"+lid).click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception
    result = app.session_state["room-preview-result-"+lid]
    assert result["yaw"] == 0 and result["pitch"] == 0 and result["fov"] == 70
    assert engine.local_path(result["file"]).is_file()
