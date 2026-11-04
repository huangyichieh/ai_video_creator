from story_channel.dual_panorama import digest,view_digests,approve
from story_channel.media_utils import file_hash
from test_dual_panorama import prepare
from test_studio import world
import pytest


def test_master_task_cache_reuses_definitions_but_rechecks_changed_inputs(world):
    engine,lid,_ = prepare(world)
    engine.generate("backgrounds",subject=lid)
    state = engine.load()
    original = engine.tasks
    calls = []
    def tasks(state):
        calls.append(1)
        return original(state)
    engine.tasks = tasks
    engine._panorama_master_tasks = None
    for _ in range(10):
        assert digest(engine,state,lid,"front_panorama")
        assert digest(engine,state,lid,"back_panorama")
    assert len(calls) == 1
    assert all(view_digests(engine,state,lid).values())
    assert len(calls) == 2
    changed = state.model_copy(deep=True)
    changed.location_reference_sets[lid]["floor_plan"] += "; north wall has new window placement"
    assert digest(engine,changed,lid,"front_panorama") is None
    assert len(calls) == 3
    assert digest(engine,state,lid,"front_panorama")
    changed = state.model_copy(deep=True)
    changed.project_style_rules = "A different art direction"
    assert digest(engine,changed,lid,"front_panorama") is None
    record = state.records[f"backgrounds:{lid}__front_panorama"]
    path = engine.local_path(record.asset.file_path)
    path.write_bytes(b"changed image")
    assert file_hash(path) != record.file_hash
    assert digest(engine,state,lid,"front_panorama") is None
    with pytest.raises(ValueError,match="current panorama"):
        approve(engine,lid,"front_panorama",record.asset.asset_id)


def test_shared_yaw_validation_still_rejects_missing_published_images(world):
    engine,lid,_ = prepare(world)
    engine.generate("backgrounds",subject=lid)
    from story_channel.dual_panorama import root
    (root(engine,lid)/"yaw/yaw_045.png").unlink()
    values = view_digests(engine,engine.load(),lid)
    assert values[45] is None
    assert all(value for angle,value in values.items() if angle != 45)


def test_checkbox_enables_approval_and_pending_work_shows_loading(world,monkeypatch):
    from threading import Event
    from streamlit.testing.v1 import AppTest
    from story_channel.assets import AssetPipeline
    from story_channel.background_library import BackgroundLibrary
    from test_dual_panorama import Sectors
    from story_channel.dual_panorama import approve as actual_approve
    world.generate_characters()
    engine = AssetPipeline(world.manager,world.project_id,demo=True,image_provider=Sectors())
    lid = BackgroundLibrary(engine).add("Office","An office",reference_type="KEY_360",floor_plan="Fixed center; north window",
                                       object_layout={"desk":"east wall"}).location_id
    from story_channel.floor_plan_guides import publish_guides
    publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    from test_dual_panorama import approve_bridges
    approve_bridges(engine,lid)
    state = engine.generate("backgrounds",subject=lid)
    record = state.records[f"backgrounds:{lid}__front_panorama"]
    release = Event()
    def delayed(*args):
        if not release.wait(10):
            raise RuntimeError("Test approval was not released")
        return actual_approve(*args)
    monkeypatch.setattr("story_channel.dual_panorama.approve",delayed)
    def display(engine,lid):
        from story_channel.background_revisions_ui import master_control
        master_control(engine,lid,"front_panorama")
    app = AppTest.from_function(display,args=(engine,lid)).run()
    button_key = f"approve-location-master-{engine.project_id}-{lid}-front_panorama"
    check_key = f"review-panorama-layout-{engine.project_id}-{lid}-{record.asset.asset_id}"
    assert app.button(key=button_key).disabled
    app.checkbox(key=check_key).check().run()
    assert not app.exception and not app.button(key=button_key).disabled
    try:
        app.button(key=button_key).click().run()
        assert app.button(key=button_key).label == "Approving..."
        assert app.button(key=button_key).disabled
    finally:
        release.set()
    app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception and app.button(key=button_key).label == "Approved"
