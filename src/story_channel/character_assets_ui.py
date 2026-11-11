"""Character reference cards and drafts backed by the canonical character bible."""
from .image_preview_ui import image_preview
from .revision_labels import revision_label
from .error_report_ui import render_error, error_project, report_widget_key
from .generation_ui import generation_button
import difflib
import json

import streamlit as st

from .files import input_hash
from .proofreading import TypoSuggestion, apply_suggestion, check_typos
from .ui_tasks import busy, launch, launch_assets, can_queue_asset, queue_asset, asset_activity
from .asset_sources import read_source, edit_source
from .action_labels import text_action_label, asset_modify_label
# Imported lazily when opening the shared dialog to avoid UI module cycles.


def detail_prefix(engine, stage, subject):
    return (f"asset-character-{engine.project_id}-{subject}" if stage == "character_references"
            else f"asset-editor-{stage}-{engine.project_id}-{subject}")


def draft_changed(engine, stage, subject, source):
    prefix = detail_prefix(engine, stage, subject)
    saved = source.model_dump_json(by_alias=True, indent=2)
    if stage != "character_references":
        baseline = st.session_state.get(prefix+"-baseline",saved)
        draft = st.session_state.get(prefix+"-draft",baseline)
        if baseline != saved and draft == baseline:
            # A clean source view follows canonical edits made on another page.
            # Preserve an actual unsaved draft and let its concurrency guard run.
            st.session_state[prefix+"-baseline"] = saved
            st.session_state[prefix+"-draft"] = saved
            st.session_state.pop(prefix+"-typos",None)
            st.session_state.pop(prefix+"-error",None)
    return st.session_state.get(prefix + "-draft", saved) != st.session_state.get(prefix + "-baseline", saved)


def keep_details_open(prefix):
    st.session_state[prefix + "-details-open"] = True
    st.session_state[prefix + "-details-panel"] = True


