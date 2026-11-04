import pytest

from story_channel import location_pairs as pairs
from story_channel.dual_panorama import MASTERS,approved,pair_ready
from test_location_details import prepare
from test_location_master_pack import master_pack,approve_pack
from test_studio import world


@pytest.mark.parametrize("chosen",MASTERS)
def test_regenerate_one_master_preserves_other_images_and_reopens_review(master_pack,chosen):
    engine,lid,provider,_ = prepare(master_pack)
    before = engine.load()
    saved_set = pairs.versions(engine,lid,"masters")[0]
    count = len(provider.calls)
    state = pairs.regenerate_one(engine,lid,chosen)
    assert len(provider.calls) == count+1
    prompt,refs = provider.calls[-1]
    assert "FINAL BACKGROUND REQUIREMENT" in prompt and "No people, story characters" in prompt
    assert len([path for path in refs if path.parent.name == "overlap_anchors"]) == 2
    assert not any(approved(engine,state,lid,name) for name in MASTERS)
    assert pairs.current(engine,state,lid,"masters")
    for name in MASTERS:
        key = f"backgrounds:{lid}__{name}"
        old,new = before.records[key],state.records[key]
        assert new.status == "succeeded"
        if name == chosen:
            assert new.asset.asset_id != old.asset.asset_id
            assert new.revision == old.revision+1
        else:
            assert (new.asset.asset_id,new.file_hash,new.revision,new.input_hash) == (
                old.asset.asset_id,old.file_hash,old.revision,old.input_hash)
    assert len(pairs.versions(engine,lid,"masters")) >= 2
    assert pair_ready(engine,approve_pack(engine,lid),lid)
    restored = pairs.apply(engine,lid,"masters",saved_set["id"])
    assert len(provider.calls) == count+1
    assert {name:restored.records[f"backgrounds:{lid}__{name}"].asset.asset_id for name in MASTERS} == {
        name:before.records[f"backgrounds:{lid}__{name}"].asset.asset_id for name in MASTERS}


def test_failed_individual_request_retains_media_and_can_retry_without_other_requests(master_pack):
    engine,lid,provider,_ = prepare(master_pack)
    before = engine.load()
    old_generate = provider.generate
    def fail(*args,**kwargs):
        raise RuntimeError("test image provider failure")
    provider.generate = fail
    from story_channel.llm import ProviderError
    with pytest.raises(ProviderError,match="test image provider failure"):
        pairs.regenerate_one(engine,lid,"front_panorama")
    state = engine.load()
    assert state.records[f"backgrounds:{lid}__front_panorama"].status == "failed"
    for name in MASTERS:
        key = f"backgrounds:{lid}__{name}"
        assert state.records[key].asset.asset_id == before.records[key].asset.asset_id
    provider.generate = old_generate
    count = len(provider.calls)
    state = pairs.regenerate_one(engine,lid,"front_panorama")
    assert len(provider.calls) == count+1
    assert pairs.current(engine,state,lid,"masters")


def test_ui_has_individual_regenerate_buttons(master_pack,monkeypatch):
    from streamlit.testing.v1 import AppTest
    engine,lid,provider,_ = prepare(master_pack)
    def launch(engine,callback,label,**kwargs):
        callback()
    monkeypatch.setattr("story_channel.background_assets_ui.launch_background",launch)
    def display(engine,lid):
        from story_channel.dual_panorama_ui import outputs
        outputs(engine,lid)
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    assert not app.exception
    keys = [f"regenerate-master-{engine.project_id}-{lid}__{name}" for name in MASTERS]
    assert all(not app.button(key=key).disabled for key in keys)
    count = len(provider.calls)
    app.button(key=keys[1]).click().run()
    assert not app.exception
    assert len(provider.calls) == count+1
