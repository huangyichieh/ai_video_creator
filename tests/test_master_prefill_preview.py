import numpy as np
from PIL import Image
import pytest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.floor_plan_guides import publish_guides
from story_channel import location_pairs as pairs
from story_channel.master_prefill import prepare
from test_dual_panorama import Sectors
from test_studio import world


@pytest.fixture
def bridges(world):
    world.generate_characters()
    provider = Sectors()
    engine = AssetPipeline(world.manager,world.project_id,demo=True,image_provider=provider)
    lid = BackgroundLibrary(engine).add("Office","Fixed office",reference_type="KEY_360",floor_plan="Fixed center",
        object_layout={"desk":"east wall","cabinet":"west wall"}).location_id
    publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    state = pairs.generate(engine,lid,"bridges","Shared visual style")
    pairs.approve(engine,lid,"bridges",expected={name:state.records[f"backgrounds:{lid}__{name}"].asset.asset_id for name in pairs.names("bridges")})
    return engine,lid,provider


def test_overlap_coordinates_and_black_unknown_regions_are_computed_before_masters(bridges):
    engine,lid,provider = bridges
    expected = {"front_panorama":{"east_bridge":[60,100],"west_bridge":[-100,-60]},
                "back_panorama":{"east_bridge":[80,120],"west_bridge":[240,280]}}
    for name,ranges in expected.items():
        metadata = prepare(engine,engine.load(),lid,name)
        assert {region["bridge"]:region["masterYawRange"] for region in metadata["overlaps"]} == ranges
        assert metadata["unknownRegions"] == [[512,0,2048,1440]]
        assert metadata["knownPixelCount"] == 2*512*1440
        assert metadata["unknownPixelCount"] == 1536*1440
        with Image.open(engine.local_path(metadata["mask"])) as image:
            unknown = np.asarray(image.getchannel("A")) == 0
        with Image.open(engine.local_path(metadata["canvas"])) as image:
            canvas = np.asarray(image)
        assert np.all(canvas[unknown] == 0)
        assert np.any(canvas[~unknown] != 0)
        assert f"backgrounds:{lid}__{name}" not in engine.load().records
    assert len(provider.calls) == 2  # Projection/preview is local; neither master has been generated.


def test_ui_shows_the_exact_canvases_later_sent_for_master_completion(bridges):
    from streamlit.testing.v1 import AppTest
    engine,lid,provider = bridges
    def display(engine,lid):
        from story_channel.master_prefill_ui import previews
        previews(engine,lid)
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    assert not app.exception and len(app.image) == 2
    assert any("black regions will be filled" in box.label for box in app.expander)
    assert len(provider.calls) == 2
    metadata = {name:prepare(engine,engine.load(),lid,name) for name in pairs.names("masters")}
    pairs.generate(engine,lid,"masters","Fill the room in the same style")
    for name,(prompt,images) in zip(pairs.names("masters"),provider.calls[2:]):
        assert images[0] == engine.local_path(metadata[name]["canvas"])
        assert "continue smoothly" in prompt
        assert "identity, scale, perspective, materials" in prompt
        assert "mask is authoritative" in prompt


def test_perspective_prefill_tracks_valid_rays_and_cache_model(bridges):
    engine,lid,_ = bridges
    state = engine.load()
    angular = prepare(engine,state,lid,"front_panorama")
    state.location_reference_sets[lid]["bridge_warp"] = {"model":"perspective"}
    perspective = prepare(engine,state,lid,"front_panorama")
    assert angular["canvas"] != perspective["canvas"]
    assert perspective["warp"]["model"] == "perspective"
    assert perspective["knownPixelCount"] < angular["knownPixelCount"]
    assert perspective["featherPixelCount"] > 0
    assert perspective["protectedPixelCount"] > 0
    assert len(perspective["unknownRegions"]) > 1
    with Image.open(engine.local_path(perspective["canvas"])) as source, Image.open(engine.local_path(perspective["mask"])) as mask:
        rgb = np.asarray(source)
        alpha = np.asarray(mask.getchannel("A"))
        assert np.all(rgb[alpha == 0] == 0)
        assert np.any((alpha > 0)&(alpha < 255))
    assert prepare(engine,state,lid,"front_panorama") == perspective


def test_reference_save_keeps_optional_warp_configuration(bridges):
    engine,lid,_ = bridges
    library = BackgroundLibrary(engine)
    spec = engine.load().location_reference_sets[lid]
    state = library.configure_references(lid,"KEY_360",spec["floor_plan"],spec["object_layout"],
        bridge_warp={"model":"perspective","featherDegrees":4})
    spec = state.location_reference_sets[lid]
    library.configure_references(lid,"KEY_360",spec["floor_plan"],spec["object_layout"])
    assert engine.load().location_reference_sets[lid]["bridge_warp"]["model"] == "perspective"
    assert engine.load().location_reference_sets[lid]["bridge_warp"]["featherDegrees"] == 4
