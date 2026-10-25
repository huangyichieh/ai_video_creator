"""Character reference cards and drafts backed by the canonical character bible."""
import difflib

import streamlit as st

from .files import input_hash
from .proofreading import TypoSuggestion, apply_suggestion, check_typos
from .ui_tasks import busy, launch, launch_assets, can_queue_asset, queue_asset
from .asset_sources import read_source, edit_source
from .media_utils import text_chunks
from .action_labels import text_action_label, asset_modify_label
# Imported lazily when opening the shared dialog to avoid UI module cycles.


def detail_prefix(engine, stage, subject):
    return (f"asset-character-{engine.project_id}-{subject}" if stage == "character_references"
            else f"asset-editor-{stage}-{engine.project_id}-{subject}")


def draft_changed(engine, stage, subject, source):
    prefix = detail_prefix(engine, stage, subject)
    saved = source.model_dump_json(by_alias=True, indent=2)
    return st.session_state.get(prefix + "-draft", saved) != st.session_state.get(prefix + "-baseline", saved)


def character_details(engine, character, record, *, stage="character_references", subject=None):
    cid = subject or character.character_id
    is_character = stage == "character_references"
    source = {"character_references": "characters", "backgrounds": "world", "scene_images": "prompts", "voices": "storyboard"}[stage]
    prefix = detail_prefix(engine, stage, cid)
    saved = character.model_dump_json(by_alias=True, indent=2)
    baseline_key, draft_key, report_key = prefix + "-baseline", prefix + "-draft", prefix + "-typos"
    st.session_state.setdefault(baseline_key, saved)
    st.session_state.setdefault(draft_key, saved)
    baseline = st.session_state[baseline_key]

    def reset():
        st.session_state[baseline_key] = saved
        st.session_state[draft_key] = saved
        st.session_state.pop(report_key, None)
        st.session_state.pop(prefix + "-error", None)

    with st.expander("Generation details"):
        st.caption("Edit canonical source JSON. Saving updates related files and invalidates affected downstream stages, approvals, and assets.")
        if stage == "voices":
            st.caption("This is the source storyboard scene shared by its speech tracks. Edit narration or dialogue here; style instructions below control delivery without changing spoken text.")
        draft = st.text_area("Character JSON" if is_character else "Source JSON", key=draft_key, height=350, disabled=busy())
        changed = draft != baseline
        if baseline != saved:
            st.warning("The saved source changed elsewhere. Revert all changes to load its latest JSON, or keep your draft for comparison.")
        if changed:
            st.caption("Modified — not saved")
            st.code("\n".join(difflib.unified_diff(baseline.splitlines(), draft.splitlines(),
                                                fromfile="Saved source", tofile="Draft source", lineterm="")), language="diff")
        else:
            st.caption("No unsaved changes")
        save, revert, typo, modify = st.columns(4)
        if modify.button(text_action_label("Modify", engine.creative), key=prefix + "-modify", disabled=busy() or changed or baseline != saved):
            from .modification_ui import open_modification
            open_modification(engine.project_id, "source", stage, subject=cid, expected=saved)
        if save.button("Save changes", key=prefix + "-save", disabled=busy() or not changed, type="primary"):
            def saved_draft(_):
                latest = (next(c for c in engine.creative.artifact("characters").characters if c.character_id == cid)
                          if is_character else read_source(engine.creative, stage, cid))
                formatted = latest.model_dump_json(by_alias=True, indent=2)
                st.session_state[baseline_key] = formatted
                st.session_state[draft_key] = formatted
                st.session_state.pop(report_key, None)

            launch(lambda: engine.creative.edit_character(cid, draft, expected=baseline) if is_character
                   else edit_source(engine.creative, stage, cid, draft, expected=baseline),
                   f"Saving {cid}", saved_draft)
        revert.button("Revert all changes", key=prefix + "-revert", disabled=busy() or not (changed or baseline != saved), on_click=reset)
        if typo.button("Check typos", key=prefix + "-check", disabled=busy()):
            checked_values = {source: draft}

            def checked(report):
                st.session_state[report_key] = {"draft_hash": input_hash(checked_values), "report": report.model_dump()}

            launch(lambda: check_typos(engine.creative, checked_values, source), f"Checking typos for {cid}", checked)
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
                except ValueError as exc:
                    st.session_state[prefix + "-error"] = str(exc)

            error = st.session_state.pop(prefix + "-error", None)
            if error:
                st.error(error)
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
            st.json(record.model_dump(mode="json", by_alias=True))
    return changed


def generation_controls(engine, state, stage, subject, has_asset, changed, *, available=True):
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
    if modify.button(asset_modify_label(engine, stage), key=f"modify-asset-{key}", disabled=busy() or changed or not has_asset or not available):
        from .modification_ui import open_modification
        open_modification(engine.project_id, "asset", stage, subject=subject, expected=record.asset.asset_id)
    prompt.text_input("Style prompt", key=style_key, on_change=save_style, disabled=busy(),
                      help="Saved locally for this asset. Applies to individual and batch generation. For voices, describes delivery and emotion; it is not spoken.")
    label = "Retry asset" if record and record.status == "failed" else "Regenerate asset" if has_asset else "Generate character" if stage == "character_references" else "Generate asset"
    queueable = can_queue_asset(engine, stage, subject)
    if queueable:
        label = "Queue asset"
    if generate.button(label, key=f"retry-asset-{key}", disabled=changed or (not queueable if busy() else not available)):
        if busy():
            queue_asset(engine, stage, subject, force=has_asset)
        launch_assets(engine, lambda: engine.generate(stage, subject=subject, force=has_asset), f"Generating {subject}")
    if changed:
        st.caption("Save or revert the source draft before generating this asset.")
    if record and key in state.asset_modifications and stage in ("character_references", "backgrounds", "scene_images"):
        configured = "Demo (offline)" if engine.demo else f"{engine.settings.image_provider} / {engine.settings.image_model}"
        st.caption(f"This asset has a saved OpenAI edit. Regenerate / retry uses {configured}; use Modify for another OpenAI image edit.")


