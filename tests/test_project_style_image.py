from pathlib import Path

import pytest

from story_channel.assets import AssetPipeline
from story_channel.background_library import locations
from story_channel.library_media import LibraryMedia
from story_channel.style_rules import StyleRules
from story_channel.style_check import check_style, signature
from story_channel.visual_generation import VisualGeneration
from story_channel.media_providers import DemoImageProvider
from test_studio import world
from test_character_visual_costumes import image_bytes
from test_style_rules import ready, Reviewer


def test_style_upload_persists_preserves_rules_validates_and_removes(world):
    engine = ready(world)
    styles = StyleRules(engine)
    styles.save("Clean thin outlines")
    state = styles.set_image(image_bytes())
    assert state.project_style_rules == "Clean thin outlines"
    assert styles.image(state).read_bytes() == image_bytes()
    styles.save("Soft cel shading")
    assert engine.load().project_style_image == state.project_style_image
    before = (engine.path / "style_rules.json").read_bytes()
    with pytest.raises(ValueError, match="valid image"):
        styles.set_image(b"invalid")
    assert (engine.path / "style_rules.json").read_bytes() == before
    fresh = AssetPipeline(engine.manager, engine.project_id)
    assert fresh.load().project_style_image == state.project_style_image
    state = styles.set_image(None)
    assert state.project_style_image is None and state.project_style_rules == "Soft cel shading"


@pytest.mark.parametrize("stage", ["character_references", "backgrounds"])
@pytest.mark.parametrize("mode", ["text", "image"])
def test_shared_style_is_separate_from_optional_design_in_each_mode(world, stage, mode):
    engine = ready(world)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    subject = cid if stage == "character_references" else locations(engine.creative)[0].location_id
    inputs = VisualGeneration(engine)
    from test_visual_generation import set_image
    state = set_image(engine, stage, subject, image_bytes("red"))
    design = inputs.selected_image(stage, subject, state)
    state = StyleRules(engine).set_image(image_bytes("blue"))
    style = StyleRules(engine).image(state)
    inputs.update(stage, subject, mode=mode, prompt="Create the requested visual")
    calls = []
    original = engine.image_provider.generate
    def capture(prompt, refs, config):
        calls.append((prompt, refs, config))
        return original(prompt, refs, config)
    engine.image_provider.generate = capture
    state = engine.generate(stage, subject=subject, force=True)
    prompt, refs, config = calls[0]
    assert refs == ([design, style] if mode == "image" else [style])
    assert config.options["reference_tags"] == (["design", "style"] if mode == "image" else ["style"])
    assert "STYLE ONLY" in prompt and "Do not copy its subjects" in prompt
    assert "@design" in prompt if mode == "image" else "@design" not in prompt
    record = next(record for record in state.records.values() if record.stage == stage)
    assert record.asset.generation_params["inputImages"][-1] == state.project_style_image["file"]


def test_openai_front_view_and_props_use_shared_style_image(world):
    engine = ready(world)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    state = StyleRules(engine).set_image(image_bytes())
    style = StyleRules(engine).image(state)
    calls = []
    class Capture(DemoImageProvider):
        def generate(self, prompt, refs, config):
            calls.append((prompt, refs))
            return super().generate(prompt, refs, config)
    VisualGeneration(engine).design_reference("character_references", cid, "Red coat", provider=Capture())
    assert calls[0][1] == [style] and "STYLE ONLY" in calls[0][0]
    assert "ONE front-view character only" in calls[0][0]
    engine.image_provider = Capture()
    media = LibraryMedia(engine.manager, engine.project_id)
    media.assets = engine
    media.image("prop:brass_key", "A brass key")
    assert calls[-1][1] == [style] and "STYLE ONLY" in calls[-1][0]


def test_style_check_uses_guide_as_baseline_and_replacement_stales_report(world):
    engine = ready(world)
    StyleRules(engine).set_image(image_bytes())
    provider = Reviewer()
    report = check_style(engine, provider=provider)
    content = provider.calls[0][0]["content"]
    assert sum(item["type"] == "input_image" for item in content) == len(report["assets"]) + 2
    assert any("PROJECT STYLE REFERENCE" in item.get("text", "") for item in content)
    assert report["signature"] == signature(engine, engine.load())
    StyleRules(engine).set_image(image_bytes("red"))
    assert report["signature"] != signature(engine, engine.load())


def test_damaged_style_image_is_not_silently_ignored(world):
    engine = ready(world)
    state = StyleRules(engine).set_image(image_bytes())
    StyleRules(engine).image(state).write_bytes(b"modified")
    cid = world.pipeline.artifact("characters").characters[0].character_id
    with pytest.raises(ValueError, match="missing or modified"):
        VisualGeneration(engine).request("character_references", cid, engine.load())
    with pytest.raises(ValueError, match="missing or modified"):
        check_style(engine, provider=Reviewer())


def test_many_runway_identity_inputs_keep_style_as_a_separate_tagged_image(tmp_path):
    from story_channel.balanced_providers import RunwayImageProvider
    from story_channel.models import ProviderConfig
    from PIL import Image
    paths = []
    for index in range(4):
        path = tmp_path / ("style.png" if index == 3 else f"design{index}.png")
        path.write_bytes(image_bytes("blue" if index == 3 else "red"))
        paths.append(path)
    uploaded, submitted = [], []
    provider = RunwayImageProvider()
    def upload(path):
        uploaded.append(path)
        return "runway://" + path.name
    def task(endpoint, body, config, unit, amount):
        submitted.append(body)
        Image.new("RGB", (64, 64), "white").save(config.options["output_path"])
        return "mock-job"
    provider.upload, provider.task = upload, task
    config = ProviderConfig(provider="runway", model="gen4_image", options={"output_path": str(tmp_path / "result.png"),
        "reference_tags": ["content1", "content2", "content3", "style"]})
    provider.generate("Compose a scene matching @style", paths, config)
    assert len(uploaded) == 2 and uploaded[-1] == paths[-1]
    assert [item["tag"] for item in submitted[0]["referenceImages"]] == ["content", "style"]


def test_episode_import_copies_shared_style_guide(world):
    from test_background_library import ready as approved_characters
    from story_channel.studio import Episodes
    initial = AssetPipeline(world.manager, world.project_id, demo=True)
    StyleRules(initial).set_image(image_bytes())
    engine, _ = approved_characters(world)
    engine.generate("backgrounds")
    engine.approve("backgrounds")
    episodes = Episodes(world.manager, world.project_id)
    episode = episodes.create("A new day", "A keeper checks the beacon.", 60)
    episodes.import_assets(episode.project_id)
    child = AssetPipeline(world.manager, episode.project_id)
    assert child.load().project_style_image == engine.load().project_style_image
    assert StyleRules(child).image(child.load()).read_bytes() == image_bytes()
