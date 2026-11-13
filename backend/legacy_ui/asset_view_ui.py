"""Lazy asset views with subject-scoped invalidation and lightweight polling."""
from copy import deepcopy
import streamlit as st

from .file_cache import read_json, version
from .files import input_hash
from .ui_panels import polling_fragment


def data(engine, name, default=None):
    path = engine.path / name
    return read_json(path) if path.exists() else deepcopy(default if default is not None else {})


def media_versions(engine, value):
    result = []
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("file", "filePath", "file_path", "audio", "video", "portrait", "referenceImage") and isinstance(item, str) and item:
                    try:
                        path = engine.local_path(item)
                        result.append((item, version(path)))
                    except (OSError, ValueError):
                        result.append((item, None))
                elif isinstance(item, (dict, list)):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(value)
    return result


def belongs(task, engine, subject, costumes=()):
    project = task.get("generation_button_project") or task.get("project_key")
    if project and str(project).replace("\\", "/").split("/")[-1] != engine.project_id:
        return False
    if task.get("asset_subject") == subject:
        return True
    key = task.get("generation_button_key") or task.get("label", "")
    return any(key.endswith(token) or ("-" + token + "-") in key for token in (subject, *costumes))


def subject_signature(engine, stage, subject):
    from .ui_tasks import workspace_tasks, generation_busy
    state = data(engine, "production.json")
    def subset(values):
        return {key: value for key, value in values.items() if key == stage + ":" + subject or key.startswith(stage + ":" + subject + "__")}
    selected = {key: subset(state.get(key, {})) for key in ("records", "stylePrompts", "assetModifications", "generationInputs")}
    selected["generationMode"] = {"demo": engine.demo, **{key: getattr(engine.settings, key, None)
        for key in ("image_provider", "image_model", "tts_provider", "tts_model", "alignment_model")}}
    selected.update({key: state.get(key) for key in ("providerMode", "projectStyleRules", "projectStyleImage", "genericPrompt", "styleReferences")})
    for key in ("characterApprovals", "backgroundApprovals", "characterImages", "backgroundImages", "backgroundModes", "backgroundPanoramaReviews", "locationReferenceSets", "locationMasterApprovals"):
        selected[key] = state.get(key, {}).get(subject)
    if stage == "backgrounds":
        selected["detailCameras"] = {key:value for key,value in data(engine,"detail_cameras.json").items()
                                     if key.startswith("location_detail:"+subject+":")}
        selected["detailGeneration"] = {key:value for key,value in data(engine,"detail_generation.json").items()
                                        if key.startswith("location_detail:"+subject+":")}
        for key in ("locationMasterApprovals", "locationYawApprovals"):
            selected[key] = {name:value for name,value in state.get(key, {}).items() if name.startswith(subject+":")}
        from .dual_panorama import root
        for name in ("panorama/seam_metadata.json", "panorama/atlas.png"):
            try:
                selected[name] = version(root(engine, subject) / name)
            except FileNotFoundError:
                selected[name] = None
        selected["optionalReferences"] = [(path.as_posix(), version(path)) for path in sorted((root(engine, subject) / "runway").glob("*/result.json"))]
    identity = data(engine, "identity_library.json")
    costume_ids = [key for key, value in identity.get("costumes", {}).items() if value.get("characterId") == subject]
    if stage == "character_references":
        selected["character"] = next((c for c in data(engine, "characters.json").get("characters", []) if c.get("characterId") == subject), None)
        selected["voice"] = identity.get("voices", {}).get(subject)
        selected["costumes"] = {key: identity["costumes"][key] for key in costume_ids}
        library = data(engine, "library_media.json")
        selected["library"] = {key: library.get(key, {}).get(subject) for key in ("samples", "voiceApprovals", "activeSamples")}
        selected["library"].update({key: {name: value for name, value in library.get(key, {}).items() if name in ["costume:" + cid for cid in costume_ids]} for key in ("visuals", "activeVisuals")})
        selected["characterInputs"] = data(engine, "references/character_inputs/inputs.json").get(subject)
    else:
        from .background_library import locations
        selected["location"] = next((loc.model_dump(mode="json", by_alias=True) for loc in locations(engine.creative) if loc.location_id == subject), None)
    selected["inputs"] = subset(data(engine, "references/generation_inputs/inputs.json"))
    selected["style"] = data(engine, "style_rules.json")
    selected["world"] = data(engine, "world.json")
    selected["files"] = media_versions(engine, selected)
    for key in selected["records"] or [stage + ":" + subject]:
        folder = engine.path / "history" / "assets" / stage / key.split(":", 1)[1]
        try:
            selected["history:" + key] = version(folder)
        except FileNotFoundError:
            selected["history:" + key] = None
    for name in (f"prompts/voice_design/{subject}.json",):
        try:
            selected[name] = version(engine.path / name)
        except FileNotFoundError:
            selected[name] = None
    selected["tasks"] = [(item.get("id"), item["future"].done(), item.get("phase"))
                         for item in workspace_tasks() + list(st.session_state.get("workspace-task-queue", []))
                         if belongs(item, engine, subject, costume_ids)]
    selected["blocked"] = generation_busy()
    return input_hash(selected)


