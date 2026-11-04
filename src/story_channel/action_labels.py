"""Display the provider/model used by an action rather than its historical name."""


def action_label(action, provider, model):
    service = {"openai": "OpenAI", "runway": "Runway", "elevenlabs": "ElevenLabs",
               "demo": "Demo", "local": "Local"}.get(provider, provider)
    return f"{action} ({service} / {model})"


def text_action_label(action, pipeline, stage="other_edit"):
    # Explicit regenerate/modify actions use OpenAI, including in Demo mode.
    from .generation_preferences import openai_config
    return action_label(action, "openai", openai_config(pipeline, stage, force=True).model)


def asset_modify_label(engine, stage, *, subject=None):
    if subject is not None and stage in ("character_references", "backgrounds"):
        from .visual_generation import VisualGeneration, EDIT_MODEL
        if VisualGeneration(engine).key(stage, subject) in engine.load().generation_inputs:
            return action_label("Modify", "openai", EDIT_MODEL)
    if stage == "voices":
        if not engine.demo and engine.settings.tts_provider == "elevenlabs":
            return action_label("Modify", "elevenlabs", engine.settings.tts_model)
        return action_label("Modify", "openai", "gpt-4o-mini-tts")
    from .visual_generation import EDIT_MODEL
    return action_label("Modify", "openai", EDIT_MODEL)
