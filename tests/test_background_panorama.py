from pathlib import Path
from threading import Event

import numpy as np
import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary, locations
from story_channel.background_panorama import (MODEL, PanoramaViewProvider, project_view, subjects)
from story_channel.costs import CostTracker
from story_channel.media_utils import file_hash
from test_studio import world
from test_ui import finish_background


def sphere(path):
    # A smooth, periodic angular pattern: the two edges describe the same wall.
    longitude = (np.arange(720, dtype=float) + .5) / 720 * 2 * np.pi - np.pi
    pixels = np.empty((360, 720, 3), dtype=np.uint8)
    pixels[:, :, 0] = np.rint(127 + 120 * np.cos(longitude))
    pixels[:, :, 1] = np.rint(127 + 120 * np.sin(longitude))
    pixels[:, :, 2] = 80
    Image.fromarray(pixels).save(path)


def test_spherical_projection_cardinal_directions_and_shared_overlap(tmp_path):
    source = tmp_path / "panorama.png"
    sphere(source)
    images = {}
    for view, yaw in (("left", -90), ("right", 90), ("front", 0), ("back", 180)):
        destination = tmp_path / (view + ".png")
        project_view(source, destination, yaw=yaw, size=(201, 101))
        images[view] = np.asarray(Image.open(destination)).astype(int)
    assert images["front"][50, 100, 0] > 240
    assert images["back"][50, 100, 0] < 15
    assert images["right"][50, 100, 1] > 240
    assert images["left"][50, 100, 1] < 15
    # The shelf/wall at -45° appears on the right of Left and left of Front.
    offset = round(201 / 2 * np.tan(np.deg2rad(45)) / np.tan(np.deg2rad(50)))
    assert np.max(np.abs(images["left"][50, 100 + offset] - images["front"][50, 100 - offset])) <= 3
    # The same relation holds across the wrap seam near the back wall.
    assert np.max(np.abs(images["right"][50, 100 + offset] - images["back"][50, 100 - offset])) <= 3


def test_one_source_only_and_revision_apply_without_provider_call(world):
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    lid = locations(world.pipeline)[0].location_id
    calls = []
    original = engine.image_provider.generate
    def image(prompt, references, config):
        calls.append(prompt)
        return original(prompt, references, config)
    def forbidden(*args):
        pytest.fail("No directional image should be generated")
    engine.image_provider.generate = image
    engine.background_projection_provider.generate = forbidden
    state = engine.generate("backgrounds", subjects=subjects(lid), force=True)
    assert len(calls) == 1
    assert [t.subject for t in engine.tasks(state) if t.stage == "backgrounds"] == subjects(lid)
    assert len(state.records) == 1
    source = state.records["backgrounds:" + lid + "__panorama"]
    assert source.asset.generation_params["coverage360Verified"] is False
    assert "fullSphere" not in source.asset.generation_params
    with Image.open(engine.local_path(source.asset.file_path)) as img:
        assert img.width == img.height * 2
    usage = [u for u in CostTracker(world.manager, world.project_id).records() if u.step == "assets:backgrounds"]
    assert len(usage) == 1 and usage[0].provider != "local"
    library = BackgroundLibrary(engine)
    assert library.approved(lid, library.approve(lid))
    engine.generate("backgrounds", subjects=subjects(lid), force=True)
    state = library.apply_panorama_revision(lid, source.asset.asset_id)
    assert len(calls) == 2
    assert state.records["backgrounds:" + lid + "__panorama"].asset.asset_id == source.asset.asset_id
    assert not library.approved(lid, state)


def test_stop_after_panorama_and_resume_without_another_generation(world):
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    lid = locations(world.pipeline)[0].location_id
    original = engine.image_provider.generate
    calls = []
    stop = Event()
    def image(*args):
        calls.append(1)
        result = original(*args)
        stop.set()
        return result
    engine.image_provider.generate = image
    engine.stop_requested = stop.is_set
    state = engine.generate("backgrounds", subjects=subjects(lid))
    assert state.generation_finished == 1
    assert state.records["backgrounds:" + lid + "__panorama"].status == "succeeded"
    stop.clear()
    state = engine.generate("backgrounds", subjects=subjects(lid))
    assert calls == [1] and not state.stopped
    assert all(state.records["backgrounds:" + s].status == "succeeded" for s in subjects(lid))