def cached(engine, key, stamp, compute):
    cache = st.session_state.setdefault("asset-view-cache", {})
    cache_key = (str(engine.path.resolve()), key)
    entry = cache.get(cache_key)
    if entry is None or entry[0] != stamp:
        result = compute()
        cache[cache_key] = (stamp, result)
        while len(cache) > 256:
            cache.pop(next(iter(cache)))
        return result
    return entry[1]


def character_summary(engine, cid, state):
    def compute():
        from .character_readiness import character_approval_hint
        tasks = tasks_for_view(engine, state)
        checks = engine.character_requirements(cid, state, tasks)
        approved = engine.character_approved(cid, state, tasks)
        hint = character_approval_hint(engine, cid, state, checks, approved=approved)
        return approved, checks, hint
    return cached(engine, "character-summary:" + cid, subject_signature(engine, "character_references", cid), compute)


def tasks_for_view(engine, state):
    files = ("world.json", "characters.json", "story.json", "script.json", "storyboard.json", "prompts.json",
             "identity_library.json", "background_library.json", "production.json", "style_rules.json",
             "references/generation_inputs/inputs.json", "references/character_inputs/inputs.json")
    stamps = []
    for name in files:
        try:
            stamps.append(version(engine.path / name))
        except FileNotFoundError:
            stamps.append(None)
    try:
        return cached(engine, "asset-tasks", stamps, lambda: engine.tasks(state))
    except (OSError, ValueError, KeyError):
        from .ui_tasks import busy
        entry = st.session_state.get("asset-view-cache", {}).get((str(engine.path.resolve()), "asset-tasks"))
        if busy() and entry:
            return entry[1]
        raise


def background_summary(engine, lid, state, tasks):
    from .background_library import BackgroundLibrary
    library = BackgroundLibrary(engine)
    return cached(engine, "background-summary:" + lid, subject_signature(engine, "backgrounds", lid),
                  lambda: (library.approved(lid, state, tasks), library.digest(lid, state, tasks)))


def background_list_signature(engine):
    from .background_library import locations
    from .ui_tasks import workspace_tasks
    ids = [loc.location_id for loc in locations(engine.creative)]
    jobs = [(task.get("id"), task["future"].done()) for task in workspace_tasks()
            if task.get("stable_background_project") == engine.project_id and
            not any(belongs(task, engine, lid) for lid in ids)]
    return ids, jobs


@polling_fragment
def poll_background_list(engine, target, baseline):
    if background_list_signature(engine) != baseline:
        refresh_target(target)


def removal_statuses(engine):
    from .character_removal import CharacterRemoval
    stamps = []
    for project in sorted(engine.path.parent.iterdir()):
        if not project.is_dir():
            continue
        for name in ("manifest.json", "story.json", "script.json", "storyboard.json", "animation.json", "outline.md",
                     "production.json", "identity_library.json", "library_media.json", "characters.json"):
            file = project / name
            if file.exists():
                stamps.append((str(file), version(file)))
    return cached(engine, "removal-statuses", stamps,
                  lambda: CharacterRemoval(engine.manager, engine.project_id).statuses())


def refresh_target(target):
    # Streamlit's public named rerun is callback-only. Timer fragments enqueue
    # the same targeted request through its runtime, without rebuilding the app.
    # Keep the runtime integration isolated here for the supported >=1.66 API.
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    from streamlit.runtime.scriptrunner_utils.script_requests import RerunData
    ctx = get_script_run_ctx()
    if ctx and ctx.script_requests:
        ctx.script_requests.request_rerun(RerunData(
            query_string=ctx.query_string, page_script_hash=ctx.page_script_hash,
            fragment_id_queue=ctx.fragment_storage.resolve_target(target),
            is_fragment_scoped_rerun=True, cached_message_hashes=ctx.cached_message_hashes,
            context_info=ctx.context_info))


@polling_fragment
def poll_subject(engine, stage, subject, target, baseline):
    if subject_signature(engine, stage, subject) != baseline:
        refresh_target(target)
