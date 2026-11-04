from pathlib import Path

import numpy as np
from PIL import Image
import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.background_enhancement import generate
from story_channel.dual_panorama import approve, approve_views, approved_views, all_views_approved, PANORAMA_MODEL
from story_channel.files import input_hash
from story_channel.media_utils import file_hash
from story_channel.models import GeneratedAsset
from story_channel.costs import CostTracker
from test_dual_panorama import prepare, Sectors
from test_studio import world


def test_openai_generates_both_masters_while_runway_never_generates_yaws(world, monkeypatch):
    from story_channel.media_providers import OpenAIImageProvider
    from story_channel.balanced_providers import RunwayImageProvider
    world.generate_characters()
    synthetic = Sectors()
    def openai(provider, prompt, refs, config):
        assert config.provider == "openai" and config.model == PANORAMA_MODEL
        provider.last_usage = {"image_count":1}
        result = synthetic.generate(prompt, refs, config)
        result.provider, result.model = config.provider, config.model
        return result
    def forbidden(*args, **kwargs):
        pytest.fail("Runway must not generate panorama masters or projected yaw images")
    monkeypatch.setattr(OpenAIImageProvider, "generate", openai)
    monkeypatch.setattr(RunwayImageProvider, "generate", forbidden)
    engine = AssetPipeline(world.manager, world.project_id, demo=False)
    lid = BackgroundLibrary(engine).add("Office", "A fixed office", reference_type="KEY_360",
        floor_plan="Fixed center, north windows, west door", object_layout={"desks":"east wall"}).location_id
    from story_channel.floor_plan_guides import publish_guides
    publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    from story_channel.location_pairs import approve as approve_pair
    for group,members in (("bridges",("east_bridge","west_bridge")),("masters",("front_panorama","back_panorama"))):
        state = engine.generate("backgrounds",subject=lid)
        approve_pair(engine,lid,group,expected={name:state.records[f"backgrounds:{lid}__{name}"].asset.asset_id for name in members})
    state = engine.generate("backgrounds", subject=lid)
    assert len(synthetic.calls) == 4
    assert all(state.records[f"backgrounds:{lid}__view_{yaw:03d}"].asset.provider == "local" for yaw in range(0,360,45))
    assert sum(record.image_count for record in CostTracker(engine.manager, engine.project_id).records() if record.provider == "openai") == 4


def test_yaw_approval_gate_and_ui_enable_enhancement_only_after_all_current_views_approved(world):
    engine, lid, _ = prepare(world)
    def display(engine, lid):
        from story_channel.background_assets_ui import background_outputs
        from story_channel.dialog_ui import show_pending_dialog
        import streamlit as st
        if st.session_state.get("dialog-open-requested"):
            show_pending_dialog()
        background_outputs(engine, lid)
    app = AppTest.from_function(display, args=(engine,lid)).run()
    assert not app.button(key="generate-yaw-"+lid).disabled
    assert app.button(key="enhance-background-"+lid).disabled
    engine.generate("backgrounds", subject=lid)
    app.run()
    assert app.button(key="enhance-background-"+lid).disabled
    approve_views(engine,lid)
    app.run()
    assert not app.exception and not app.button(key="enhance-background-"+lid).disabled
    app.button(key="enhance-background-"+lid).click().run()
    assert not app.exception and app.get("dialog")
    assert any(box.label == "Operation" for box in app.selectbox)
    app.button(key="close-background-enhance").click().run()
    assert "background-enhance-target" not in app.session_state
    engine.generate("backgrounds", subject=lid+"__view_045", force=True)
    assert sum(approved_views(engine,engine.load(),lid).values()) == 7
    app.run()
    assert app.button(key="enhance-background-"+lid).disabled


class OptionalProvider:
    last_usage = {}
    calls = 0
    fail = False
    def generate(self, prompt, refs, config):
        self.calls += 1
        self.last_usage = {"image_count":1}
        if self.fail:
            from story_channel.llm import ProviderError
            raise ProviderError("Simulated optional reference failure")
        path = Path(config.options["output_path"])
        with Image.open(refs[0]) as source:
            Image.new("RGB",source.size,"red").save(path)
        return GeneratedAsset(type="image", provider=config.provider, model=config.model, file_path=str(path), prompt_hash=input_hash(prompt))


def test_optional_runway_results_preserve_approved_world_and_repair_protected_pixels(world):
    engine,lid,_ = prepare(world)
    provider = OptionalProvider()
    with pytest.raises(ValueError,match="Approve all eight"):
        generate(engine,lid,provider=provider)
    assert provider.calls == 0
    engine.generate("backgrounds",subject=lid)
    approve_views(engine,lid)
    before = {key:record.file_hash for key,record in engine.load().records.items() if key.startswith("backgrounds:"+lid)}
    enhanced = generate(engine,lid,provider=provider)
    assert not enhanced["canonicalYawReplaced"] and enhanced["sourceStillCurrent"]
    repaired = generate(engine,lid,mode="repair",region=(.25,.25,.25,.25),provider=provider)
    assert repaired["protectedPixelsVerified"]
    with Image.open(engine.local_path(repaired["sourceImage"])) as source, Image.open(engine.local_path(repaired["asset"]["filePath"])) as result:
        a,b = np.asarray(source),np.asarray(result)
        assert np.array_equal(a[:a.shape[0]//4],b[:b.shape[0]//4])
        assert not np.array_equal(a,b)
    additional = generate(engine,lid,mode="additional_reference",yaw=22,provider=provider)
    assert additional["yaw"] == 22 and additional["mode"] == "additional_reference"
    from story_channel.background_enhancement import use_reference
    from story_channel.location_references import LocationReferenceResolver
    used = use_reference(engine,lid,additional,"reception desk")
    assert used["status"] == "approved_reference"
    references = LocationReferenceResolver(engine,engine.load()).resolve(lid,22,22)
    assert "__detail_" in references[0] and len(references) <= 3
    assert all(file_hash(engine.local_path(engine.load().records[key].asset.file_path)) == value for key,value in before.items())
    assert all_views_approved(engine,engine.load(),lid)
    provider.fail = True
    from story_channel.llm import ProviderError
    with pytest.raises(ProviderError,match="Simulated"):
        generate(engine,lid,provider=provider)
    rows = [row for row in CostTracker(engine.manager,engine.project_id).records() if row.step == "background:runway:"+lid]
    assert len(rows) == 4 and rows[-1].outcome == "failed" and rows[-1].image_count == 1