def character_details(engine, character, record, *, stage="character_references", subject=None):
    cid = subject or character.character_id
    is_character = stage == "character_references"
    source = {"character_references": "characters", "backgrounds": "world", "scene_images": "prompts", "voices": "storyboard"}[stage]
    prefix = detail_prefix(engine, stage, cid)
    saved = character.model_dump_json(by_alias=True, indent=2)
    if not is_character:
        draft_changed(engine,stage,cid,character)
    baseline_key, draft_key, report_key = prefix + "-baseline", prefix + "-draft", prefix + "-typos"
    st.session_state.setdefault(baseline_key, saved)
    st.session_state.setdefault(draft_key, saved)
    baseline = st.session_state[baseline_key]
    if not is_character and baseline != saved:
        st.warning("The saved source changed on another page. Your unsaved draft is preserved; Revert all changes loads the latest saved content.")

    def reset():
        st.session_state[baseline_key] = saved
        st.session_state[draft_key] = saved
        st.session_state.pop(report_key, None)
        st.session_state.pop(prefix + "-error", None)
        keep_details_open(prefix)

    panel_key, open_key = prefix + "-details-panel", prefix + "-details-open"
    if panel_key in st.session_state:
        st.session_state[open_key] = bool(st.session_state[panel_key])
    from .ui_panels import persistent_expander
    panel = persistent_expander("Character detail" if is_character else "Generation details", key=panel_key,
        expanded=st.session_state.get(open_key, False))
    if not panel.open:
        return st.session_state[draft_key] != baseline
    with panel:
        st.caption("Source details. Use Edit mode to edit character fields; saving updates related files and affected approvals." if is_character else
                   "Source details. Use Edit JSON to make changes; saving updates related files and affected approvals.")
        if stage == "voices":
            st.caption("This is the source storyboard shared by the scene's speech tracks. Character Lines controls this track's saved character and text. Editing this JSON changes the shared storyboard; style instructions control delivery.")
        editing = st.toggle("Edit mode" if is_character else "Edit JSON", key=prefix + ("-edit-mode" if is_character else "-edit-json"),
                            disabled=busy(), on_change=keep_details_open, args=(prefix,))
        draft = st.session_state[draft_key]
        if editing and not is_character:
            draft = st.text_area("Source JSON", key=draft_key, height=240, disabled=busy())
        valid_json = True
        try:
            data = json.loads(draft)
            if is_character and not isinstance(data, dict):
                raise ValueError("Character fields must be an object")
            if is_character and editing:
                from .character_fields_ui import character_fields
                character_fields(data, prefix, draft_key, on_edit=lambda: keep_details_open(prefix))
            elif not editing:
                from .json_details_ui import compact_json
                compact_json({key: value for key, value in data.items() if key != "referenceImage"} if is_character else data)
            if is_character:
                try:
                    type(character).model_validate(data)
                    if data.get("characterId") != cid:
                        raise ValueError("Character ID cannot be changed")
                except ValueError:
                    valid_json = False
                    st.caption("Complete all required character fields before saving. Character ID must remain unchanged.")
        except (ValueError, TypeError, KeyError):
            valid_json = False
            render_error("The character draft cannot be read. Use Revert all changes to restore its saved fields." if is_character else
                     "Invalid JSON. Open Edit JSON and fix the draft before saving or checking typos.", project_path=error_project(locals()), key=report_widget_key('character_assets_ui:88', locals()))
        changed = draft != baseline
        if baseline != saved:
            st.warning("The saved source changed elsewhere. Revert all changes to load its latest JSON, or keep your draft for comparison.")
        if changed:
            st.caption("Modified — not saved")
            with st.expander("View changes", expanded=False):
                st.code("\n".join(difflib.unified_diff(baseline.splitlines(), draft.splitlines(),
                                                    fromfile="Saved source", tofile="Draft source", lineterm="")), language="diff")
        else:
            st.caption("No unsaved changes")
        save, revert, typo, modify = st.columns(4)
        from .modification_ui import open_modification
        modify.button(text_action_label("Modify", engine.creative, f"{stage}:{cid}"), key=prefix + "-modify", disabled=busy() or changed or baseline != saved, on_click=open_modification, args=(engine.project_id, "source", stage,), kwargs={'subject': cid, 'expected': saved, 'rerun': False})
        if save.button("Save changes", key=prefix + "-save", disabled=busy() or not changed or not valid_json, type="primary"):
            def saved_draft(_):
                latest = (next(c for c in engine.creative.artifact("characters").characters if c.character_id == cid)
                          if is_character else read_source(engine.creative, stage, cid))
                formatted = latest.model_dump_json(by_alias=True, indent=2)
                st.session_state[baseline_key] = formatted
                st.session_state[draft_key] = formatted
                st.session_state.pop(report_key, None)
                keep_details_open(prefix)

            launch(lambda: engine.creative.edit_character(cid, draft, expected=baseline) if is_character
                   else edit_source(engine.creative, stage, cid, draft, expected=baseline),
                   f"Saving {cid}", saved_draft)
        revert.button("Revert all changes", key=prefix + "-revert", disabled=busy() or not (changed or baseline != saved), on_click=reset)
        if typo.button("Check typos", key=prefix + "-check", disabled=busy() or not valid_json):
            checked_values = {source: draft}

            def checked(report):
                st.session_state[report_key] = {"draft_hash": input_hash(checked_values), "report": report.model_dump()}
                keep_details_open(prefix)

            launch(lambda: check_typos(engine.creative, checked_values, source, task_key=f"typo_check:{stage}:{cid}"), f"Checking typos for {cid}", checked)
        checked = st.session_state.get(report_key)
        if checked:
            current = {source: draft}
            stale = checked["draft_hash"] != input_hash(current)
            st.markdown("**Typo recommendations**")
            st.write(checked["report"]["summary"])
            if stale:
                st.warning("The draft changed after this check. Check typos again for current recommendations.")

            def apply(index):
                report = st.session_state[report_key]
                values = {source: st.session_state[draft_key]}
                if report["draft_hash"] != input_hash(values):
                    return
                try:
                    suggestion = TypoSuggestion.model_validate(report["report"]["suggestions"][index])
                    updated = apply_suggestion(values, suggestion)
                    st.session_state[draft_key] = updated
                    report["draft_hash"] = input_hash({source: updated})
                    report.setdefault("applied", []).append(index)
                    keep_details_open(prefix)
                except ValueError as exc:
                    st.session_state[prefix + "-error"] = str(exc)

            error = st.session_state.pop(prefix + "-error", None)
            if error:
                render_error(error, project_path=error_project(locals()), key=report_widget_key('character_assets_ui:152', locals()))
            for i, suggestion in enumerate(checked["report"]["suggestions"]):
                st.caption(suggestion["field"])
                before, after = st.columns(2)
                before.text("Original: " + suggestion["original"])
                after.text("Suggested: " + suggestion["replacement"])
                st.write(suggestion["explanation"])
                applied = i in checked.get("applied", [])
                st.button("Applied" if applied else "Apply suggestion", key=f"{prefix}-apply-{i}",
                          disabled=busy() or stale or applied, on_click=apply, args=(i,))
            if not checked["report"]["suggestions"]:
                st.info("No typos were identified.")
        if record:
            st.markdown("**Generation metadata (read-only)**")
            st.caption(f"{record.status.title()} · Revision {record.revision} · Attempts {record.attempts}")
            if record.asset:
                st.caption(f"{record.asset.provider} / {record.asset.model}")
            if st.toggle("Show metadata JSON", key=prefix + "-metadata-json"):
                st.json(record.model_dump(mode="json", by_alias=True), expanded=False)
    return changed


