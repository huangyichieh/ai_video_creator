import json
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from story_channel.costs import CostTracker
from story_channel.files import input_hash
from story_channel.llm import ProviderError
from story_channel.location_references import LocationReferenceResolver
from story_channel.media_providers import ThreadLocalUsage
from story_channel.models import GeneratedAsset
from story_channel.room_canonical import ANGLES, records
from story_channel.style_rules import StyleRules
from test_shared_room import make_room, approve_pack
from test_studio import world
from test_character_visual_costumes import image_bytes


class Enhancer(ThreadLocalUsage):
    def __init__(self):
        self.calls = []
        self.fail = False

    def generate(self,prompt,refs,config):
        self.calls.append((prompt,list(refs),config))
        self.last_usage = {"image_count":1}
        if self.fail:
            raise ProviderError("Mock enhancement failed")
        Path(config.options["output_path"]).parent.mkdir(parents=True,exist_ok=True)
        # No network; preserve the raw reference geometry for inspection.
        with Image.open(refs[0]) as image:
            image.save(config.options["output_path"])
        return GeneratedAsset(type="image",provider=config.provider,model=config.model,
                              file_path=config.options["output_path"],prompt_hash=input_hash(prompt))


def test_canonical_inputs_costs_references_and_invalidation(world):
    engine,library,lid = make_room(world)
    StyleRules(engine).save("Soft cel shading")
    StyleRules(engine).set_image(image_bytes("blue"))
    engine.generate("backgrounds",subject=lid+"__room_panorama")
    engine.generate("backgrounds",subjects=[f"{lid}__raw_{yaw:03d}" for yaw in ANGLES])
    state = engine.load()
    raw = records(engine,state,lid,enhanced=False)
    assert len(raw) == 8 and not records(engine,state,lid)
    assert all(r.asset.provider == "local" for r in raw)
    cameras = [r.asset.generation_params["canonicalProjection"]["cameraCenter"] for r in raw]
    assert all(camera == cameras[0] for camera in cameras)
    with pytest.raises(ValueError,match="all required"):
        approve_pack(engine,library,lid)
    enhancer = Enhancer()
    engine.panorama_image_provider = enhancer
    engine.demo = False
    engine.settings.image_cost_per_image = Decimal("0.05")
    state = engine.generate("backgrounds",subjects=[f"{lid}__enhanced_{yaw:03d}" for yaw in ANGLES])
    assert len(enhancer.calls) == 8
    for yaw,(prompt,refs,config) in zip(ANGLES,enhancer.calls):
        assert config.provider == "openai"
        assert refs[0] == engine.local_path(state.records[f"backgrounds:{lid}__raw_{yaw:03d}"].asset.file_path)
        assert refs[1] == engine.local_path(state.records[f"backgrounds:{lid}__room_panorama"].asset.file_path)
        assert refs[2] == StyleRules(engine).image(state)
        assert "STYLE ONLY" in prompt and "Soft cel shading" in prompt
        assert "NEVER move, duplicate, remove or resize" in prompt
        assert '"objectLayout"' in prompt and '"cameraPlan"' in prompt
    usages = [r for r in CostTracker(engine.manager,engine.project_id).records() if r.provider == "openai"]
    assert len(usages) == 8 and sum(r.cost for r in usages) == Decimal("0.40")
    with pytest.raises(ValueError,match="Compare all eight"):
        library.approve(lid)
    state = approve_pack(engine,library,lid)
    resolver = LocationReferenceResolver(engine,state)
    selected = resolver.resolve(lid,44,80)
    assert len(selected) == 3 and "__shot_" in selected[0]
    assert selected[1:] == [f"backgrounds:{lid}__enhanced_045",f"backgrounds:{lid}__enhanced_090"]
    assert resolver.resolve(lid,359,359,max_references=1) == [f"backgrounds:{lid}__enhanced_000"]
    pack = json.loads((engine.path/"locations"/lid/"location_reference_pack.json").read_text(encoding="utf-8"))
    assert len(pack["views"]) == 8 and pack["spatialTruth"] == "shared_3d_room"
    assert all(pair["raw"]["file"] != pair["enhanced"]["file"] for pair in pack["views"])
    raw_ids = [r.asset.asset_id for r in raw]
    source_id = state.records[f"backgrounds:{lid}__room_panorama"].asset.asset_id
    engine.generate("backgrounds",subject=lid+"__enhanced_045",force=True)
    state = engine.load()
    assert not library.approved(lid,state)
    assert [r.asset.asset_id for r in records(engine,state,lid,enhanced=False)] == raw_ids
    assert state.records[f"backgrounds:{lid}__room_panorama"].asset.asset_id == source_id
    revisions = engine.revisions("backgrounds",lid+"__enhanced_045")
    assert len(revisions) == 2
    previous = min(revisions,key=lambda record:record.revision)
    engine.apply_revision("backgrounds",lid+"__enhanced_045",previous.asset.asset_id)
    assert engine.load().records[f"backgrounds:{lid}__enhanced_045"].asset.asset_id == previous.asset.asset_id
    approve_pack(engine,library,lid)
    StyleRules(engine).save("Watercolor")
    state = engine.inspect()
    assert len(records(engine,state,lid,enhanced=False)) == 8
    assert not records(engine,state,lid) and not library.approved(lid,state)


def test_failed_enhancement_preserves_raw_outputs_and_records_usage(world):
    engine,library,lid = make_room(world)
    engine.generate("backgrounds",subject=lid+"__room_panorama")
    engine.generate("backgrounds",subjects=[f"{lid}__raw_{yaw:03d}" for yaw in ANGLES])
    enhancer = Enhancer()
    enhancer.fail = True
    engine.panorama_image_provider = enhancer
    engine.demo = False
    engine.settings.image_cost_per_image = Decimal("0.05")
    with pytest.raises(ProviderError,match="Mock enhancement failed"):
        engine.generate("backgrounds",subject=lid+"__enhanced_045")
    state = engine.load()
    assert len(records(engine,state,lid,enhanced=False)) == 8
    assert state.records[f"backgrounds:{lid}__enhanced_045"].status == "failed"
    usage = CostTracker(engine.manager,engine.project_id).records()[-1]
    assert usage.provider == "openai" and usage.outcome == "failed" and usage.cost == Decimal("0.05")
