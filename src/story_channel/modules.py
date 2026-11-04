"""Creative module definitions, prompt construction, and cross-artifact validation."""
import json
from dataclasses import dataclass

from .creative import Characters, Prompts, ReviewReport, Script, Story, Storyboard, World
from .storyboard_planning import MINIMAL_ASSETS,ASSET_DESCRIPTIONS
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
                     "Actively direct the audible performance using each dialogue line's audioTags and each scene's narrationAudioTags. "
                     "Use concise English tag names without brackets; keep text/narration as clean spoken words only. "
                     "Draw broadly from these categories when the scene calls for them: emotions (curious, sad, excited, nervous, sarcastically), "
                     "vocalizations/human reactions (laughs, sighs, gasps, clears throat, crying), delivery (whispers, shouts, softly), "
                     "pacing (slowly, hurriedly, hesitantly), character performance/accent consistent with the approved voice and language, "
                     "and sound-effect tags (clapping, applause) when deliberately integrated into that vocal performance. "
                     "Use expressive tags at meaningful beats and vary them across the story; combine compatible cues when useful. "
                     "Do not mechanically force every category onto every line, use conflicting cues, imitate real people, or change established vocal identity. "
                     "Plan concrete Foley, environmental ambience and impactful sound effects in each scene's sfx list; describe source/action/texture "
                     "without dialogue or music. Prefer sfx for standalone environmental effects so they can be generated and mixed separately. "
                     "Never duplicate the same effect in audioTags and sfx. "
                     "Use only existing character/location IDs. Total estimatedDurationSec must be within 15% of targetDurationSec."),
    "storyboard": Module(Storyboard, "scenes.json", ("world", "characters", "script"),
                         MINIMAL_ASSETS+ASSET_DESCRIPTIONS+"Turn the script into detailed shots with camera, visuals, BGM mood and SFX. Use only existing character IDs. "
                         "Reuse registered background and prop IDs whenever possible. If a shot needs a NEW location, declare its locationId, name and description in backgrounds and use that ID in the scene. "
                         "For close-ups or area-specific camera views, prefer a suitable existing detail background from detailBackgrounds[locationId]. "
                         "Set the parent locationId and its stable detailId as targetArea when selecting a detail. If a new detail is essential and no existing master/detail is sufficient, declare a new item in detailBackgrounds "
                         "with locationId, detailId (lowercase letters/digits/underscores, starting with a letter, max 64 chars), area and a concrete description "
                         "of the desired scene-ready view; use that detailId as targetArea. Reuse IDs across shots of the same area. "
                         "Details remain inside the same parent room: preserve geometry, objects, materials, lighting and style. Do not declare a new full location for a local close-up. "
                         "Only DETAIL locations support detail references; locations marked SIMPLE in locationTypes use their single master directly. "
                         "New detail declarations create drafts, not images; reuse existing generated details when suitable and never redefine them. "
                         "Declare newly required physical props in props with propId, title, description and modes; "
                         "list the appropriate propIds on each scene. Include every standalone sound effect in the scene sfx list as a concrete source/action/texture description. "
                         "For EVERY sfx description, add an entry in the scene's sfxTiming list with description (the exact sfx text), durationSec and loop. "
                         "Choose a realistic audible duration for the action, not a fixed default: clicks/ticks/impacts about 0.5–1 seconds; "
                         "doors, swishes and short movements about 1–3 seconds; sustained actions match their action length; "
                         "ambient room tone, wind or rain can span the scene with loop=true. One-shot effects use loop=false. "
                         "Durations must be 0.5–30 seconds and should not exceed the scene duration. Do not pad a short event to fill the scene. "
                         "Reuse registered BGM IDs from assetCatalog.bgm when suitable; only when music is necessary and no existing cue fits, declare new reusable music in bgm with bgmId, title and description (mood, instrumentation, pacing, no dialogue), and set each scene's bgmId. For silence, leave bgmId null and bgmMood empty. Keep bgmMood as scene-specific musical direction otherwise. "
                         "New declarations become pending reusable Assets, not generated or approved media, and must respect approved World canon. "
                         "Describe concrete actions and interaction targets (open a door, pick up a glass, hand an object to another character), "
                         "rather than mood alone. Preserve dialogue intent, character identity and established location geometry. "
                         "Preserve each line's audioTags and the narrationAudioTags from the script; retain its sfx cues in the appropriate shots. "
                         "Scene orders must be 1,2,3,...; status must be draft and assetRefs empty. "
                         "Fill shotType, cameraMovement and visualDescription. Set cameraStartAngle, cameraEndAngle and targetArea for planned views/movements. "
                         "Yaw is clockwise from the location center: 0 front, 90 right, 180 rear, 270 left. "
                         "Use unwrapped angles for an orbit (0 to 360 for a full turn, 0 to -180 counterclockwise); a static shot keeps equal angles. "
                         "Preserve the approved floor plan, object positions and 180-degree axis. Total durationSec must be within 15% of targetDurationSec."),
    "prompts": Module(Prompts, "prompts/prompts.json", ("world", "characters", "storyboard"),
                      "Build provider-specific visual prompts from approved storyboard data."),
}
STEPS = tuple(MODULES)


def stage_title(step):
    return {"review": "AI Review (Story)", "story_review": "AI Review (Story)",
            "storyboard_review": "AI Review (Storyboard)"}.get(step, step.replace("_", " ").title())


PROMPT_VERSION = "phase1-v1"


def build_prompt(step: str, context: dict) -> str:
    extra = (" Generate ONLY new characters requested in characterRequest. Do not return or alter existingCharacters, "
             "and never reuse their IDs. Follow approvedCanon and the established World. "
             if step == "characters" and context.get("appendCharacters") else "")
    if step == "world" and context.get("approvedCanon"):
        extra += (" Extract the concise worldview draft faithfully. Do not invent additional permanent restrictions, "
                  "organizations, history or powers. Use 'Unspecified' for an undecided era and empty lists for "
                  "optional undecided fields. Keep locations limited to those established or directly implied by "
                  "the draft. Missing information is open for later approved additions, not a contradiction.")
    return (f"Module: {step}\n{MODULES[step].instruction}{extra}\n"
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
    if step == "storyboard":
        from .storyboard_details import validate
        validate(output,context)
        locations.update(item.location_id for item in output.backgrounds)
        props = {item.prop_id for item in output.props} | set(context.get("assetCatalog",{}).get("props",{}))
        bgm = {item.bgm_id for item in output.bgm} | set(context.get("assetCatalog",{}).get("bgm",{}))
    total = 0.0
    for scene in output.scenes:
        if scene.location_id not in locations:
            raise ValueError(f"Unknown location ID: {scene.location_id}")
        if not set(scene.character_ids) <= characters:
            raise ValueError(f"Unknown character ID in scene {scene.scene_id}")
        if any(line.character_id not in scene.character_ids for line in scene.dialogue):
            raise ValueError(f"Dialogue speaker missing from scene {scene.scene_id}")
        if step == "storyboard":
            if not set(scene.prop_ids) <= props:
                raise ValueError(f"Declare new props before referencing them in scene {scene.scene_id}")
            if scene.bgm_id and scene.bgm_id not in bgm:
                raise ValueError(f"Declare new BGM before referencing it in scene {scene.scene_id}")
            if not all((scene.shot_type.strip(), scene.camera_movement.strip(), scene.visual_description.strip())):
                raise ValueError("Storyboard shots require shotType, cameraMovement and visualDescription")
            if scene.status != "draft" or scene.asset_refs:
                raise ValueError("Generated storyboard scenes must be draft with no generated assets")
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