def generation_controls(engine, state, stage, subject, has_asset, changed, *, available=True,stable_target=None,unavailable_reason=None):
    if stage == "character_references":
        from .visual_generation_ui import generation_section
        generation_section(engine, state, stage, subject, changed=changed, available=available)
        return
    key = f"{stage}:{subject}"
    record = state.records.get(key)
    style_key = f"asset-style-{engine.project_id}-{key}"
    st.session_state.setdefault(style_key, state.style_prompts.get(key, ""))

    def save_style():
        try:
            engine.set_style_prompt(stage, subject, st.session_state[style_key])
        except (OSError, ValueError, RuntimeError) as exc:
            st.session_state["workspace-task-message"] = ("error", str(exc))

    prompt, generate, modify = st.columns([3, 1, 1.5], vertical_alignment="bottom")
    from .modification_ui import open_modification
    modify.button(asset_modify_label(engine, stage), key=f"modify-asset-{key}", disabled=busy() or changed or not has_asset or not available, on_click=open_modification, args=(engine.project_id, "asset", stage,), kwargs={'subject': subject, 'expected': record.asset.asset_id if record and record.asset else None, 'rerun': False})
    prompt.text_input("Style prompt", key=style_key, on_change=save_style, disabled=busy(),
                      help="Saved locally for this asset. Applies to individual and batch generation. For voices, describes delivery and emotion; it is not spoken.")
    label = "Retry asset" if record and record.status == "failed" else "Regenerate asset" if has_asset else "Generate asset"
    queueable = can_queue_asset(engine, stage, subject)
    if queueable:
        label = "Queue asset"
    if stage == "scene_images":
        from .action_labels import action_label
        from .scene_image_references import SCENE_IMAGE_MODEL
        label = action_label(label,"demo" if engine.demo else "openai",SCENE_IMAGE_MODEL)
    def generate_current():
        if stable_target and stage == "scene_images":
            from .scene_image_cards_ui import request_generation
            request_generation(engine,subject,has_asset,stable_target)
            return
        if busy():
            if not stable_target:
                queue_asset(engine, stage, subject, force=has_asset)
            else:
                from .ui_tasks import can_queue_work,find_task
                if not can_queue_work():
                    running = find_task(lambda item:item.get("asset_engine") is not None and item["asset_engine"].path == engine.path and not item["future"].done())
                    if running:
                        running["asset_engine"].enqueue(stage,subject,force=has_asset)
                    return
        task = launch_assets(engine, lambda: engine.generate(stage, subject=subject, force=has_asset), f"Generating {subject}",
            rerun_on_start=not bool(stable_target), service="openai" if stage in ("character_references","scene_images") else None)
        if task and stable_target:
            task.update(scene_image_project=engine.project_id,asset_subject=subject,
                scene_image_baselines={subject:(record.revision,record.attempts) if record else (0,0)})
    activity = asset_activity(engine,stage,subject)
    if stable_target and stage == "scene_images":
        from .scene_image_cards_ui import scene_phase
        activity = scene_phase(engine,subject,record)
    reason = ("Save or revert the source draft before generating this asset." if changed else
              "Wait for the current task to finish. This button will enable automatically." if busy() and not queueable else
              unavailable_reason if not available else None)
    clicked = generation_button(label, key=f"retry-asset-{key}", disabled=changed or (not queueable if busy() else not available),help=reason,
                         activity=activity, container=generate,
                         **({"on_click":generate_current} if stable_target else {}))
    if clicked and not stable_target:
        generate_current()
    if changed:
        st.caption("Save or revert the source draft before generating this asset.")
    elif reason and stable_target and not activity:
        st.caption("Cannot generate: "+reason)
    if record and key in state.asset_modifications and stage in ("character_references", "backgrounds", "scene_images"):
        from .visual_generation import DESIGN_MODEL
        configured = "Demo (offline)" if engine.demo else f"openai / {DESIGN_MODEL}" if stage == "character_references" else f"{engine.settings.image_provider} / {engine.settings.image_model}"
        if stage == "scene_images" and not engine.demo:
            from .scene_image_references import SCENE_IMAGE_MODEL
            configured = f"OpenAI / {SCENE_IMAGE_MODEL}"
        st.caption(f"This asset has a saved OpenAI edit. Regenerate / retry uses {configured}; use Modify for another OpenAI image edit.")


