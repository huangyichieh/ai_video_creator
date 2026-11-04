from pathlib import Path

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from test_studio import world


def test_each_character_has_own_visual_voice_and_costume_tabs_and_selection(world, monkeypatch):
    world.generate_characters()
    world.generate_characters("Guest")
    characters = world.pipeline.artifact("characters").characters
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    engine.set_generic_prompt("Watercolor")
    engine.generate("character_references")
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Characters"
    app.button(key="nav-assets").click().run()
    assert not app.exception
    assert app.session_state[f"page-tab-assets-{world.project_id}"] == "Character References"
    assert not any(tab.label == "Characters" for tab in app.tabs)
    visuals = [tab for tab in app.tabs if tab.label == "Visual"]
    voices = [tab for tab in app.tabs if tab.label == "Voice"]
    costumes = [tab for tab in app.tabs if tab.label == "Costumes"]
    assert len(visuals) == len(voices) == len(costumes) == len(characters)
    for index, character in enumerate(characters):
        cid = character.character_id
        assert any(button.key == f"download-asset-character_references:{cid}" for button in visuals[index].download_button)
        assert not voices[index].button
        assert not costumes[index].button
        other = characters[1 - index].character_id
        assert not any(button.key == "studio-audio-sample-" + other for button in voices[index].button)
        assert not any(button.key == "studio-costume-" + other + "_default" for button in costumes[index].button)
    first_key = f"page-tab-character-{world.project_id}-{characters[0].character_id}"
    second_key = f"page-tab-character-{world.project_id}-{characters[1].character_id}"
    app.session_state[first_key] = "Voice"
    app.session_state[second_key] = "Costumes"
    app.run()
    assert app.button(key="studio-audio-sample-" + characters[0].character_id)
    assert not any(b.key == f"download-asset-character_references:{characters[0].character_id}" for b in app.button)
    assert not any(b.key == "studio-audio-sample-" + characters[1].character_id for b in app.button)
    app.button(key="nav-world").click().run()
    app.button(key="nav-assets").click().run()
    assert app.session_state[first_key] == "Voice"
    assert app.session_state[second_key] == "Costumes"
    # Assets uses a prerequisite hint, without a next-stage workflow button.
    assert not any(button.key == "studio-assets-next" for button in app.button)
    assert any("Assets prerequisite" in item.value for item in app.caption)
