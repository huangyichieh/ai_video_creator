from decimal import Decimal
from pathlib import Path

from PIL import Image
import pytest

from story_channel.library_media import LibraryMedia,PROP_IMAGE_MODEL
from story_channel.models import GeneratedAsset
from story_channel.costs import CostTracker
from story_channel.files import input_hash
from test_studio import world


class PropImages:
    def __init__(self,fail=False):
        self.fail,self.calls = fail,[]
        self.last_usage = {}

    def generate(self,prompt,references,config):
        self.calls.append((prompt,list(references),config))
        self.last_usage = {"image_count":1}
        if self.fail:
            raise RuntimeError("OpenAI prop image failed")
        path = Path(config.options["output_path"])
        Image.new("RGB",(80,80),"blue").save(path)
        return GeneratedAsset(type="image",provider="openai",model=config.model,file_path=str(path),prompt_hash=input_hash(prompt))


@pytest.mark.parametrize("fail",[False,True])
def test_prop_uses_openai_preserves_revisions_and_records_openai_cost(world,monkeypatch,fail):
    media = LibraryMedia(world.manager,world.project_id)
    key = "prop:compass:open"
    media.image(key,"Old compass")
    state = media.load()
    old = state["visuals"][key][0]
    old["asset"].update(provider="runway",model="gen4_image")
    media.save(state)
    original = media.assets.local_path(old["asset"]["filePath"])
    provider = PropImages(fail)
    media.assets.demo = False
    media.assets.panorama_image_provider = provider
    class NoRunway:
        def generate(self,*args,**kwargs):
            raise AssertionError("Props must not call Runway")
    media.assets.image_provider = NoRunway()
    world.manager.settings.image_cost_per_image = Decimal(".20")
    world.manager.settings.runway_image_cost_per_image = Decimal(".99")
    monkeypatch.setattr("story_channel.provider_prompts.compile_prompt",lambda *a,**kw:(_ for _ in ()).throw(AssertionError("Do not shorten OpenAI props for Runway")))
    prompt = "Detailed prop design. "*100
    if fail:
        with pytest.raises(RuntimeError,match="OpenAI prop image failed"):
            media.image(key,prompt)
    else:
        image = media.image(key,prompt)
        assert image.provider == "openai" and image.model == PROP_IMAGE_MODEL
    assert len(provider.calls) == 1
    text,refs,config = provider.calls[0]
    assert prompt in text
    assert "uniform pure white (#FFFFFF) background" in text
    assert "Show ONLY the requested prop in its specified mode" in text
    assert config.provider == "openai" and config.model == PROP_IMAGE_MODEL
    assert config.options["size"] == "1024x1024"
    record = CostTracker(world.manager,world.project_id).records()[-1]
    assert record.provider == "openai" and record.model == PROP_IMAGE_MODEL
    assert record.cost == Decimal(".20")
    assert record.outcome == ("failed" if fail else "succeeded")
    versions = media.load()["visuals"][key]
    assert len(versions) == (1 if fail else 2)
    assert versions[0]["asset"]["provider"] == "runway" and original.is_file()


def test_prop_white_background_rule_is_applied_after_style_references(world,monkeypatch):
    media = LibraryMedia(world.manager,world.project_id)
    provider = PropImages()
    media.assets.panorama_image_provider = provider
    monkeypatch.setattr("story_channel.style_rules.style_reference",
        lambda engine,state,prompt,refs:(prompt+"\nStyle reference: dark office scene with people",refs))
    media.image("prop:compass:open","A compass on a wooden desk")
    text = provider.calls[0][0]
    assert text.index("MANDATORY PROP PRESENTATION") > text.index("Style reference: dark office scene with people")
    assert "overrides conflicting background, scene or composition directions above" in text
    assert "never copy their backgrounds or additional objects" in text
    assert "MANDATORY PROP PRESENTATION" in media.load()["visuals"]["prop:compass:open"][0]["prompt"]


def test_prop_resolution_ui_defaults_to_square_and_can_generate_high_resolution(world,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from story_channel.prop_drafts import save
    media = LibraryMedia(world.manager,world.project_id)
    save(media,"compass","Compass","Brass compass",["default"],expected=input_hash(None))
    provider = PropImages()
    media.assets.panorama_image_provider = provider
    def start(media,episode_id,pid,callback,*args,**kwargs):
        callback()
    monkeypatch.setattr("story_channel.check_prop_drafts_ui.start",start)
    def display(media):
        from story_channel.check_prop_drafts_ui import render_library
        render_library(media)
    app = AppTest.from_function(display,args=(media,)).run()
    app.session_state[f"asset-prop-editor-{world.project_id}-compass"] = True
    app.run()
    resolution = next(widget for widget in app.selectbox if widget.label == "Image resolution")
    assert resolution.value == "1024x1024"
    resolution.select("1536x1024").run()
    next(button for button in app.button if button.label == "Generate mode visual").click().run()
    assert not app.exception
    assert provider.calls[0][2].options["size"] == "1536x1024"
    assert media.load()["visuals"]["prop:compass:default"][0]["asset"]["generationParams"]["requestedSize"] == "1536x1024"


def test_prop_ui_queues_openai_even_when_shared_image_setting_is_runway(world,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from story_channel.prop_drafts import save
    from story_channel.files import input_hash
    media = LibraryMedia(world.manager,world.project_id)
    save(media,"compass","Compass","A blue brass compass",["closed"],expected=input_hash(None))
    media.assets.demo = False
    captured = []
    monkeypatch.setattr("story_channel.check_prop_drafts_ui.start",lambda *args,**kwargs:captured.append(kwargs))
    def display(media):
        from story_channel.check_prop_drafts_ui import render_library
        render_library(media)
    app = AppTest.from_function(display,args=(media,)).run()
    app.session_state[f"asset-prop-editor-{world.project_id}-compass"] = True
    app.run()
    assert not app.exception
    assert world.manager.settings.image_provider == "runway"
    assert any("Prop images: OpenAI / "+PROP_IMAGE_MODEL in text.value for text in app.caption)
    next(button for button in app.button if button.label == "Generate mode visual").click().run()
    assert not app.exception
    assert captured[0]["service"] == "openai"