def character_cards(engine, state, *, voice_editor=None, costumes_editor=None):
    try:
        characters = engine.creative.artifact("characters").characters
    except (OSError, ValueError):
        st.info("Complete the Characters stage to see character assets here.")
        return
    available = not busy() and engine.can_generate("character_references", state)
    from .asset_view_ui import character_summary
    slots = []
    # Send the complete list before reading/displaying any card's media.
    for character in characters:
        approved, _, _ = character_summary(engine, character.character_id, state)
        slot = st.empty()
        slot.markdown("**" + character.name + "** · " + (":green[Approved]" if approved else ":orange[Pending]"))
        slots.append(slot)
    for slot, character in zip(slots, characters):
        target = f"character-card-{engine.project_id}-{character.character_id}"
        with slot.container():
            st.fragment(key=target)(_character_card)(engine, character.character_id, available, voice_editor, costumes_editor, target)


def _character_card(engine, cid, available, voice_editor, costumes_editor, target):
    from .asset_view_ui import character_summary, subject_signature, poll_subject, belongs
    from .ui_tasks import workspace_tasks, collect
    from .identity import IdentityManager
    from .ui_tasks import take_task_result
    message_key = f"character-card-message-{engine.project_id}-{cid}"
    identity = IdentityManager(engine.manager, engine.project_id).load()
    costumes = [c.costume_id for c in identity.costumes.values() if c.character_id == cid] if identity else []
    for task in list(workspace_tasks()):
        if task["future"].done() and belongs(task, engine, cid, costumes):
            collect(task)
            if task.get("ui_owned_asset_project") == engine.project_id:
                result = take_task_result(engine.project_id, [task.get("generation_button_key") or task["label"]])
                if result:
                    st.session_state[message_key] = result
    baseline = subject_signature(engine, "character_references", cid)
    state = engine.load()
    available = engine.can_generate("character_references", state)
    character = next((c for c in engine.creative.artifact("characters").characters if c.character_id == cid), None)
    if character is None:
        return
    approved, _, _ = character_summary(engine, cid, state)
    label = "**" + character.name + "** · " + (":green[Approved]" if approved else ":orange[Pending]")
    from .ui_panels import persistent_expander
    panel = persistent_expander(label, expanded=not approved,
                       key=f"character-library-card-{engine.project_id}-{cid}")
    if panel.open:
        with panel:
            message = st.session_state.get(message_key)
            if message and message[0][0] == "error":
                render_error(message[0][1], project_path=engine.path, task_key=message[1].get("task"), key=message_key + "-error")
            # Usage scans are needed only for an opened card. Removal rechecks
            # the canonical files under its lock before deleting anything.
            removal_status = None
            manifest = engine.manager.load(engine.project_id)
            if manifest.workflow_version == "world_episode" and not manifest.world_project_id:
                try:
                    from .asset_view_ui import removal_statuses
                    removal_status = removal_statuses(engine).get(cid)
                except (OSError, ValueError) as exc:
                    st.warning(str(exc))
            character_approval_controls(engine, character, removal_status)
            if voice_editor is not None and costumes_editor is not None:
                character_tabs(engine, state, character, available, voice_editor, costumes_editor)
            else:
                character_visual(engine, state, character, available=available)
    poll_subject(engine, "character_references", cid, target, baseline)


