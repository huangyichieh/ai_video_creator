"""Read-only Assets inventory and approval checklist; never starts provider calls."""
import json

from .animation import AnimationDirector
from .assets_workflow import character_voice_approval, identity_checklist, preview_current
from .identity import IdentityManager
from .media_utils import file_hash


def assets_status_rows(engine, state, *, running_label=""):
    manager, pid = engine.manager, engine.project_id
    manifest = manager.load(pid)
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
        ("character_references", "Character Reference Packs", characters, "characters"),
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
            add(tab, stage, subject, status, "Approved" if valid_gate and ready else "Outdated" if approval else "Not approved",
                "Current file; matching generation inputs; visual review",
                "Ready" if valid_gate and ready else "Review and approve references" if ready else "Generate or apply a saved revision, then approve", record,
                error=record.stale_reason if record and status == "stale" else "")
        if not subjects:
            add(tab, stage, "Awaiting story data", "Blocked", "Not approved",
                "Current World / Characters / Storyboard", "Complete the required stages on Story & Settings")
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
        action="Ready" if direction_valid else "Use Generate next asset Stage after identity approval")
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
