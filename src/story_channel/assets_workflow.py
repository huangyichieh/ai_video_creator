"""Assets-page orchestration: references, identity approval, then voice auditions."""
from dataclasses import dataclass

from .animation import AnimationDirector, require_voice, voice_signature
from .identity import IdentityManager
from .media_utils import file_hash

from .voice_preview_text import preview_sample_text


@dataclass(frozen=True)
class AssetsAction:
    stage: str
    title: str
    detail: str
    runnable: bool = False


def preview_current(engine, profile, preview):
    if not preview or preview.profile_hash != voice_signature(profile, engine.settings):
        return False
    if preview.asset.provider != ("demo" if engine.demo else engine.settings.tts_provider):
        return False
    try:
        return file_hash(engine.local_path(preview.asset.file_path)) == preview.file_hash
    except (OSError, ValueError):
        return False


def missing_previews(engine, direction):
    missing = []
    for cid, profile in direction.plan.voices.items():
        if not preview_current(engine, profile, direction.previews.get(cid)):
            missing.append((cid, None))
        for emotion, preview in direction.emotion_previews.get(cid, {}).items():
            if not preview_current(engine, profile, preview):
                missing.append((cid, emotion))
    return missing


def character_voice_approval(engine, direction, cid):
    """Use the same current-file and review checks for display and handoff."""
    if not direction or not direction.enabled or cid not in direction.plan.voices:
        return False, "Set up current voice profiles first."
    try:
        AnimationDirector(engine.manager, engine.project_id).validate_plan(direction.plan)
        profile = direction.plan.voices[cid]
        clips = {"baseline": direction.previews.get(cid), **direction.emotion_previews.get(cid, {})}
        for name, clip in clips.items():
            if not preview_current(engine, profile, clip):
                return False, f"Generate a current {name} sample."
            if not clip.approval or clip.approval.content_hash != clip.profile_hash:
                return False, "Listen to the baseline and generated emotions, then approve this character's voice."
        require_voice(engine.manager, engine.project_id, cid)
    except (OSError, ValueError) as exc:
        return False, str(exc)
    return True, "Current baseline and all generated emotion samples approved."


def identity_checklist(engine, state):
    """Separate generated files from registered metadata and voice auditions."""
    service = IdentityManager(engine.manager, engine.project_id)
    library = service.load()
    manifest = engine.manager.load(engine.project_id)
    try:
        characters = {c.character_id for c in engine.creative.artifact("characters").characters}
        locations = {scene.location_id for scene in engine.creative.artifact("storyboard").scenes}
    except (OSError, ValueError):
        try:
            characters = {c.character_id for c in engine.creative.artifact("characters").characters}
            locations = {loc.location_id for loc in engine.creative.artifact("world").locations}
        except (OSError, ValueError):
            characters, locations = set(), set()
    rows = []
    for name, stage, ids, gate in (("Character Reference Packs", "character_references", characters, "characters"),
                                    ("Locations / Backgrounds", "backgrounds", locations, "backgrounds")):
        generated = sum(bool((record := state.records.get(f"{stage}:{subject}")) and record.status == "succeeded"
                             and engine._intact(record)) for subject in ids)
        approved = bool(ids) and generated == len(ids) and gate in manifest.asset_approvals
        rows.append({"Part": name, "Status": f"{generated}/{len(ids)} generated" + (" · Approved" if approved else ""),
                     "Requirement": "Identity approval", "Next action": "Ready" if approved else
                     "Approve references" if ids and generated == len(ids) else "Generate missing references"})
    costumes = {item.character_id for item in library.costumes.values()} if library else set()
    voices = {cid for cid, profile in library.voices.items() if profile.voice_id.strip()} if library else set()
    rows.extend([
        {"Part": "Costumes", "Status": f"{len(costumes & characters)}/{len(characters)} characters registered",
         "Requirement": "Identity approval", "Next action": "Ready · no separate image generation" if library and characters <= costumes
         else "Build/edit the identity library; register a costume for each character"},
        {"Part": "Voice Profiles", "Status": f"{len(voices & characters)}/{len(characters)} voice IDs configured",
         "Requirement": "Identity approval", "Next action": "Ready" if library and characters <= voices else "Set explicit provider Voice IDs in Voice Profiles or the identity library"},
        {"Part": "Props", "Status": f"{len(library.props) if library else 0} registered",
         "Requirement": "Optional", "Next action": "Register recurring props when needed; no separate image generation"},
    ])
    direction = AnimationDirector(engine.manager, engine.project_id).load()
    if direction:
        try:
            AnimationDirector(engine.manager, engine.project_id).validate_plan(direction.plan)
        except (OSError, ValueError):
            direction = None
    generated = sum(preview_current(engine, profile, direction.previews.get(cid))
                    for cid, profile in direction.plan.voices.items()) if direction else 0
    approved = 0
    if direction:
        for cid in direction.plan.voices:
            preview = direction.previews.get(cid)
            if not preview_current(engine, direction.plan.voices[cid], preview) or not preview.approval:
                continue
            if character_voice_approval(engine, direction, cid)[0]:
                approved += 1
    rows.append({"Part": "Voice auditions", "Status": f"{generated}/{len(characters)} generated · {approved}/{len(characters)} approved",
                 "Requirement": "Voice production after identity approval", "Next action": "Listen and approve in Voice Profiles; emotion samples are optional"})
    return rows


