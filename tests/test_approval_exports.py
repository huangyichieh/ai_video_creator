import json

from story_channel.approval_exports import json_markdown, readable_export, redundant_json_sources
from story_channel.markdown_exports import readable_text


def test_export_preserves_nested_fields_and_refreshes_without_editing_source(tmp_path):
    source = tmp_path / "world.json"
    source.write_text(json.dumps({"era": "Future", "rules": ["Keep memories"],
        "nested": {"importantFlag": False, "detail": None}}), encoding="utf-8")
    original = source.read_bytes()
    destination = readable_export(source)
    assert destination == tmp_path / "world.md"
    text = destination.read_text(encoding="utf-8")
    for expected in ("Future", "Keep memories", "Important flag", "No", "Not set"):
        assert expected in text
    assert source.read_bytes() == original
    source.write_text('{"era": "Present"}', encoding="utf-8")
    readable_export(source)
    assert "Present" in destination.read_text(encoding="utf-8")
    assert "Future" not in destination.read_text(encoding="utf-8")


def test_dialogue_export_preserves_separate_speaker_lines_and_extra_details():
    text = json_markdown({"dialogue": [
        {"characterId": "wife_01", "text": "Let me help.", "emotion": "warm"},
        {"characterId": "husband_01", "text": "Thank you."},
    ]}, "Script")
    assert "wife_01: Let me help." in text
    assert "husband_01: Thank you." in text
    assert "warm" in text
    assert "```json" not in text


def test_combined_character_bible_hides_only_complete_duplicate_records(tmp_path):
    husband = {"characterId": "husband_01", "name": "Husband", "appearance": "Blue coat"}
    wife = {"characterId": "wife_01", "name": "Wife", "appearance": "Red coat"}
    files = [tmp_path / name for name in ("characters.json", "husband.json", "wife.json", "unique.json")]
    for path, data in zip(files, ({"characters": [husband, wife]}, husband, wife, {**wife, "appearance": "Green coat"})):
        path.write_text(json.dumps(data), encoding="utf-8")
    assert redundant_json_sources(files) == {files[1], files[2]}
    # An exact duplicate of the combined package still leaves one copy visible.
    duplicate = tmp_path / "copy.json"
    duplicate.write_bytes(files[0].read_bytes())
    assert redundant_json_sources([*files, duplicate]) == {files[1], files[2], duplicate}


def test_readable_text_separates_sentences_speakers_and_preserves_paragraphs():
    assert readable_text("房門打開了。妻子走進來！丈夫回頭？") == "房門打開了。  \n妻子走進來！  \n丈夫回頭？"
    assert readable_text("Dr. Chen arrived. He waited! Then left.") == "Dr. Chen arrived.  \nHe waited!  \nThen left."
    assert readable_text("wife_01: 我教你。 husband_01: 我明白。") == "wife_01: 我教你。  \nhusband_01: 我明白。"
    text = 'He paid 3.14 USD. See https://example.com/file.json\n\nNext paragraph.'
    formatted = readable_text(text)
    assert "3.14" in formatted and "https://example.com/file.json" in formatted
    assert "\n\nNext paragraph." in formatted
    assert readable_text(formatted) == formatted


def test_long_json_values_get_visible_line_breaks_without_changing_source(tmp_path):
    source = tmp_path / "review_report.json"
    source.write_text(json.dumps({"summary": "先確認設定。再調整節奏。", "issues": ["One issue. Another detail."]}), encoding="utf-8")
    original = source.read_bytes()
    text = readable_export(source).read_text(encoding="utf-8")
    assert "先確認設定。  \n再調整節奏。" in text
    assert "- One issue.  \n  Another detail." in text
    assert source.read_bytes() == original
