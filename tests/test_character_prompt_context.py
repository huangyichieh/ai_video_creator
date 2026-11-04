import json
from copy import deepcopy

from story_channel.assets import AssetPipeline
from story_channel.character_prompt_context import visual_context, voice_context
from story_channel.creative import Character
from story_channel.voice_design import voice_design_context
from test_studio import world


def character():
    return Character(character_id="private_character_id", name="Private name",
        age=37, appearance="Tall with blue eyes and short black hair", clothing="A green jacket and brown boots",
        personality="Reserved but kind", background="SECRET BACKSTORY NOT FOR MEDIA PROMPTS",
        speaking_style="Measured, quiet, clear speech", voice_id="private_provider_voice", reference_image="private.png",
        immutable_traits=["Blue eyes and always speaks softly", "Loyal to friends", "左臉有疤；對朋友忠誠", "Always wears round glasses"])


def test_visual_payload_omits_lore_ids_voice_fields_and_nonvisible_immutable_clauses():
    result = visual_context(character())
    assert set(result) == {"age", "appearance", "clothing", "personality", "immutableTraits"}
    assert result["immutableTraits"] == ["Blue eyes", "左臉有疤", "Always wears round glasses"]
    assert "SECRET BACKSTORY" not in json.dumps(result)
    changed = character().model_copy(update={"immutable_traits": ["Loyal to friends", "Always speaks softly"]})
    assert "immutableTraits" not in visual_context(changed)


def test_voice_payload_has_exactly_age_personality_and_speaking_style():
    assert voice_context(character()) == {"age": 37, "personality": "Reserved but kind",
                                           "speakingStyle": "Measured, quiet, clear speech"}


def test_visual_provider_receives_minimal_fields_and_voice_context_is_separate(world):
    world.generate_characters()
    original = world.pipeline.artifact("characters").characters[0]
    edited = character().model_copy(update={"character_id": original.character_id, "voice_id": original.voice_id})
    world.pipeline.edit_character(original.character_id, edited.model_dump_json(by_alias=True), expected=original.model_dump_json(by_alias=True))
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    calls = []
    generate = engine.image_provider.generate
    def capture(prompt, references, config):
        calls.append(prompt)
        return generate(prompt, references, config)
    engine.image_provider.generate = capture
    state = engine.generate("character_references")
    prompt = calls[0]
    data = json.loads(prompt.split("Character visual data:\n", 1)[1])
    assert data == visual_context(edited)
    for excluded in (edited.name, edited.character_id, edited.voice_id, edited.background, edited.speaking_style, "Loyal to friends"):
        assert excluded not in prompt
    context = voice_design_context(world.pipeline, edited.character_id)
    assert context == {"language": world.manager.load(world.project_id).language, "character": voice_context(edited)}
    # Changing a voice-only field leaves the visual task fingerprint intact.
    before = next(t for t in engine.tasks(state) if t.subject == edited.character_id)
    digest = engine._hash(before, state)
    changed = edited.model_copy(update={"speaking_style": "Quick and bright", "background": "Changed unrelated history"})
    world.pipeline.edit_character(edited.character_id, changed.model_dump_json(by_alias=True), expected=edited.model_dump_json(by_alias=True))
    after = next(t for t in engine.tasks(engine.load()) if t.subject == edited.character_id)
    assert engine._hash(after, engine.load()) == digest
    assert voice_design_context(world.pipeline, edited.character_id)["character"]["speakingStyle"] == "Quick and bright"


def test_narrowing_context_keeps_previously_reviewed_images_and_approvals(world, monkeypatch):
    from asset_test_helpers import complete_character_parts
    import story_channel.reference_input_migration as migration
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    with monkeypatch.context() as patch:
        patch.setattr(migration, "narrow_character_context", lambda *args: None)
        state = engine.generate("character_references")
        complete_character_parts(engine)
        state = engine.approve("characters")
        assert state.character_prompt_version == 0
    old = deepcopy(state)
    approval = world.manager.load(world.project_id).asset_approvals["characters"]
    state = engine.inspect()
    assert state.character_prompt_version == 1
    for key, record in old.records.items():
        assert state.records[key].asset.asset_id == record.asset.asset_id
        assert state.records[key].file_hash == record.file_hash
        assert state.records[key].status == "succeeded"
    assert all(engine.character_approved(cid, state) for cid in old.character_approvals)
    assert world.manager.load(world.project_id).asset_approvals["characters"].approved_at == approval.approved_at