def character_cards(engine, state):
    try:
        characters = engine.creative.artifact("characters").characters
    except (OSError, ValueError):
        st.info("Complete the Characters stage to see character assets here.")
        return
    available = not busy() and engine.can_generate("character_references", state)
    for character in characters:
        cid = character.character_id
        key = f"character_references:{cid}"
        record = state.records.get(key)
        with st.container(border=True):
            st.markdown(f"**{cid} · {record.status if record else 'pending'} · revision {record.revision if record else 0}**")
            if record and record.status == "running":
                st.status("Generating…", state="running")
            st.caption(character.name)
            has_image = False
            if record and record.asset:
                try:
                    path = engine.local_path(record.asset.file_path)
                    if path.is_file():
                        has_image = True
                        st.image(str(path), width="stretch")
                        st.download_button("Download", path.read_bytes(), file_name=path.name, key=f"download-asset-{key}")
                    else:
                        st.warning("The asset image is missing.")
                except (OSError, ValueError) as exc:
                    st.warning(str(exc))
            if record and record.error:
                st.error(record.error)
            if record and record.status == "stale":
                st.warning(record.stale_reason or "Saved image available, but generation inputs changed. Apply a revision or regenerate, then approve.")
            revision_browser(engine, "character_references", cid)
            changed = draft_changed(engine, "character_references", cid, character)
            generation_controls(engine, state, "character_references", cid, has_image, changed, available=available)
            character_details(engine, character, record)


def asset_cards(engine, state, stage):
    try:
        if stage == "backgrounds":
            items = [(loc.location_id, loc.name, loc) for loc in engine.creative.artifact("world").locations]
        elif stage == "scene_images":
            items = [(scene.scene_id, scene.visual_description, read_source(engine.creative, stage, scene.scene_id))
                     for scene in engine.creative.artifact("storyboard").scenes]
        else:
            items = []
            for scene in engine.creative.artifact("storyboard").scenes:
                lines = ([("narrator", scene.narration)] if scene.narration.strip() else [])
                lines += [(line.character_id, line.text) for line in scene.dialogue]
                for line_index, (speaker, text) in enumerate(lines):
                    for chunk_index, chunk in enumerate(text_chunks(text)):
                        subject = f"{scene.scene_id}_{line_index:04d}_{chunk_index:04d}"
                        items.append((subject, f"{scene.scene_id} · {speaker}: {chunk}", read_source(engine.creative, stage, subject)))
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
            has_asset = False
            if record and record.asset:
                try:
                    path = engine.local_path(record.asset.file_path)
                    if path.is_file():
                        has_asset = True
                        if stage == "voices":
                            st.audio(str(path))
                        else:
                            st.image(str(path), width="stretch")
                        st.download_button("Download", path.read_bytes(), file_name=path.name, key=f"download-asset-{key}")
                    else:
                        st.warning("The asset file is missing.")
                except (OSError, ValueError) as exc:
                    st.warning(str(exc))
            if record and record.error:
                st.error(record.error)
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
def revision_browser(engine, stage, subject):
    state = engine.load()
    current = state.records.get(f"{stage}:{subject}")
    history = engine.revisions(stage, subject)
    if not history:
        return
    prefix = f"reference-revisions-{engine.project_id}-{stage}-{subject}"
    current_id = current.asset.asset_id if current and current.asset else None
    gate = "characters" if stage == "character_references" else "backgrounds"
    approval = engine.manager.load(engine.project_id).asset_approvals.get(gate)
    st.caption("Approval: Approved" if approval and current and current.status == "succeeded" else "Approval: Needs approval after generating or applying a revision")
    with st.expander("View / apply saved revisions", key=prefix + "-panel"):
        choices = {r.asset.asset_id: r for r in history}
        def label(asset_id):
            record = choices[asset_id]
            return f"Revision {record.revision}" + (" · Active" if asset_id == current_id else "")
        selected = st.selectbox("Preview revision", list(choices), format_func=label,
                                key=prefix + "-choice", disabled=busy())
        record = choices[selected]
        intact = engine._intact(record)
        if intact:
            st.image(str(engine.local_path(record.asset.file_path)), caption=label(selected), width="stretch")
        else:
            st.warning("This revision's file is missing or modified and cannot be applied.")
        st.caption("Previewing does not change the active image. Apply selects this image for the current settings and requires a new reference approval. Applying does not call a generation API.")
        already_applied = selected == current_id and current and current.status == "succeeded"
        task = st.session_state.get("workspace-task")
        saving_label = f"Applying {stage} {subject} revision {record.revision}"
        saving = bool(task and not task["future"].done() and task["label"] == saving_label)

        def apply():
            launch(lambda: engine.apply_revision(stage, subject, selected), saving_label, inline=True, rerun_on_start=False)

        st.button("Applying…" if saving else f"Applied revision {record.revision}" if already_applied else f"Apply revision {record.revision}",
                  key=prefix + "-apply", on_click=apply, disabled=busy() or not intact or bool(already_applied))
        with st.expander("Revision generation details"):
            st.json(record.asset.model_dump(mode="json", by_alias=True))
