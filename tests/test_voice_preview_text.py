from types import SimpleNamespace

import pytest

from story_channel.voice_preview_text import preview_sample_text, SAMPLES


@pytest.mark.parametrize("language,expected", [("zh-TW", "zh-Hant"), ("zh_Hant", "zh-Hant"), ("繁體中文", "zh-Hant"),
    ("zh-CN", "zh-Hans"), ("简体中文", "zh-Hans"), ("en-US", "en"), ("English", "en"), ("ja-JP", "ja"), ("ko", "ko")])
def test_samples_follow_language(language, expected):
    assert preview_sample_text(language) == SAMPLES[expected]


def test_other_languages_use_only_the_selected_characters_dialogue():
    lines = [SimpleNamespace(character_id="a", text="Привіт, друже."), SimpleNamespace(character_id="b", text="Another voice.")]
    assert preview_sample_text("uk", lines, "a") == "Привіт, друже."
    assert preview_sample_text("uk", lines, "missing") == ""


def test_editor_refreshes_default_and_preserves_custom_sample(tmp_path, monkeypatch):
    from story_channel.animation import AnimationDirector
    from test_assets_ui import prepare
    from test_identity_ui import open_assets
    manager, project = prepare(tmp_path)
    direction = AnimationDirector(manager, project.project_id).prepare()
    cid = next(iter(direction.plan.voices))
    app = open_assets(manager, monkeypatch)
    key = f"animation-preview-text-{project.project_id}-{cid}"
    assert app.text_area(key=key).value == SAMPLES["zh-Hant"]
    project = manager.load(project.project_id)
    project.language = "ja"
    manager.save(project)
    app.run()
    assert not app.exception and app.text_area(key=key).value == SAMPLES["ja"]
    app.text_area(key=key).set_value("自訂的試聽文字，請保留。").run()
    project.language = "en"
    manager.save(project)
    app.run()
    assert not app.exception and app.text_area(key=key).value == "自訂的試聽文字，請保留。"