@st.fragment
def character_approval_controls(engine, character, removal_status=None):
    cid = character.character_id
    # Voice/costume generators finish locally. Refresh only these controls so
    # their reasons and enabled state update without rebuilding the card list.
    state = engine.load()
    from .asset_view_ui import character_summary
    approved, requirements, hint = character_summary(engine, cid, state)
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        if st.button("Approved" if approved else "Approve character", key="approve-character-" + cid,
                     disabled=busy() or approved or not all(requirements.values()),
                     help=hint + ("\n\nWait for the current task to finish." if busy() else "")):
            expected_asset_id = state.records["character_references:" + cid].asset.asset_id
            task = launch(lambda: engine.approve_character(cid, expected_asset_id=expected_asset_id),
                          "Approving character " + cid, rerun_on_start=False)
            if task:
                task.update(asset_subject=cid, generation_button_project=engine.project_id,
                            ui_owned_asset_project=engine.project_id)
        if removal_status is not None:
            from .character_removal_ui import remove_button
            remove_button(engine, character, removal_status, show_reason=False)
    st.caption(" · ".join(part + (": ready" if done else ": missing or outdated") for part, done in requirements.items()))
    if removal_status is not None and not removal_status.allowed:
        st.caption(removal_status.reason)


@st.fragment
def character_tabs(engine, state, character, available, voice_editor, costumes_editor):
    # Keep tab changes local so the surrounding cards and their expanded state
    # are not rebuilt, which can move the browser's scroll position.
    visual, voice, costumes = st.tabs(["Visual", "Voice", "Costumes"],
        key=f"page-tab-character-{engine.project_id}-{character.character_id}", on_change="rerun")
    if visual.open:
        with visual:
            character_visual(engine, state, character, available=available)
    if voice.open:
        with voice:
            voice_editor(character)
    if costumes.open:
        with costumes:
            costumes_editor(character)