def test_panorama_dimensions_are_never_stretched_and_edge_check_is_not_certification(tmp_path):
    from story_channel.background_panorama import normalize_panorama, assess_panorama
    path = tmp_path / "wide.png"
    Image.new("RGB", (1920, 1080), "blue").save(path)
    normalize_panorama(path)
    check = assess_panorama(path)
    assert (check["width"], check["height"]) == (1920, 1080)
    assert not check["isEquirectangularLayout"] and not check["coverage360Verified"]
    sphere(path)
    assert not assess_panorama(path)["seamSuspected"]
    image = np.asarray(Image.open(path)).copy()
    image[:, :40] = 255
    Image.fromarray(image).save(path)
    assert assess_panorama(path)["seamSuspected"]


def test_production_approval_requires_current_coverage_and_seam_review(world):
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    lid = locations(world.pipeline)[0].location_id
    state = engine.generate("backgrounds", subjects=subjects(lid))
    library = BackgroundLibrary(engine)
    engine.demo = False
    state = library.import_panorama(lid, engine.local_path(state.records["backgrounds:" + lid + "__panorama"].asset.file_path).read_bytes())
    with pytest.raises(ValueError, match="Review the current panorama"):
        library.approve(lid)
    asset = state.records["backgrounds:" + lid + "__panorama"].asset
    choice = {lid: {"assetId": asset.asset_id, "coverage360": True, "seamContinuous": False}}
    with pytest.raises(ValueError, match="start/end seam"):
        library.approve(lid, review_choices=choice)
    choice[lid]["seamContinuous"] = True
    state = library.approve(lid, review_choices=choice)
    assert library.approved(lid, state)
    state = library.apply_panorama_revision(lid, asset.asset_id)
    assert not library.approved(lid, state)
    assert not library.panorama_reviewed(lid, state)


def test_native_panorama_import_keeps_one_image_and_invalid_shape_keeps_saved_state(world):
    from io import BytesIO
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    library = BackgroundLibrary(engine)
    lid = locations(world.pipeline)[0].location_id
    data = BytesIO()
    Image.new("RGB", (800, 400), "blue").save(data, "PNG")
    before_costs = len(CostTracker(world.manager, world.project_id).records())
    state = library.import_panorama(lid, data.getvalue())
    assert len(state.records) == 1
    record = state.records["backgrounds:" + lid + "__panorama"]
    assert record.asset.model == "uploaded-panorama" and engine._intact(record)
    assert library.digest(lid, state, engine.tasks(state)) is not None
    assert len(CostTracker(world.manager, world.project_id).records()) == before_costs
    before = engine.state_path.read_bytes()
    invalid = BytesIO()
    Image.new("RGB", (800, 450), "red").save(invalid, "PNG")
    with pytest.raises(ValueError, match="native 2:1"):
        library.import_panorama(lid, invalid.getvalue())
    assert engine.state_path.read_bytes() == before


def test_active_panorama_circular_loading_keeps_cards_and_add_form(world, monkeypatch):
    from story_channel.media_providers import DemoImageProvider
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    started, release = Event(), Event()
    original = DemoImageProvider.generate
    def delayed(self, prompt, references, config):
        if config.options.get("panorama"):
            started.set()
            assert release.wait(timeout=30)
        return original(self, prompt, references, config)
    monkeypatch.setattr(DemoImageProvider, "generate", delayed)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Background"
    app.run()
    lid = locations(world.pipeline)[0].location_id
    task = None
    try:
        app.button(key="generate-background-" + lid).click().run()
        task = app.session_state["workspace-task"]
        assert started.wait(timeout=15)
        app.run()
        assert not app.exception
        assert any("background-result-circle" in node.proto.body and "Generating panorama" in node.proto.body
                   for node in app.get("html"))
        assert any(field.label == "Title" for field in app.text_input)
        release.set()
        finish_background(app)
        assert not app.exception
        assert not any(button.key and button.key.startswith("rebuild-background-views") for button in app.button)
        assert any(button.key == "download-background-panorama-" + lid for button in app.get("download_button"))
    finally:
        release.set()
        if task:
            task["future"].result(timeout=20)
