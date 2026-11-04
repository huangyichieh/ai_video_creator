"""Editable finishing plan, previews, and checkbox-based generation/retries."""
from .error_report_ui import render_error, error_project, report_widget_key
from .generation_ui import generation_button
import json
from uuid import uuid4

import streamlit as st

from .finishing_models import FinishingPlan, EffectLayer
from .ui_tasks import busy, launch, launch_render
from .files import input_hash
from .action_labels import action_label, text_action_label


def finishing_panel(engine, render, *, section=None):
    service = engine.finishing
    state = service.load()
    plan = state.plan
    pid = engine.project_id + ("-" + section if section else "")
    production = engine.assets.load()
    if not production.scenes:
        st.caption("Effects become available after Voices & input exports are ready.")
        return
    try:
        board = engine.assets.creative.artifact("storyboard").scenes
    except (OSError, ValueError):
        st.caption("Approve the episode Storyboard before configuring effects.")
        return
    if any(scene.scene_id not in production.scenes or scene.scene_id not in render.music_plan.scenes for scene in board):
        st.caption("Approve Voices & input before configuring effects.")
        return
    draft_key = f"finishing-draft-{pid}"
    typo_key = f"finishing-typos-{pid}"
    fields = {"faceTargets": [f.model_dump(mode="json", by_alias=True) for f in plan.face_targets],
              "effects": [e.model_dump(mode="json", by_alias=True) for e in plan.effects]}
    baseline = json.dumps(fields, ensure_ascii=False, indent=2)

    def setting(name, value):
        key = f"finishing-setting-{pid}-{name}"
        st.session_state.setdefault(key, value)
        return key

    with st.expander("Generated VFX layers" if section == "video" else "Generated SFX and VFX layers"):
        st.caption("Local VFX are transparent procedural layers; OpenAI VFX use a green-screen layer. Compose scenes on Scene review & continuity to apply enabled effects." if section == "video" else
                   "ElevenLabs generates unassigned sound cues. Local VFX are transparent procedural layers; OpenAI VFX use a green-screen layer. Scene composition applies enabled finishing.")
        lip_key = setting("lip", plan.lip_sync)
        model_key = setting("model", plan.lip_model)
        sound_key = setting("sound", plan.sound_provider)
        st.session_state[lip_key] = False
        if plan.lip_sync:
            st.warning("Sync has been removed from the UI. Save finishing settings to disable this older lip-sync configuration before generating.")
        if section != "video":
            st.selectbox("Sound effect provider", ("local", "elevenlabs"), key=sound_key, disabled=busy(),
                         format_func=lambda value: "Local placeholders" if value == "local" else "ElevenLabs generated SFX / ambience")
            st.caption("Store ELEVENLABS_API_KEY in the local .env file. Optional rate settings record USD estimates; provider invoices are separate from OpenAI billing.")
            st.caption("Use concrete sound descriptions (source, action and texture), not dialogue. Non-English descriptions are translated before generation; translation usage is recorded. Preview each result before approving scenes.")
            with st.expander("Edit sound descriptions and timing"):
                st.caption('Edit each cue\'s prompt and duration in seconds. Use ambience with loop=true for continuous backgrounds; very short clips can cut off footsteps or ambience. Save, then select the SFX items and regenerate below.')
                from .final_ui import sound_plan
                sound_plan(engine, render, pid)
        st.session_state.setdefault(draft_key, baseline)

        with st.expander("Add an effect layer"):
            sid = st.selectbox("Effect scene", [s.scene_id for s in board], key=setting("effect-scene", board[0].scene_id))
            kind = st.selectbox("Effect type", ("rain", "snow", "smoke", "sparks", "fire", "lightning", "energy", "hud", "openai"), key=setting("effect-kind", "rain"))
            st.caption("Local types use kind, intensity and seed. The text prompt is used by OpenAI VFX; green-screen quality should be reviewed before compositing.")
            prompt = st.text_input("VFX generation prompt", key=setting("effect-prompt", ""), disabled=busy())
            at = st.number_input("Effect start (seconds)", min_value=0., step=.1, key=setting("effect-start", 0.))
            duration = st.number_input("Effect duration (seconds)", min_value=.1, max_value=30., step=.1,
                                       key=setting("effect-duration", float(min(3, production.scenes[sid].duration))))
            opacity = st.slider("Effect opacity", 0., 1., key=setting("effect-opacity", .5))
            def add_effect():
                try:
                    data = json.loads(st.session_state[draft_key])
                    effect = EffectLayer(effect_id=f"effect_{uuid4().hex[:10]}", scene_id=sid, kind=kind,
                                         prompt=prompt, at=at, duration=duration, opacity=opacity)
                    data.setdefault("effects", []).append(effect.model_dump(mode="json", by_alias=True))
                    st.session_state[draft_key] = json.dumps(data, ensure_ascii=False, indent=2)
                except ValueError as exc:
                    st.session_state["workspace-task-message"] = ("error", str(exc))
            st.button("Add layer to draft", key=f"finishing-add-{pid}", disabled=busy() or kind == "openai" and not prompt.strip(), on_click=add_effect)

        markdown = engine.path / "finishing_plan.md"
        if markdown.exists():
            st.markdown(markdown.read_text(encoding="utf-8"))
        text = st.text_area("Face targets / VFX layers JSON", key=draft_key, height=260, disabled=busy())
        changed = (text != baseline or st.session_state[lip_key] != plan.lip_sync
                   or st.session_state[model_key] != plan.lip_model or st.session_state[sound_key] != plan.sound_provider)
        if changed:
            st.caption("Modified - not saved")
        def clear(_=None):
            st.session_state.pop(typo_key, None)
            for key in (draft_key, lip_key, model_key, sound_key):
                st.session_state.pop(key, None)
        save, revert, modify, typo = st.columns(4)
        if save.button("Save finishing settings", key=f"finishing-save-{pid}", disabled=busy() or not changed):
            try:
                data = json.loads(text)
                updated = FinishingPlan(lip_sync=st.session_state[lip_key], lip_model=st.session_state[model_key],
                    sound_provider=st.session_state[sound_key], **data)
                expected = plan.model_dump_json(by_alias=True)
                launch(lambda: service.configure(updated, expected=expected), "Saving finishing settings", clear)
            except (TypeError, ValueError) as exc:
                render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('finishing_ui:103', locals()))
        def reset():
            st.session_state.pop(typo_key, None)
            st.session_state.update({draft_key: baseline, lip_key: plan.lip_sync, model_key: plan.lip_model, sound_key: plan.sound_provider})
        revert.button("Revert all changes", key=f"finishing-revert-{pid}", disabled=busy() or not changed, on_click=reset)
        from .modification_ui import open_modification
        modify.button(text_action_label("Modify", engine.assets.creative, "finishing_plan"), key=f"finishing-modify-{pid}", disabled=busy() or changed, on_click=open_modification, args=(engine.project_id, "finishing_plan", "finishing",), kwargs={'expected': plan.model_dump_json(by_alias=True), 'rerun': False})
        if typo.button("Check typos", key=f"finishing-typo-check-{pid}", disabled=busy()):
            from .proofreading import check_typos
            def checked(report):
                st.session_state[typo_key] = {"hash": input_hash(text), "report": report.model_dump(), "applied": []}
            launch(lambda: check_typos(engine.assets.creative, {"finishing": text}, "finishing"), "Checking effect prompts", checked)
        report = st.session_state.get(typo_key)
        if report:
            stale = report["hash"] != input_hash(text)
            st.write(report["report"]["summary"])
            if stale:
                st.caption("Draft changed; check typos again for current recommendations.")
            for index, suggestion in enumerate(report["report"]["suggestions"]):
                st.write(f"{suggestion['original']} → {suggestion['replacement']}")
                def apply(index=index):
                    from .proofreading import TypoSuggestion, apply_suggestion
                    saved = st.session_state[typo_key]
                    current = st.session_state[draft_key]
                    if saved["hash"] != input_hash(current):
                        return
                    try:
                        result = apply_suggestion({"finishing": current}, TypoSuggestion.model_validate(saved["report"]["suggestions"][index]))
                        st.session_state[draft_key] = result
                        saved["hash"] = input_hash(result)
                        saved["applied"].append(index)
                    except ValueError as exc:
                        st.session_state["workspace-task-message"] = ("error", str(exc))
                st.button("Applied" if index in report["applied"] else "Apply suggestion", key=f"finishing-typo-apply-{pid}-{index}",
                          on_click=apply, disabled=busy() or stale or index in report["applied"])
        tasks = service.tasks(production, board, render)
        if section == "video":
            tasks = {key: value for key, value in tasks.items() if not key.startswith("sfx:")}
        if tasks:
            rows = []
            picked_key = f"finishing-picked-{pid}"
            picked = set(st.session_state.get(picked_key, []))
            for key, (sid, label, _) in tasks.items():
                record = state.records.get(key)
                rows.append({"Select": key in picked, "Item": key, "Scene": sid, "Subject": label,
                    "Status": "Generating…" if record and record.status == "running" else "outdated" if record and record.status == "succeeded" and not service.task_intact(record, tasks[key][2]) else record.status if record else "pending",
                    "Revision": record.revision if record else 0, "Cost (USD)": f"{record.recorded_cost:.2f}" if record and record.pricing_known else "Unknown" if record else "—",
                    "Attempts": record.attempts if record else 0, "Error": record.error or "" if record else ""})
            table_key = f"finishing-table-{pid}"
            def remember():
                selection = set(st.session_state.get(picked_key, []))
                for index, edits in st.session_state[table_key].get("edited_rows", {}).items():
                    if "Select" in edits and int(index) < len(rows):
                        key = rows[int(index)]["Item"]
                        selection.add(key) if edits["Select"] else selection.discard(key)
                st.session_state[picked_key] = sorted(selection)
            selected = st.data_editor(rows, key=table_key, on_change=remember, hide_index=True, width="stretch",
                disabled=True if busy() else ["Item", "Scene", "Subject", "Status", "Revision", "Cost (USD)", "Attempts", "Error"],
                column_config={"Select": st.column_config.CheckboxColumn("Select")})
            keys = [row["Item"] for row in selected if row["Select"]]
            retry, regenerate = st.columns(2)
            if generation_button("Generate / retry selected", key=f"finishing-retry-{pid}", disabled=busy() or changed or not keys, container=retry):
                launch_render(engine, lambda: service.generate_selected(keys, force=False), "Generating finishing assets")
            if generation_button("Regenerate selected", key=f"finishing-regenerate-{pid}", disabled=busy() or changed or not keys, container=regenerate):
                launch_render(engine, lambda: service.generate_selected(keys, force=True), "Regenerating finishing assets")
            st.caption("Retry reuses intact results and resumes saved jobs. Regenerate requests new versions. Finishing runs one task at a time; Stop finishes the current task and preserves results. To edit a sound prompt, use prompt in the Music / SFX plan.")
        for key, record in state.records.items():
            if section == "video" and key.startswith("sfx:"):
                continue
            from .ui_panels import persistent_expander
            panel = persistent_expander(f"{key} · {record.status} · revision {record.revision}",
                key=f"finishing-effect-panel-{pid}-{key}")
            if not panel.open:
                continue
            with panel:
                if record.error:
                    render_error(record.error, project_path=engine.path, task_key=f"finishing:{key}", key=f"finishing-error-{pid}-{key}")
                if key not in tasks:
                    st.caption("Previous item; not part of the current finishing selection.")
                if record.asset:
                    preview = record.asset.generation_params.get("previewFile") or record.asset.file_path
                    file = engine.assets.local_path(preview)
                    if file.exists():
                        st.audio(str(file)) if record.asset.type == "audio" else st.video(str(file))
                    if key in tasks and key.startswith("vfx:") and record.asset.provider == "openai" and record.asset.generation_params.get("jobId"):
                        effect_id = key.partition(":")[2]
                        from .modification_ui import open_modification
                        st.button(action_label("Modify", record.asset.provider, record.asset.model), key=f"finishing-vfx-modify-{pid}-{effect_id}", disabled=busy() or changed, on_click=open_modification, args=(engine.project_id, "vfx", "vfx",), kwargs={'subject': effect_id, 'expected': record.asset.asset_id if record and record.asset else None, 'rerun': False})
                st.json(record.model_dump(mode="json", by_alias=True))