@st.fragment
def character_visual(engine, state, character, *, available):
    from .ui_tasks import find_task, collect, queue_status
    task = find_task(lambda item: item.get("asset_engine") is not None and item["asset_engine"].project_id == engine.project_id or
                     item.get("visual_design_project") == engine.project_id and item.get("visual_design_stage") == "character_references")
    if task and task["future"].done():
        collect(task)
    state = engine.load()
    cid = character.character_id
    key = "character_references:" + cid
    record = state.records.get(key)
    st.caption(f"{cid} · {record.status if record else 'pending'} · revision {record.revision if record else 0}")
    if record and record.status == "running":
        st.status("Generating…", state="running")
    has_image = False
    if record and record.asset:
        try:
            path = engine.local_path(record.asset.file_path)
            if path.is_file():
                has_image = True
                image_preview(str(path), width="stretch")
                st.download_button("Download", lambda path=path: path.read_bytes(), file_name=path.name, key=f"download-asset-{key}")
            else:
                st.warning("The asset image is missing.")
        except (OSError, ValueError) as exc:
            st.warning(str(exc))
    if record and record.error:
        render_error(record.error, project_path=engine.path, task_key=f"assets:character_references:{cid}", key=report_widget_key('character_assets_ui:296', locals()))
    if record and record.status == "stale":
        st.warning(record.stale_reason or "Saved image available, but generation inputs changed. Apply a revision or regenerate, then approve.")
    revision_browser(engine, "character_references", cid)
    changed = draft_changed(engine, "character_references", cid, character)
    generation_controls(engine, state, "character_references", cid, has_image, changed, available=available)
    if task and task.get("generation_button_key") in (f"retry-asset-character_references:{cid}",
            f"visual-generation-{engine.project_id}-character_references-{cid}-design") and not task["future"].done():
        queue_status(task, key="visual-queue-" + engine.project_id + "-" + cid)
    character_details(engine, character, record)


def asset_cards(engine, state, stage):
    if stage == "scene_images":
        from .scene_image_cards_ui import render
        render(engine)
        return
    try:
        if stage == "backgrounds":
            items = [(loc.location_id, loc.name, loc) for loc in engine.creative.artifact("world").locations]
        elif stage == "scene_images":
            items = [(scene.scene_id, scene.visual_description, read_source(engine.creative, stage, scene.scene_id))
                     for scene in engine.creative.artifact("storyboard").scenes]
        else:
            voice_tasks={task.subject:task for task in engine.tasks(state) if task.stage=="voices"}
            items = [(task.subject,f"{task.scene_id} · {task.speaker}: {task.prompt}",read_source(engine.creative,stage,task.subject))
                for task in voice_tasks.values()]
    except (OSError, ValueError):
        st.info("Generate the related creative stages to see assets here.")
        return
    if not items:
        st.info("No assets available yet. Complete the creative stages to unlock them.")
    try:
        active = {task.key for task in engine.tasks(state)}
    except (OSError, ValueError):
        active = set()
    available = not busy() and engine.can_generate(stage, state)
    for subject, name, source in items:
        key = f"{stage}:{subject}"
        record = state.records.get(key)
        with st.container(border=True):
            st.markdown(f"**{subject} · {record.status if record else 'pending'} · revision {record.revision if record else 0}**")
            if record and record.status == "running":
                st.status("Generating…", state="running")
            st.caption(name)
            if stage=="voices":
                from .voice_lines_ui import editor
                editor(engine,state,subject,task=voice_tasks[subject])
            has_asset = False
            if record and record.asset:
                try:
                    path = engine.local_path(record.asset.file_path)
                    if path.is_file():
                        has_asset = True
                        if stage == "voices":
                            st.audio(str(path))
                        else:
                            image_preview(str(path), width="stretch")
                        st.download_button("Download", path.read_bytes(), file_name=path.name, key=f"download-asset-{key}")
                    else:
                        st.warning("The asset file is missing.")
                except (OSError, ValueError) as exc:
                    st.warning(str(exc))
            if record and record.error:
                render_error(record.error, project_path=engine.path, task_key=f"assets:{stage}:{subject}", key=report_widget_key('character_assets_ui:418', locals()))
            if stage == "backgrounds":
                if record and record.status == "stale":
                    st.warning(record.stale_reason or "Apply a saved revision or regenerate, then approve.")
                revision_browser(engine, stage, subject)
            changed = draft_changed(engine, stage, subject, source)
            generation_controls(engine, state, stage, subject, has_asset, changed, available=available and key in active)
            character_details(engine, source, record, stage=stage, subject=subject)
            if key not in active:
                st.caption("This source is not used by the current approved scene plan. Complete the creative stages to unlock generation.")


