"""Read-only Assets inventory and approval checklist; never starts provider calls."""
import json

from .animation import AnimationDirector
from .assets_workflow import character_voice_approval, identity_checklist, preview_current
from .identity import IdentityManager
from .media_utils import file_hash


def assets_status_rows(engine, state, *, running_label=""):
    manager, pid = engine.manager, engine.project_id
    manifest = manager.load(pid)
    if manifest.workflow_version == "world_episode" and not manifest.world_project_id:
        return world_assets_status_rows(engine, state)
    identity = IdentityManager(manager, pid)
    library = identity.load()
    library_valid, library_error, identity_approved = False, "", False
    if library:
        try:
            identity.validate(library)
            library_valid = True
            identity.require_approved()
            identity_approved = True
        except (ValueError, OSError) as exc:
            library_error = str(exc)
    identity_status = "Approved" if identity_approved else "Outdated" if library and library.approval else "Not approved"
    rows = []

    def add(tab, stage, subject, status, approval="Not required", checklist="", action="", record=None, error=""):
        rows.append({"Stage": stage, "Subject": subject, "Tab": tab, "Status": status,
                     "Approval": approval, "Checklist": checklist, "Next action": action,
                     "Revision": record.revision if record else None,
                     "Attempts": record.attempts if record else None,
                     "Error": error or (record.error or "" if record else "")})

    try:
        characters = [c.character_id for c in engine.creative.artifact("characters").characters]
    except (ValueError, OSError):
        characters = list(library.voices) if library else []
    try:
        locations = [loc.location_id for loc in engine.creative.artifact("world").locations]
        try:
            used = {scene.location_id for scene in engine.creative.artifact("storyboard").scenes}
            locations = [lid for lid in locations if lid in used]
        except (ValueError, OSError):
            pass
    except (ValueError, OSError):
        locations = list(library.locations) if library else []
    try:
        tasks = engine.tasks(state.model_copy(deep=True))
    except (ValueError, OSError):
        tasks = None
    task_index = {t.key: t for t in tasks or []}
    for stage, tab, subjects, gate in (
        ("character_references", "Character References", characters, "characters"),
        ("backgrounds", "Locations / Backgrounds", locations, "backgrounds"),
    ):
        subjects = list(dict.fromkeys(subjects + [r.subject for r in state.records.values() if r.stage == stage]))
        approval = manifest.asset_approvals.get(gate)
        valid_gate = bool(approval and tasks is not None and approval.content_hash == engine._gate_hash(gate, state, tasks))
        intact = []
        for subject in subjects:
            record = state.records.get(f"{stage}:{subject}")
            task = task_index.get(f"{stage}:{subject}")
            ready = bool(task and engine.record_current(record, task, state))
            intact.append(ready)
            status = ("Generating…" if record.status == "running" else record.status) if record else "Not generated"
            if record and record.status == "succeeded" and not ready:
                status = "stale"
            subject_approved = engine.character_approved(subject, state, tasks) if stage == "character_references" else valid_gate and ready
            add(tab, stage, subject, status, "Approved" if subject_approved else "Pending" if stage == "character_references" else "Outdated" if approval else "Not approved",
                "Current file; matching generation inputs; visual review",
                "Ready" if subject_approved else "Review and approve references" if ready else "Generate or apply a saved revision, then approve", record,
                error=record.stale_reason if record and status == "stale" else "")
        if not subjects:
            add(tab, stage, "Awaiting story data", "Blocked", "Not approved",
                "Current World / Characters / Storyboard", "Approve World, generate Characters on Assets, and complete the episode Storyboard")
        ready = bool(subjects) and all(intact)
        add(tab, stage + "_approval", "All references", "Approved" if valid_gate and ready else "Ready for review" if ready else "Blocked",
            "Approved" if valid_gate and ready else "Outdated" if approval else "Not approved",
            f"{sum(intact)}/{len(subjects)} current files; review appearance/continuity; check approval confirmation",
            "Ready" if valid_gate and ready else "Use the reference approval controls" if ready else "Generate missing/outdated references")

    for stage, tab, entries in (("costumes", "Costumes", library.costumes if library else {}),
                                ("props", "Props", library.props if library else {})):
        for subject in entries or {"All items": None}:
            add(tab, stage, subject, "Registered" if entries and library_valid else "Outdated" if entries else "Not registered" if stage == "costumes" else "Optional · empty",
                identity_status if entries else "Included in identity approval" if stage == "costumes" else "Not required",
                "Every character needs a costume; valid stable IDs" if stage == "costumes" else "Optional; valid owner/location IDs",
                "Edit in the identity library; no separate generation", error=library_error if not library_valid else "")

    director = AnimationDirector(manager, pid)
    direction = director.load()
    direction_valid = False
    if direction:
        try:
            director.validate_plan(direction.plan)
            direction_valid = direction.enabled
        except (ValueError, OSError):
            pass
    add("Voice Profiles", "voice_setup", "Voice profile setup", "Ready" if direction_valid else "Outdated" if direction else "Not prepared",
        checklist="Identity library approved; saved provider Voice IDs; current direction plan",
        action="Ready" if direction_valid else "Set up voices and auditions in each character Voice tab")
    for cid in characters:
        profile = library.voices.get(cid) if library else direction.plan.voices.get(cid) if direction else None
        configured = bool(profile and profile.voice_id.strip())
        add("Voice Profiles", "voice_profiles", cid, "Configured" if configured and (library_valid or not library) else "Outdated" if configured else "Not configured",
            identity_status, "Explicit provider Voice ID; current identity profile", "Ready" if configured else "Save a provider Voice ID")
        planned = direction.plan.voices.get(cid) if direction else None
        matching = bool(direction_valid and planned and profile == planned and (library_valid or not library))
        previews = {"baseline": direction.previews.get(cid) if direction else None}
        previews.update(direction.emotion_previews.get(cid, {}) if direction else {})
        current = {name: bool(matching and preview_current(engine, planned, clip)) for name, clip in previews.items()}
        voice_approved = character_voice_approval(engine, direction, cid)[0]
        for name, clip in previews.items():
            ready = current[name]
            add("Voice Profiles", "voice_auditions", f"{cid} · {name}", "Generated" if ready else "Outdated" if clip else "Not generated",
                "Approved" if voice_approved else "Outdated" if clip and clip.approval else "Not approved",
                "Current voice/provider; intact audio; listen; approve baseline and all generated emotions",
                "Ready" if voice_approved else "Listen and approve voice" if ready else "Generate current audition")
        for name in ("happy", "sad", "angry", "afraid", "excited"):
            if name not in previews:
                add("Voice Profiles", "voice_auditions", f"{cid} · {name}", "Optional · not generated",
                    checklist="Optional; once generated, must be current and included in voice approval",
                    action="Generate emotion sample when needed")
        add("Voice Profiles", "voice_approval", cid, "Approved" if voice_approved else "Ready for review" if all(current.values()) else "Blocked",
            "Approved" if voice_approved else "Not approved",
            f"Voice ID: {'ready' if configured else 'missing'}; baseline: {'ready' if current['baseline'] else 'missing/outdated'}; generated emotions: {sum(current.values()) - int(current['baseline'])}/{len(current) - 1} current; listening confirmation",
            "Ready" if voice_approved else "Listen, check confirmation, then Approve voice preview")
    narrator = state.voice_map.get("narrator", "").strip()
    try:
        narrator_required = any(scene.narration.strip() for scene in engine.creative.artifact("storyboard").scenes) and not engine.demo
    except (ValueError, OSError):
        narrator_required = False
    add("Voice Profiles", "narrator", "Narrator", "Configured" if narrator else "Not configured" if narrator_required else "Optional · not configured",
        checklist="Provider Voice ID for story narration; no character audition approval",
        action="Ready" if narrator else "Save narrator Voice ID" if narrator_required else "Configure when narration is needed")
    checks = identity_checklist(engine, state)
    add("Identity library", "identity_approval", "Identity and reference library", identity_status if library else "Not built", identity_status,
        "; ".join(f"{item['Part']}: {item['Status']}" for item in checks if item["Requirement"] == "Identity approval"),
        "Ready" if identity_approved else "Review and approve identity library", error=library_error)

    if engine.shared_style_enabled(state):
        style_files = sum(bool(engine.local_path(item["file"]).is_file() and
                               file_hash(engine.local_path(item["file"])) == item["hash"])
                          for item in state.style_references)
        style_ready = engine.has_generic_prompt(state) and style_files == len(state.style_references)
        add("Style References", "style_references", "Shared style", "Ready" if style_ready else "Missing / outdated",
            checklist=f"Text: {'saved' if state.generic_prompt.strip() else 'empty'}; images: {style_files}/{len(state.style_references)} intact (maximum 3); text or image required",
            action="Ready" if style_ready else "Save shared style text or reference images")
        add("Style References", "registered_style", "Identity style", "Registered" if library and library.style.strip() else "Optional · empty",
            identity_status if library and library.style.strip() else "Not required",
            "Optional style metadata in the identity library", "Edit in the identity library when needed")
    receipts = sorted((engine.path / "audio" / "library").glob("*.source.json"))
    for receipt in receipts:
        try:
            data = json.loads(receipt.read_text(encoding="utf-8"))
            ready = engine.local_path(data["file"]).is_file() and bool(data.get("source", "").strip())
            add("Music / SFX library", "audio_library", data["file"], "Ready" if ready else "Missing file / license", checklist="Local audio file; license/source recorded", action="Place in Final audio mixing" if ready else "Import audio with its license/source")
        except (OSError, ValueError, KeyError) as exc:
            add("Music / SFX library", "audio_library", receipt.name, "Invalid entry", error=str(exc))
    if not receipts:
        add("Music / SFX library", "audio_library", "Music / SFX", "Optional · empty", checklist="Licensed audio; scene SFX generation is on Animations", action="Import audio when needed")
    images = [r for r in state.records.values() if r.stage in ("character_references", "backgrounds", "scene_images")]
    count = sum(r.status == "succeeded" and engine._intact(r) for r in images)
    add("Reusable generated images", "reusable_images", "Image collection", f"{count}/{len(images)} current images", checklist="Existing generated images; approval belongs to their source stage", action="Browse images; regenerate at the source stage")
    for row in rows:
        if row["Stage"] == "voice_auditions":
            cid, emotion = row["Subject"].split(" · ", 1)
            label = f"Previewing {cid}" + (f": {emotion}" if emotion != "baseline" else "")
            if running_label == label:
                row["Status"] = "Generating…"
        elif row["Stage"] == "voice_approval" and running_label == f"Approving {row['Subject']} voice preview":
            row["Status"] = "Approving…"
        elif row["Stage"] == "voice_profiles" and running_label == f"Saving {row['Subject']} voice":
            row["Status"] = "Saving…"
    return rows


