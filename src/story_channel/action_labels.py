"""Display the provider/model used by an action rather than its historical name."""


def action_label(action, provider, model):
    service = {"openai": "OpenAI", "runway": "Runway", "elevenlabs": "ElevenLabs",
               "demo": "Demo", "local": "Local"}.get(provider, provider)
    return f"{action} ({service} / {model})"


def text_action_label(action, pipeline):
    # Explicit regenerate/modify actions use OpenAI, including in Demo mode.
    model = pipeline.settings.llm_model if pipeline.demo else pipeline.config.model
    return action_label(action, "openai", model)


def asset_modify_label(engine, stage):
    if stage == "voices":
        if not engine.demo and engine.settings.tts_provider == "elevenlabs":
            return action_label("Modify", "elevenlabs", engine.settings.tts_model)
        return action_label("Modify", "openai", "gpt-4o-mini-tts")
    return action_label("Modify", "openai", "gpt-image-1")
