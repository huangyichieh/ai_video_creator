from pathlib import Path
from threading import Event

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.identity import IdentityManager
from story_channel.studio import WorldStudio
from test_studio import world
from test_ui import finish_background


def assets_app(world, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    return app


def test_add_character_box_generates_first_character_then_appends(world, monkeypatch):
    app = assets_app(world, monkeypatch)
    assert any(title.value == "AI Generate characters" for title in app.subheader)
    assert not any(item.label == "Add character manually" for item in app.expander)
    assert not any(item.label == "Character name" for item in app.text_input)
    captured = []
    original = WorldStudio.generate_characters
    def generate(studio, prompt="", *, single=False):
        captured.append((prompt, single))
        return original(studio, prompt, single=single)
    monkeypatch.setattr(WorldStudio, "generate_characters", generate)
    assert not any(button.label == "Add character" for button in app.button)
    assert any(item.label == "Add character" for item in app.expander)
    assert app.button(key="studio-generate-added-character").disabled
    prompt_key = "studio-draft-add-character-prompt-" + world.project_id
    app.text_area(key=prompt_key).set_value("A curious lighthouse keeper").run()
    app.button(key="studio-generate-added-character").click().run()
    finish_background(app)
    assert not app.exception
    character = world.pipeline.artifact("characters").characters[0]
    assert len(world.pipeline.artifact("characters").characters) == 1
    assert not any(title.value == "AI Generate characters" for title in app.subheader)
    assert not any(button.key == "studio-add-character" for button in app.button)
    assert any(item.label == "Add character" for item in app.expander)
    assert character.character_id in IdentityManager(world.manager, world.project_id).load().voices
    assert captured == [("A curious lighthouse keeper", True)]
    assert app.text_area(key=prompt_key).value == ""
    app.text_area(key=prompt_key).set_value("A visiting sailor").run()
    app.button(key="studio-generate-added-character").click().run()
    finish_background(app)
    assert not app.exception
    assert len(world.pipeline.artifact("characters").characters) == 2
    assert world.pipeline.artifact("characters").characters[0] == character
    assert captured[-1] == ("A visiting sailor", True)


def test_single_character_generation_rejects_multiple_results_without_changing_cast(world, monkeypatch):
    world.generate_characters()
    existing = world.pipeline.artifact("characters")
    from story_channel.creative import Characters
    output = Characters(characters=[existing.characters[0].model_copy(update={"character_id": cid}) for cid in ("new_01", "new_02")])
    monkeypatch.setattr(world.pipeline, "_generate", lambda *args: output)
    with pytest.raises(ValueError, match="more than one"):
        world.generate_characters("A guest", single=True)
    assert world.pipeline.artifact("characters") == existing
    with pytest.raises(ValueError, match="character prompt"):
        world.generate_characters("", single=True)


def test_failed_add_character_keeps_prompt_for_retry(world, monkeypatch):
    app = assets_app(world, monkeypatch)
    def fail(*args, **kwargs):
        raise ValueError("Synthetic character generation failure")
    monkeypatch.setattr(WorldStudio, "generate_characters", fail)
    prompt_key = "studio-draft-add-character-prompt-" + world.project_id
    app.text_area(key=prompt_key).set_value("A visiting sailor").run()
    app.button(key="studio-generate-added-character").click().run()
    finish_background(app)
    assert not app.exception
    assert app.text_area(key=prompt_key).value == "A visiting sailor"
    assert not app.button(key="studio-generate-added-character").disabled
    assert any("Synthetic character generation failure" in error.value for error in app.error)


@pytest.mark.parametrize("kind", ["initial", "single"])
def test_generation_changes_button_label_and_uses_prompt(world, monkeypatch, kind):
    app = assets_app(world, monkeypatch)
    started, release = Event(), Event()
    original = WorldStudio.generate_characters
    captured = []
    def wait_generate(studio, prompt="", *, single=False):
        captured.append((prompt, single))
        started.set()
        if not release.wait(timeout=20):
            raise RuntimeError("Test did not release worker")
        return original(studio, prompt, single=single)
    monkeypatch.setattr(WorldStudio, "generate_characters", wait_generate)
    try:
        prompt_key = ("studio-draft-new-character-" if kind == "initial" else "studio-draft-add-character-prompt-") + world.project_id
        button_key = "studio-add-character" if kind == "initial" else "studio-generate-added-character"
        app.text_area(key=prompt_key).set_value("A curious young lighthouse keeper").run()
        app.button(key=button_key).click().run()
        assert started.is_set()
        assert not app.exception
        assert app.button(key=button_key).disabled
        assert app.button(key=button_key).label == "Generating…"
        assert not app.status
        assert captured == [("A curious young lighthouse keeper", kind == "single")]
    finally:
        release.set()
        finish_background(app)
    assert not app.exception
    assert world.pipeline.artifact("characters").characters
    assert not any(button.key == button_key and button.label == "Generating…" for button in app.button)
