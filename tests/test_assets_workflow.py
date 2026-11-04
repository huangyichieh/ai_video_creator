from story_channel.animation import AnimationDirector
from story_channel.assets import AssetPipeline
from story_channel.assets_workflow import generate_assets_page, identity_checklist, next_assets_action
from story_channel.identity import IdentityManager
from test_assets_ui import prepare


def test_assets_flow_stops_at_every_human_gate_and_finishes_with_voice_auditions(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    engine = AssetPipeline(manager, project.project_id)
    identity = IdentityManager(manager, project.project_id)
    assert next_assets_action(engine, engine.inspect()).stage == "character_references"
    assert generate_assets_page(engine, until_review=True).stage == "character_review"
    assert identity.load().costumes
    assert all(not profile.voice_id for profile in identity.load().voices.values())
    engine.approve("characters")
    assert generate_assets_page(engine, until_review=True).stage == "background_review"
    engine.approve("backgrounds")
    assert generate_assets_page(engine, until_review=True).stage == "voice_settings"
    library = identity.load()
    expected = library.model_dump_json(by_alias=True)
    for profile in library.voices.values():
        profile.voice_id = "nova"
    identity.save(library, expected=expected)
    assert next_assets_action(engine, engine.inspect()).stage == "identity_approval"
    identity.approve()
    assert next_assets_action(engine, engine.inspect()).stage == "voice_profiles"
    samples = []
    original_preview = AnimationDirector.preview_voice
    def preview(self, cid, text, **kwargs):
        samples.append(text)
        return original_preview(self, cid, text, **kwargs)
    monkeypatch.setattr(AnimationDirector, "preview_voice", preview)
    assert generate_assets_page(engine, until_review=True).stage == "voice_approval"
    from story_channel.voice_preview_text import SAMPLES
    assert samples and all(text == SAMPLES["zh-Hant"] for text in samples)
    director = AnimationDirector(manager, project.project_id)
    for cid in director.load().plan.voices:
        director.approve_voice(cid)
    assert next_assets_action(engine, engine.inspect()).stage == "complete"
    state = engine.load()
    assert {record.stage for record in state.records.values()} == {"character_references", "backgrounds"}
    rows = {row["Part"]: row for row in identity_checklist(engine, state)}
    assert "Approved" in rows["Character References"]["Status"]
    assert "no separate image generation" in rows["Costumes"]["Next action"]
    assert "1/1 voice IDs configured" == rows["Voice Profiles"]["Status"]
    assert "1/1 approved" in rows["Voice auditions"]["Status"]


def test_voice_profile_setup_does_not_generate_audio_until_the_next_step(tmp_path):
    manager, project = prepare(tmp_path)
    engine = AssetPipeline(manager, project.project_id)
    identity = IdentityManager(manager, project.project_id)
    library = identity.prepare()
    expected = library.model_dump_json(by_alias=True)
    for profile in library.voices.values():
        profile.voice_id = "nova"
    identity.save(library, expected=expected)
    engine.generate("character_references")
    engine.approve("characters")
    engine.generate("backgrounds")
    engine.approve("backgrounds")
    identity.approve()
    generate_assets_page(engine)
    assert AnimationDirector(manager, project.project_id).load().previews == {}
    assert next_assets_action(engine, engine.inspect()).stage == "voice_previews"


def test_assets_buttons_continue_into_voice_profiles_and_stop_for_approval(tmp_path, monkeypatch):
    from test_identity_ui import open_assets
    from test_ui import finish_background
    manager, project = prepare(tmp_path)
    identity = IdentityManager(manager, project.project_id)
    library = identity.prepare()
    expected = library.model_dump_json(by_alias=True)
    for profile in library.voices.values():
        profile.voice_id = "nova"
    identity.save(library, expected=expected)
    engine = AssetPipeline(manager, project.project_id)
    engine.generate("character_references")
    engine.approve("characters")
    engine.generate("backgrounds")
    engine.approve("backgrounds")
    identity.approve()
    app = open_assets(manager, monkeypatch)
    button = app.button(key="generate-next-asset-stage")
    assert button.label.endswith("Set up Voice Profiles") and not button.disabled
    button.click().run()
    finish_background(app)
    assert app.button(key="generate-next-asset-stage").label.endswith("Voice auditions")
    next(button for button in app.button if button.label == "Generate assets until review").click().run()
    finish_background(app)
    button = app.button(key="generate-next-asset-stage")
    assert button.label.endswith("Approve Voice Profiles") and not button.disabled
    assert any("Voice approval: Not approved" in item.value for item in app.markdown)
    button.click().run()
    assert app.session_state[f"page-tab-assets-{project.project_id}"] == "Voice Profiles"
    assert "workspace-task" not in app.session_state
    director = AnimationDirector(manager, project.project_id)
    for cid, preview in director.load().previews.items():
        app.checkbox(key=f"animation-voice-check-{project.project_id}-{cid}-{preview.file_hash}").check().run()
        app.button(key=f"animation-voice-approve-{project.project_id}-{cid}").click().run()
        finish_background(app)
    assert "Assets ready" in app.button(key="continue-assets-to-animations").label
    assert not app.button(key="continue-assets-to-animations").disabled
    assert any("Voice approval: Approved" in item.value for item in app.markdown)
    app.button(key="continue-assets-to-animations").click().run()
    assert not app.exception and app.session_state["workspace_page"] == "Animations"
    assert {record.stage for record in engine.load().records.values()} == {"character_references", "backgrounds"}
    assert not app.exception
