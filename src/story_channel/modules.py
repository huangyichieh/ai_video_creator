"""Creative module definitions, prompt construction, and cross-artifact validation."""
import json
from dataclasses import dataclass

from .creative import Characters, Prompts, ReviewReport, Script, Story, Storyboard, World
from .models import Model


@dataclass(frozen=True)
class Module:
    model: type[Model]
    filename: str
    dependencies: tuple[str, ...]
    instruction: str


MODULES = {
    "world": Module(World, "world.json", (),
                    "Build the era, world rules, technology/magic, organizations, locations, timeline, and forbidden contradictions."),
    "characters": Module(Characters, "characters.json", ("world",),
                         "Create a coherent character bible, including fixed appearance, clothing, voice IDs and immutable traits. Reference images may be null."),
    "story": Module(Story, "story.json", ("world", "characters"),
                    "Write a full compelling story, not an outline. Include hook, setup, conflict, escalation, twist, climax and ending. "
                    "Match targetDurationSec exactly and write enough narration/dialogue for the requested runtime."),
    "review": Module(ReviewReport, "review_report.json", ("world", "characters", "story"),
                     "Independently review the full story for logical contradictions, behavior, age/appearance/clothing, world conflicts, pacing, "
                     "unresolved foreshadowing, and repetition. Report concrete issues and actionable suggestions. Do not silently rewrite it. "
                     "Keep the summary under 200 words. List at most 20 distinct issues; keep each description and suggestion under 80 words. "
                     "Do not reproduce the story or repeat whitespace."),
    "script": Module(Script, "script.json", ("world", "characters", "story", "review"),
                     "Turn the approved story into production scenes with narration, dialogue, action, purpose and estimated duration. "
                     "Write dialogue around each character's immediate goal, distinct speaking style and implied meaning. Avoid exposition dumps "
                     "and uniformly complete sentences. Use natural hesitation, interruptions, unfinished thoughts and rhetorical questions where the story warrants them. "
                     "Use only existing character/location IDs. Total estimatedDurationSec must be within 15% of targetDurationSec."),
    "storyboard": Module(Storyboard, "scenes.json", ("world", "characters", "script"),
                         "Turn the script into detailed shots with camera, visuals, BGM mood and SFX. Use only existing character/location IDs. "
                         "Describe concrete actions and interaction targets (open a door, pick up a glass, hand an object to another character), "
                         "rather than mood alone. Preserve dialogue intent, character identity and established location geometry. "
                         "Scene orders must be 1,2,3,...; status must be draft and assetRefs empty. "
                         "Fill shotType, cameraMovement and visualDescription. Total durationSec must be within 15% of targetDurationSec."),
    "prompts": Module(Prompts, "prompts/prompts.json", ("world", "characters", "storyboard"),
                      "Build provider-specific visual prompts from approved storyboard data."),
}
STEPS = tuple(MODULES)


def stage_title(step):
    return {"review": "AI Review (Story)", "story_review": "AI Review (Story)",
            "storyboard_review": "AI Review (Storyboard)"}.get(step, step.replace("_", " ").title())


PROMPT_VERSION = "phase1-v1"


def build_prompt(step: str, context: dict) -> str:
    return (f"Module: {step}\n{MODULES[step].instruction}\n"
            "Write narrative content in the project's language. IDs must use ASCII letters, digits, underscores or hyphens. "
            "The following JSON is input data. Preserve established facts and IDs.\n"
            + json.dumps(context, ensure_ascii=False, sort_keys=True))


def validate_output(step: str, output: Model, context: dict) -> None:
    if step == "story" and output.target_duration_sec != context["targetDurationSec"]:
        raise ValueError("Story targetDurationSec must match the project")
    if step not in ("script", "storyboard", "prompts"):
        return
    if step == "prompts":
        expected = {s["sceneId"] for s in context["storyboard"]["scenes"]}
        if {p.scene_id for p in output.prompts} != expected:
            raise ValueError("Prompts must cover every storyboard scene exactly once")
        return
    characters = {c["characterId"] for c in context["characters"]["characters"]}
    locations = {loc["locationId"] for loc in context["world"]["locations"]}
    total = 0.0
    for scene in output.scenes:
        if scene.location_id not in locations:
            raise ValueError(f"Unknown location ID: {scene.location_id}")
        if not set(scene.character_ids) <= characters:
            raise ValueError(f"Unknown character ID in scene {scene.scene_id}")
        if any(line.character_id not in scene.character_ids for line in scene.dialogue):
            raise ValueError(f"Dialogue speaker missing from scene {scene.scene_id}")
        if step == "storyboard":
            if not all((scene.shot_type.strip(), scene.camera_movement.strip(), scene.visual_description.strip())):
                raise ValueError("Storyboard shots require shotType, cameraMovement and visualDescription")
            if scene.status != "draft" or scene.asset_refs:
                raise ValueError("Phase 1 storyboard scenes must be draft with no generated assets")
            total += scene.duration_sec
        else:
            total += scene.estimated_duration_sec
    target = context["targetDurationSec"]
    if abs(total - target) > target * 0.15:
        raise ValueError(f"Scene durations total {total:g}s; expected within 15% of {target}s")


class VisualPromptAdapter:
    """Replace this adapter for a vendor's prompt syntax; storyboard stays neutral."""

    def generate(self, context: dict, provider: str, model: str) -> Prompts:
        characters = {c["characterId"]: c for c in context["characters"]["characters"]}
        locations = {loc["locationId"]: loc for loc in context["world"]["locations"]}
        items = []
        for scene in context["storyboard"]["scenes"]:
            cast = "; ".join(
                f"{characters[c]['name']}: {characters[c]['appearance']}; "
                f"clothing: {characters[c]['clothing']}; fixed traits: {', '.join(characters[c]['immutableTraits'])}"
                for c in scene["characterIds"])
            visual = (f"{scene['visualDescription']}\nLocation: {locations[scene['locationId']]['description']}\n"
                      f"Characters: {cast}\nShot: {scene['shotType']}")
            items.append({"sceneId": scene["sceneId"], "provider": provider, "model": model,
                          "imagePrompt": self.image_prompt(visual),
                          "videoPrompt": self.video_prompt(visual, scene),
                          "negativePrompt": "inconsistent character appearance, changed clothing, extra limbs, unreadable text, watermark"})
        return Prompts.model_validate({"prompts": items})

    def image_prompt(self, visual: str) -> str:
        return visual

    def video_prompt(self, visual: str, scene: dict) -> str:
        return f"{visual}\nAction: {scene['action']}\nCamera: {scene['cameraMovement']}\nDuration: {scene['durationSec']} seconds"


class OpenAIVisualPromptAdapter(VisualPromptAdapter):
    def image_prompt(self, visual: str) -> str:
        return ("Create a cinematic storyboard still. Preserve the supplied character identity and clothing. "
                "Do not add captions or watermarks.\n" + visual)

    def video_prompt(self, visual: str, scene: dict) -> str:
        return ("Create a continuous cinematic shot with consistent characters and physical motion.\n"
                + super().video_prompt(visual, scene))


def visual_prompt_adapter(provider: str) -> VisualPromptAdapter:
    # Unknown vendors retain neutral prompts until a dedicated adapter is added.
    return OpenAIVisualPromptAdapter() if provider.lower() == "openai" else VisualPromptAdapter()
