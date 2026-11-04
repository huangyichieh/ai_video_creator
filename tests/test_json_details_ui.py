from pathlib import Path

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.json_details_ui import detail_rows
from test_studio import world


def test_nested_json_fields_remain_readable_and_preserve_values():
    rows = dict(detail_rows({"characterId": "keeper", "fixed_traits": ["Brown eyes", "Blue coat"],
                             "dialogue": [{"text": "Hello\nthere"}], "enabled": False, "referenceImage": None}))
    assert rows["Character id"] == "keeper"
    assert rows["Fixed traits"] == "Brown eyes\nBlue coat"
    assert rows["Dialogue 1 › Text"] == "Hello\nthere"
    assert rows["Enabled"] == "No"
    assert rows["Reference image"] == "Not set"


def test_generation_details_default_to_compact_preview_and_keep_json_edits(world, monkeypatch):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    engine.set_generic_prompt("Watercolor")
    engine.generate("character_references")
    character = world.pipeline.artifact("characters").characters[0]
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    prefix = f"asset-character-{world.project_id}-{character.character_id}"
    app.session_state[prefix + "-details-panel"] = True
    app.run()
    assert not any(area.key == prefix + "-draft" for area in app.text_area)
    assert any('<dl class="story-json-details">' in item.value for item in app.markdown)
    assert not app.toggle(key=prefix + "-metadata-json").value
    reference_button = f"edit-character-reference-{world.project_id}-{character.character_id}"
    assert not app.button(key=reference_button).disabled
    assert len(app.get("file_uploader")) >= 1
    app.button(key=reference_button).click().run()
    assert not app.exception
    assert not app.toggle(key=prefix + "-edit-mode").value
    assert app.button(key=reference_button).label == "Done editing reference"
    upload_keys = [field.proto.id for field in app.get("file_uploader")]
    assert len(upload_keys) == len(set(upload_keys))
    app.toggle(key=prefix + "-edit-mode").set_value(True).run()
    assert app.session_state[prefix + "-details-panel"]
    assert not any(area.label == "Character JSON" for area in app.text_area)
    assert not any(field.label == "Character ID" for field in app.text_input)
    app.text_area(key=prefix + "-field-personality").set_value("Curious <script>alert('unsafe')</script>").run()
    app.toggle(key=prefix + "-edit-mode").set_value(False).run()
    previews = [item.value for item in app.markdown if '<dl class="story-json-details">' in item.value]
    assert any("Curious &lt;script&gt;" in text for text in previews)
    assert all("<script>" not in text for text in previews)
    assert not app.button(key=prefix + "-save").disabled
    app.button(key="nav-world").click().run()
    app.button(key="nav-assets").click().run()
    assert not app.toggle(key=prefix + "-edit-mode").value
    assert not app.button(key=prefix + "-save").disabled
    app.toggle(key=prefix + "-edit-mode").set_value(True).run()
    app.text_input(key=prefix + "-field-name").set_value("").run()
    assert app.button(key=prefix + "-save").disabled
    assert app.button(key=prefix + "-check").disabled
    app.button(key=prefix + "-revert").click().run()
    assert app.text_area(key=prefix + "-field-personality").value == character.personality
    app.toggle(key=prefix + "-metadata-json").set_value(True).run()
    assert app.json
    assert not app.exception
