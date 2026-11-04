"""Per-process OpenAI choices, separate from models pinned to existing artifacts."""
from .models import ProviderConfig
from .locking import project_lock

MODELS = {
    "gpt-6.1-sol": "GPT-6.1 Sol",
    "gpt-6-astra": "GPT-6 Astra",
    "gpt-6-luna": "GPT-6 Luna",
    "gpt-5.6-sol": "GPT-5.6 Sol",
}

# Process IDs are durable configuration keys; generation stages map to them below.
PROCESSES = {
    "world": "World settings generation",
    "world_review": "World settings / additional settings review",
    "canon": "Canon conflict detection",
    "characters": "Character generation",
    "outline": "Story outline / outline review",
    "story": "Full story generation",
    "story_review": "Final story quality review",
    "script": "Dialogue writing / Script",
    "dialogue_review": "Dialogue review",
    "storyboard": "Storyboard / Shot planning",
    "storyboard_review": "Storyboard review",
    "prompts": "Image / video prompt writing",
    "performance": "Performance metadata / direction (emotion, gesture, bodyAction, cameraReaction)",
    "metadata": "Metadata / tags / filenames / summaries",
    "proofreading": "Typo checks",
    "voice_design": "Voice design prompts",
    "location_layout": "Background floor plan / object layout",
    "style_check": "Character / background style check",
    "sound_description": "SFX / ambience descriptions",
    "asset_edit": "Character / background / prop / costume detail edits",
    "animation_edit": "Animation / finishing plan edits",
    "other_edit": "Other content edits",
}

MODEL_HELP = """GPT-6.1 Sol:
- Best balance of quality / cost / latency
- More expensive than Luna

GPT-6 Astra:
- Highest reasoning quality
- Much more expensive and slower

GPT-6 Luna:
- Very cheap and fast
- Weaker for nuanced storytelling and long-range consistency

GPT-5.6 Sol: Mature and still capable"""


def preset(name="Balance"):
    if name not in ("Economic", "Balance"):
        raise ValueError("Unknown generation preset")
    return {process: ("gpt-6-luna" if name == "Economic" or process == "metadata" else "gpt-6.1-sol")
            for process in PROCESSES}


def process_for(stage):
    stage = stage.removeprefix("modify:")
    if stage.startswith("typo_check:"):
        return "proofreading"
    if stage.startswith("voice_design"):
        return "voice_design"
    if "consistency" in stage or "conflict" in stage:
        return "canon"
    if stage.startswith("world") and ("review" in stage or "structure" in stage):
        return "world_review" if "review" in stage else "metadata"
    if stage.startswith("episode_"):
        return {"outline": "outline", "story": "story_review", "script": "dialogue_review",
                "storyboard": "storyboard_review", "prompts": "prompts"}.get(stage.removeprefix("episode_").removesuffix("_review"), "other_edit")
    if stage.startswith("ai_review_"):
        return "storyboard_review" if "storyboard" in stage else "story_review"
    if stage == "review":
        return "story_review"
    if stage.startswith(("scene_direction", "voice_delivery")):
        return "performance"
    if stage.startswith(("animation_plan", "finishing_plan")):
        return "animation_edit"
    if stage.startswith(("sound_", "sfx_")):
        return "sound_description"
    if stage.startswith(("compact_", "provider_prompt")):
        return "prompts"
    if stage.startswith(("character_references:", "backgrounds:", "scene_images:", "costume", "prop")):
        return "asset_edit"
    return stage if stage in PROCESSES else "other_edit"


def preferences(manager, project_id):
    manifest = manager.load(project_id)
    owner = manager.load(manifest.world_project_id) if manifest.world_project_id else manifest
    return {process: owner.provider_config.get("openai:" + process,
            ProviderConfig(provider="openai", model=model)).model for process, model in preset().items()}


def openai_config(pipeline, stage, *, force=False):
    if pipeline.demo and not force:
        return pipeline.config
    model = preferences(pipeline.manager, pipeline.project_id)[process_for(stage)]
    return ProviderConfig(provider="openai", model=model,
                          options={"max_output_tokens": pipeline.settings.llm_max_output_tokens})


def save_preferences(manager, project_id, models, *, demo=False):
    if set(models) != set(PROCESSES) or any(model not in MODELS for model in models.values()):
        raise ValueError("Choose a supported model for every process.")
    manifest = manager.load(project_id)
    owner = manifest.world_project_id or project_id
    with project_lock(manager.project_path(owner)):
        manifest = manager.load(owner)
        for process, model in models.items():
            manifest.provider_config["openai:" + process] = ProviderConfig(provider="openai", model=model)
        old = manifest.provider_config.get("llm")
        manifest.provider_config["llm"] = ProviderConfig(provider="demo" if demo else "openai",
            model="demo-v1" if demo else (old.model if old and old.provider == "openai" else "gpt-6.1-sol"),
            options={"max_output_tokens": manager.settings.llm_max_output_tokens})
        manager.save(manifest)
