from types import SimpleNamespace

from story_channel.action_labels import action_label, asset_modify_label, text_action_label
from story_channel.generation_preferences import preset, save_preferences
from test_pipeline import flow


def test_text_actions_show_actual_openai_model_in_demo_and_saved_projects(flow):
    assert text_action_label("Regenerate", flow, "story") == "Regenerate (OpenAI / gpt-6.1-sol)"
    choices = preset("Economic")
    choices["story"] = "gpt-6-astra"
    save_preferences(flow.manager, flow.project_id, choices, demo=True)
    assert text_action_label("Modify", flow, "story") == "Modify (OpenAI / gpt-6-astra)"
    assert text_action_label("Modify", flow, "outline") == "Modify (OpenAI / gpt-6-luna)"


def test_asset_actions_show_media_provider_instead_of_source_llm():
    engine = SimpleNamespace(demo=False, settings=SimpleNamespace(tts_provider="elevenlabs", tts_model="eleven_v4"))
    assert asset_modify_label(engine, "voices") == "Modify (ElevenLabs / eleven_v4)"
    assert asset_modify_label(engine, "backgrounds") == "Modify (OpenAI / gpt-image-2.5-sunburst)"
    assert asset_modify_label(engine, "scene_images") == "Modify (OpenAI / gpt-image-2.5-sunburst)"
    engine.demo = True
    assert asset_modify_label(engine, "voices") == "Modify (OpenAI / gpt-4o-mini-tts)"
    assert action_label("Modify", "runway", "act_two") == "Modify (Runway / act_two)"
