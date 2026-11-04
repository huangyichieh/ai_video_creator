import pytest

from story_channel.assets import AssetPipeline
from story_channel.media_providers import DemoImageProvider, OpenAIImageProvider
from story_channel.visual_generation import VisualGeneration, DESIGN_MODEL
from test_studio import world


def test_live_character_provider_is_openai(world):
    engine = AssetPipeline(world.manager, world.project_id, demo=False)
    assert world.manager.settings.image_provider == "runway"
    assert isinstance(engine.character_image_provider, OpenAIImageProvider)


@pytest.mark.parametrize("fail", [False, True])
def test_character_retry_uses_openai_and_keeps_completed_media(world, monkeypatch, fail):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    cid = engine.creative.artifact("characters").characters[0].character_id
    VisualGeneration(engine).update("character_references", cid, mode="text", prompt="A detailed character model sheet")
    state = engine.generate("character_references", subject=cid)
    previous = state.records["character_references:" + cid].asset
    previous_file = engine.local_path(previous.file_path)
    calls = []
    class Images(DemoImageProvider):
        def generate(self, prompt, refs, config):
            calls.append(config)
            if fail:
                raise RuntimeError("OpenAI unavailable")
            return super().generate(prompt, refs, config)
    engine.demo = False
    engine.character_image_provider = Images()
    monkeypatch.setattr("story_channel.provider_prompts.compile_prompt", lambda *a, **kw: pytest.fail("Character prompts must not use Runway compaction"))
    engine.image_provider.generate = lambda *a, **kw: pytest.fail("Character generation must not call Runway")
    if fail:
        with pytest.raises(RuntimeError, match="OpenAI unavailable"):
            engine.generate("character_references", subject=cid, force=True)
    else:
        engine.generate("character_references", subject=cid, force=True)
    assert calls and calls[0].provider == "openai"
    assert calls[0].model == DESIGN_MODEL
    assert previous_file.is_file()