def world_assets_status_rows(engine, state):
    """World-owned inventory: no episode storyboard/direction prerequisites."""
    from .library_media import LibraryMedia
    from .files import input_hash
    manifest = engine.manager.load(engine.project_id)
    identity = IdentityManager(engine.manager, engine.project_id)
    library = identity.load()
    media = LibraryMedia(engine.manager, engine.project_id)
    saved = media.load()
    rows = []
    def add(tab, stage, subject, status, approval="Not required", revision=None, attempts=None, checklist="", error=""):
        rows.append({"Stage": stage, "Subject": subject, "Tab": tab, "Status": status,
                     "Approval": approval, "Revision": revision, "Attempts": attempts, "Checklist": checklist, "Error": error})
    yaw_reviews = {}
    for task in engine.tasks(state):
        record = state.records.get(task.key)
        gate = "characters" if task.stage == "character_references" else "backgrounds"
        from .background_library import BackgroundLibrary
        approved = engine.character_approved(task.subject, state) if gate == "characters" else BackgroundLibrary(engine).approved(BackgroundLibrary(engine).subject_id(task.subject), state)
        if task.operation == "location_panorama":
            from .dual_panorama import approved as panorama_approved
            lid,name = task.subject.rsplit("__",1)
            approved = panorama_approved(engine,state,lid,name)
        elif task.operation == "location_projection":
            from .dual_panorama import approved_views
            lid = task.subject.rsplit("__",1)[0]
            if lid not in yaw_reviews:
                yaw_reviews[lid] = approved_views(engine,state,lid)
            approved = yaw_reviews[lid][int(task.subject.rsplit("view_",1)[1])]
        waiting_master = False
        if task.stage == "backgrounds" and "__view_" in task.subject:
            from .location_references import master_approved
            lid = BackgroundLibrary(engine).subject_id(task.subject)
            waiting_master = state.location_reference_sets.get(lid, {}).get("reference_type") in ("KEY", "KEY_360") and not master_approved(engine, state, lid)
        add("Character References / Visual" if gate == "characters" else "Background", task.stage, task.subject,
            "Waiting for master approval" if waiting_master else "Generating..." if record and record.status == "running" else
            "Needs camera-angle review" if record and record.status == "succeeded" and record.asset and record.asset.generation_params.get("yawReview", {}).get("requiresReview") and not approved else record.status if record else "pending",
            "Approved" if approved else "Pending",
            record.revision if record else 0, record.attempts if record else 0,
            "Generate one source panorama and review its seam" if task.operation == "panorama" else
            "Deterministic local projection from approved panorama world pixels" if task.operation in ("panorama_view", "location_projection") else
            "Generate/apply revision, inspect and approve current references", record.error or "" if record else "")
    from .dual_panorama import is_key, approved as panorama_approved, digest, MASTERS, seam_info, pair_ready
    for lid, spec in state.location_reference_sets.items():
        if spec.get("reference_type") == "SIMPLE":
            approved = BackgroundLibrary(engine).approved(lid,state)
            ready = BackgroundLibrary(engine).digest(lid,state,engine.tasks(state,references_only=True)) is not None
            add("Background","simple_master_approval",lid,
                "Approved" if approved else "Needs review" if ready else "Generate master",
                "Approved" if approved else "Not approved",
                checklist="SIMPLE: saved detail prompt, optional guidance image and one current master image.")
            continue
        from .shared_room import enabled,current
        if enabled(spec):
            from .room_geometry import scene_from_spec
            try:
                scene_from_spec(spec)
                layout_status = "Ready"
            except ValueError:
                layout_status = "Prepare spatial layout"
            add("Background","shared_3d_layout",lid,layout_status,
                checklist="One spatial room boundary, camera center, object footprints, heights and materials.")
            add("Background","shared_3d_panorama",lid,"Rendered" if current(engine,state,lid) else "Render panorama",
                "Approved" if BackgroundLibrary(engine).approved(lid,state) else "Pending",
                checklist="Local 360° blockout is additional geometry/context; shared room remains spatial truth.")
            from .room_canonical import records
            raw_count = len(records(engine,state,lid,enhanced=False))
            enhanced_count = len(records(engine,state,lid))
            add("Background","canonical_reference_pack",lid,f"{raw_count}/8 raw · {enhanced_count}/8 enhanced",
                "Approved" if BackgroundLibrary(engine).approved(lid,state) else "Pending",
                checklist="Compare enhanced/raw pairs, confirm fixed-object geometry and overlap consistency, then approve. Shots use nearest enhanced views for appearance.")
            continue
        if is_key(spec):
            from .floor_plan_guides import current_guides
            guides_ready = bool(current_guides(engine,lid,spec))
            add("Background","floor_plan_camera_guides",lid,"Ready" if guides_ready else "Generate floor plan image",
                checklist="Generate current four directional camera-region guides from the floor plan and object layout.")
            for name in MASTERS:
                approved = panorama_approved(engine, state, lid, name)
                add("Background", "background_master_approval", lid+"__"+name,
                    "Approved" if approved else "Needs review" if digest(engine, state, lid, name) else "Generate master",
                    "Approved" if approved else "Not approved", checklist="Review all four camera-region masters and overlaps for layout, object and style consistency.")
            from .location_details import current as detail_current
            for identity,reference in spec.get("detail_references",{}).items():
                ready = detail_current(engine,state,lid,reference)
                add("Background","background_detail_reference",lid+"__"+identity,
                    "Ready" if ready else "Stale or missing","Optional",
                    checklist="Local appearance only; resolver selects relevant details after the primary master.")
    for gate in ("characters", "backgrounds"):
        add("Character References / Visual" if gate == "characters" else "Background", gate + "_approval", "All references",
            "Approved" if gate in manifest.asset_approvals else "Needs review", "Approved" if gate in manifest.asset_approvals else "Not approved",
            checklist="Every character: current visual, approved voice samples and current visuals for all costumes" if gate == "characters" else "DETAIL: four approved directional masters and camera guides. SIMPLE: one current approved master.")
    for cid, profile in library.voices.items() if library else []:
        approved = media.voice_approved(cid, profile, saved)
        add("Character References / Voice", "voice_profiles", cid, "Configured" if profile.voice_id.strip() else "Missing Voice ID",
            checklist="Save a valid provider Voice ID")
        signature = input_hash(profile.model_dump(mode="json"))
        samples = media.selected_samples(cid, profile, saved)
        for sample in samples:
            try:
                intact = file_hash(engine.local_path(sample["audio"])) == sample["fileHash"]
            except (OSError, ValueError):
                intact = False
            current_provider = media.sample_matches_provider(sample)
            add("Character References / Voice", "voice_samples", cid + " / " + sample["emotion"], "Different provider/model" if not current_provider else sample.get("status", "succeeded") if intact else "Missing / changed audio",
                "Approved" if approved and current_provider else "Not approved", sample["revision"], checklist="Listen to the current neutral and generated emotion samples")
        add("Character References / Voice", "voice_approval", cid, "Approved" if approved else "Needs review", "Approved" if approved else "Not approved",
            checklist="Current neutral sample required; all generated emotions intact")
    add("Character References / Voice", "narrator", "Narrator", "Configured" if state.voice_map.get("narrator", "").strip() else "Not configured",
        checklist="A saved provider Voice ID is required for production episodes containing narration")
    for item in library.costumes.values() if library else []:
        from .library_revisions import active_visual, visual_current
        key = "costume:" + item.costume_id
        latest = active_visual(saved, key)
        current = False
        if latest:
            try:
                current = visual_current(media, saved, key, media.costume_signature(item.character_id, item.costume_id))
            except (OSError, ValueError):
                pass
        add("Character References / Costumes", "costumes", item.costume_id, "Model sheet generated" if current else "Outdated visual" if latest else "Registered", revision=latest["revision"] if latest else 0,
            checklist="Current generated/applied costume model sheet required before character approval")
    for pid, prop in saved["props"].items():
        for mode in prop["modes"]:
            versions = saved["visuals"].get("prop:" + pid + ":" + mode, [])
            add("Props", "prop_modes", prop["title"] + " / " + mode, "Visual generated" if versions else "Not generated",
                revision=versions[-1]["revision"] if versions else 0, checklist="Optional; independent prompt and visual for each prop mode")
    receipts = list((engine.path / "audio" / "library").glob("*.source.json"))
    from .file_cache import read_json
    sfx_count = 0
    for receipt in receipts:
        try:
            sfx_count += read_json(receipt).get("kind") != "bgm"
        except (OSError,ValueError):
            pass
    add("SFX", "audio_library", "Licensed SFX", f"{sfx_count} library items", checklist="Optional; scene-specific generation is on Animations > SFX")
    from .bgm_library import entries,ready
    music = entries(engine.manager,engine.project_id)
    for identity,item in music.items():
        usable = ready(engine.path,item)
        add("BGM","bgm",item["title"]+" · "+identity,"Ready" if usable else "Pending audio",
            checklist="Optional reusable background music; license/source required. "+("Choose in storyboard" if usable else "Attach licensed BGM audio"))
    if not music:
        add("BGM","bgm","Reusable background music","Optional · empty",checklist="BGM requirements are added from storyboards. Add a draft or import licensed music.")
    from .style_check import pending_candidates,candidates
    visual_assets = candidates(engine,state)
    pending = pending_candidates(engine,state,visual_assets)
    add("Style check","style_check","New or updated visual assets",f"{len(pending)} awaiting optional review",
        checklist="Optional; only unreviewed/new revisions are targets, unchanged assets are comparison context.")
    return rows
