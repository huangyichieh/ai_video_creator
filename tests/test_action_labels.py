from types import SimpleNamespace

from story_channel.action_labels import action_label, asset_modify_label, text_action_label


def test_text_actions_show_actual_openai_model_in_demo_and_saved_projects():
    pipeline = SimpleNamespace(demo=True, settings=SimpleNamespace(llm_model="configured-live-model"),
                               config=SimpleNamespace(model="demo-v1"))
    assert text_action_label("Regenerate", pipeline) == "Regenerate (OpenAI / configured-live-model)"
    pipeline.demo = False
    pipeline.config.model = "saved-story-model"
    assert text_action_label("Modify", pipeline) == "Modify (OpenAI / saved-story-model)"


def test_asset_actions_show_media_provider_instead_of_source_llm():
    engine = SimpleNamespace(demo=False, settings=SimpleNamespace(tts_provider="elevenlabs", tts_model="eleven_v4"))
    assert asset_modify_label(engine, "voices") == "Modify (ElevenLabs / eleven_v4)"
    assert asset_modify_label(engine, "backgrounds") == "Modify (OpenAI / gpt-image-1)"
    engine.demo = True
    assert asset_modify_label(engine, "voices") == "Modify (OpenAI / gpt-4o-mini-tts)"
    assert action_label("Modify", "runway", "act_two") == "Modify (Runway / act_two)"