@st.fragment
def revision_browser(engine, stage, subject, *, apply_callback=None, apply_hint=None, launch_callback=None, in_dialog=False, allow_apply=True, allow_delete=True):
    state = engine.load()
    current = state.records.get(f"{stage}:{subject}")
    prefix = f"reference-revisions-{engine.project_id}-{stage}-{subject}"
    current_id = current.asset.asset_id if current and current.asset else None
    gate = "characters" if stage == "character_references" else "backgrounds"
    approval = engine.character_approved(subject, state) if stage == "character_references" else engine.manager.load(engine.project_id).asset_approvals.get(gate)
    if stage != "scene_images":
        st.caption("Approval: Approved" if approval and current and current.status == "succeeded" else "Approval: Needs approval after generating or applying a revision")
    panel = st.container() if in_dialog else st.expander("View / apply saved revisions", key=prefix + "-panel", on_change="rerun")
    if not in_dialog and not panel.open:
        return
    history = engine.revisions(stage, subject)
    if not history:
        panel.caption("No saved revisions yet.")
        return
    with panel:
        choices = {r.asset.asset_id: r for r in history}
        if st.session_state.pop(prefix + "-reset-choice", False) or st.session_state.get(prefix + "-choice") not in choices:
            st.session_state.pop(prefix + "-choice", None)
        def label(asset_id):
            record = choices[asset_id]
            return revision_label(record) + (" · Active" if asset_id == current_id else "")
        selected = st.selectbox("Preview revision", list(choices), format_func=label,
                                key=prefix + "-choice", disabled=busy())
        record = choices[selected]
        st.caption(f"Revision {record.revision} · {record.status} · {record.asset.provider} / {record.asset.model}")
        intact = engine._intact(record)
        if intact:
            image_preview(str(engine.local_path(record.asset.file_path)), caption=label(selected), width="stretch")
        else:
            st.warning("This revision's file is missing or modified and cannot be applied.")
        st.caption(apply_hint or "Previewing does not change the active image. Apply selects this image for the current settings and requires a new reference approval. Applying does not call a generation API.")
        already_applied = selected == current_id and current and current.status == "succeeded"
        task = st.session_state.get("workspace-task")
        saving_label = f"Applying {stage} {subject} revision {record.revision}"
        saving = bool(task and not task["future"].done() and task["label"] == saving_label)

        def apply():
            callback = lambda: apply_callback(selected) if apply_callback else engine.apply_revision(stage, subject, selected)
            if launch_callback:
                launch_callback(callback, saving_label)
            else:
                task = launch(callback, saving_label, inline=True, rerun_on_start=False)
                if task:
                    from .background_library import BackgroundLibrary
                    owner = BackgroundLibrary(engine).subject_id(subject) if stage == "backgrounds" else subject
                    task.update(asset_subject=owner, generation_button_project=engine.project_id,
                                ui_owned_asset_project=engine.project_id)

        st.button("Applying…" if saving else f"Applied revision {record.revision}" if already_applied else f"Apply revision {record.revision}",
                  key=prefix + "-apply", on_click=apply, disabled=busy() or not intact or bool(already_applied) or not allow_apply)
        def delete():
            def deleted(_):
                st.session_state[prefix + "-reset-choice"] = True
            task = launch(lambda: engine.delete_revision(stage, subject, selected),
                          f"Deleting {stage} {subject} revision {record.revision}", deleted,
                          inline=True, rerun_on_start=False)
            if task:
                from .background_library import BackgroundLibrary
                owner = BackgroundLibrary(engine).subject_id(subject) if stage == "backgrounds" else subject
                task.update(asset_subject=owner, generation_button_project=engine.project_id,
                            ui_owned_asset_project=engine.project_id)
        if allow_delete:
            st.button("Delete revision", key=prefix + "-delete", disabled=busy(), on_click=delete)
            st.caption("Remaining revisions are renumbered from 1. Deleting the active revision clears its selection and requires a replacement and approval. Existing episode media is retained.")
        with st.expander("Revision generation details", expanded=in_dialog):
            st.json(record.asset.model_dump(mode="json", by_alias=True))