def next_assets_action(engine, state):
    manifest = engine.manager.load(engine.project_id)
    if any(not manifest.steps.get(step) or manifest.steps[step].status != "succeeded" for step in ("world", "characters")):
        return AssetsAction("waiting_for_characters", "Complete World / Characters", "Complete current World and Characters on Story & Settings before generating identity assets.")
    identity = IdentityManager(engine.manager, engine.project_id)
    library = identity.load()
    if library:
        try:
            if library.source_hash != identity.source():
                return AssetsAction("identity_library", "Rebuild Identity Library", "World or Characters changed. Rebuild registered identities before generating references.", True)
            identity.validate(library)
        except (OSError, ValueError) as exc:
            return AssetsAction("identity_settings", "Identity library settings", str(exc))
    reference_steps = {
        "character_references": ("Character Reference Packs", "Build default identity registrations if needed, then generate character references one at a time."),
        "backgrounds": ("Locations / Backgrounds", "Generate current background references, one at a time."),
    }
    if state.next_stage in reference_steps:
        title, detail = reference_steps[state.next_stage]
        return AssetsAction(state.next_stage, title, detail, engine.can_generate(state.next_stage, state))
    if state.next_stage in ("character_review", "background_review"):
        title = "Approve Character Reference Packs" if state.next_stage == "character_review" else "Approve Locations / Backgrounds"
        return AssetsAction(state.next_stage, title, "Review the images and use the reference approval controls below.")
    if state.next_stage == "waiting_for_creative":
        return AssetsAction(state.next_stage, "Complete Story & Settings", "Complete story/storyboard approvals and Prompts to unlock backgrounds.")
    if library is None:
        return AssetsAction("identity_library", "Build Identity Library", "Build costume, prop, location and voice registrations. Do this before references when possible, as it can make references outdated.", True)
    missing = [cid for cid, profile in library.voices.items() if not profile.voice_id.strip()]
    if missing:
        return AssetsAction("voice_settings", "Configure Voice Profiles", "Open Assets → Voice Profiles and save a provider Voice ID for each character: " + ", ".join(missing) + ". For ElevenLabs, use My Voices → ⋯ → Copy voice ID. Then approve the identity library.")
    try:
        identity.require_approved()
    except (OSError, ValueError) as exc:
        return AssetsAction("identity_approval", "Approve Identity Library", str(exc) + ". Use the identity approval button above.")
    director = AnimationDirector(engine.manager, engine.project_id)
    direction = director.load()
    try:
        if direction is None:
            raise ValueError("Voice profiles have not been set up")
        director.validate_plan(direction.plan)
    except (OSError, ValueError):
        return AssetsAction("voice_profiles", "Set up Voice Profiles", "Prepare current voice profiles and direction from the approved identity library.", True)
    if missing_previews(engine, direction):
        return AssetsAction("voice_previews", "Voice auditions", "Generate missing/outdated baseline auditions and refresh any outdated emotion samples. Then listen and approve in Voice Profiles.", True)
    pending = [cid for cid in direction.plan.voices if not character_voice_approval(engine, direction, cid)[0]]
    if pending:
        return AssetsAction("voice_approval", "Approve Voice Profiles", "Voices still needing approval: " + ", ".join(pending) + ". Listen to each character's baseline and generated emotions, check the confirmation, then Approve voice preview. Regenerating a sample requires approval again.")
    if not engine.demo and not state.voice_map.get("narrator", "").strip():
        if any(scene.narration.strip() for scene in engine.creative.artifact("storyboard").scenes):
            return AssetsAction("narrator", "Configure narrator voice", "Save the narrator provider Voice ID in Voice Profiles before generating narration tracks.")
    return AssetsAction("complete", "Assets ready → Animations", "Continue on Animations for scene images, voice tracks, performance approval and scene generation.")


def generate_assets_page(engine, *, until_review=False, samples=None):
    """Run only Assets work, stopping before manual settings, approval or handoff."""
    samples = samples or {}
    while not engine.stop_requested():
        state = engine.inspect()
        action = next_assets_action(engine, state)
        if not action.runnable:
            return action
        if action.stage in ("character_references", "backgrounds"):
            if (action.stage == "character_references" and not state.records
                    and IdentityManager(engine.manager, engine.project_id).load() is None):
                IdentityManager(engine.manager, engine.project_id).prepare()
            engine.generate(action.stage)
        elif action.stage == "identity_library":
            IdentityManager(engine.manager, engine.project_id).prepare()
        elif action.stage == "voice_profiles":
            AnimationDirector(engine.manager, engine.project_id).prepare()
        elif action.stage == "voice_previews":
            director = AnimationDirector(engine.manager, engine.project_id)
            director.assets.demo = engine.demo
            direction = director.load(required=True)
            for cid, emotion in missing_previews(engine, direction):
                if engine.stop_requested():
                    break
                text = samples.get(cid) or preview_sample_text(engine.manager.load(engine.project_id).language, direction.plan.performances, cid)
                if not text.strip():
                    raise ValueError(f"Enter preview sample text in the project language for {cid} in Voice Profiles.")
                director.preview_voice(cid, text, emotion=emotion)
        if not until_review:
            return action
