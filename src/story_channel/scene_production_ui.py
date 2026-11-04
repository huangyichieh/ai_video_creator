"""Per-scene snapshot, editable direction suggestions and approved visual clips."""
from .error_report_ui import render_error, error_project, report_widget_key
from .generation_ui import generation_button
import json
import streamlit as st
from .scene_production import SceneProduction
from .rendering import RenderPipeline
from .ui_tasks import busy, launch, launch_render


def direction_cards(manager, project_id):
    service = SceneProduction(manager, project_id)
    if not service.director.load():
        st.info("Build the animation plan to prepare each scene's direction.")
        return
    try:
        service.director.validate_plan(service.director.load().plan)
    except (OSError, ValueError):
        st.info("Direction is outdated. Build the current animation plan before reviewing scenes.")
        return
    production = service.assets.load()
    for scene in service.assets.creative.artifact("storyboard").scenes:
        sid = scene.scene_id
        from .ui_panels import persistent_expander
        panel = persistent_expander(sid + " · " + ("Approved" if service.direction_approved(sid) else "Needs review"),
            key=f"scene-direction-panel-{project_id}-{sid}")
        if not panel.open:
            continue
        with panel:
            image = production.records.get("scene_images:" + sid)
            if image and image.asset and service.assets.local_path(image.asset.file_path).exists():
                st.image(str(service.assets.local_path(image.asset.file_path)), width=500)
            current = service.direction(sid)
            try:
                expected = service.direction_hash(sid)
            except ValueError as exc:
                st.info(str(exc))
                continue
            st.write(scene.visual_description)
            prompt = st.text_area("Direction request (optional)", key=f"studio-draft-scene-request-{project_id}-{sid}", disabled=busy())
            generate, review, modify = st.columns(3)
            for column, label, purpose in ((generate, "Generate direction", "Generate robust cinematic direction."),
                                            (review, "AI review", "Review direction and performance for clarity, continuity and typos."),
                                            (modify, "Modify with prompt", prompt)):
                if label == "AI review":
                    from .generation_ui import review_button
                    review_button(label,container=column,key=f"scene-direction-{label}-{project_id}-{sid}",disabled=busy(),
                        on_click=lambda sid=sid,purpose=purpose:launch(lambda:service.propose(sid,purpose),"Preparing scene direction suggestions"))
                elif column.button(label, key=f"scene-direction-{label}-{project_id}-{sid}", disabled=busy() or label == "Modify with prompt" and not prompt.strip()):
                    launch(lambda sid=sid, purpose=purpose: service.propose(sid, purpose), "Preparing scene direction suggestions")
            proposal = service.proposals().get(sid)
            pending_proposal = bool(proposal and proposal["sourceHash"] == expected and not proposal.get("savedHash"))
            selected, resolved = None, False
            choice_key = f"studio-review-choice-direction-{project_id}-{sid}-{expected[:8]}"
            if proposal and proposal["sourceHash"] == expected and not proposal.get("savedHash"):
                review_id = proposal.get("reviewId", "")
                choice_key += "-" + review_id
                left, right = st.columns(2)
                left.text_area("Original direction", value=json.dumps(proposal["original"], ensure_ascii=False, indent=2), disabled=True,
                               key=f"scene-original-{project_id}-{sid}-{review_id}", height=260)
                draft_key = f"studio-draft-scene-proposal-{project_id}-{sid}-{expected[:8]}-{review_id}"
                st.session_state.setdefault(draft_key, json.dumps(proposal["suggested"], ensure_ascii=False, indent=2))
                selected = right.text_area("Suggested direction — editable", key=draft_key, height=260, disabled=busy(),
                    on_change=lambda key=choice_key: st.session_state.pop(key, None))
                apply, revert = st.columns(2)
                apply.button("Apply changes", key=f"scene-apply-{project_id}-{sid}", disabled=busy(),
                    on_click=lambda key=choice_key: st.session_state.update({key: "apply"}))
                revert.button("Revert changes", key=f"scene-revert-{project_id}-{sid}", disabled=busy(),
                    on_click=lambda key=choice_key: st.session_state.update({key: "revert"}))
                resolved = st.session_state.get(choice_key) in ("apply", "revert")
            else:
                st.json(current.model_dump(mode="json", by_alias=True))
            approved = service.direction_approved(sid)
            save, unchanged = st.columns(2)
            if save.button("Save and approve", key=f"scene-save-direction-{project_id}-{sid}", disabled=busy() or not resolved):
                choice = st.session_state[choice_key]
                launch(lambda sid=sid, expected=expected, text=selected, choice=choice, rid=proposal.get("reviewId"): service.approve_direction(sid, expected=expected, text=text, choice=choice, review_id=rid), "Saving and approving scene direction")
            if unchanged.button("Approved" if approved and not pending_proposal else "Remain unchanged and approve", key=f"scene-keep-direction-{project_id}-{sid}", disabled=busy() or approved and not pending_proposal):
                launch(lambda sid=sid, expected=expected: service.approve_direction(sid, expected=expected), "Approving current scene direction")


def animated_scenes(manager, project_id):
    service = SceneProduction(manager, project_id)
    engine = RenderPipeline(manager, project_id)
    service.stop_requested = lambda: engine.stop_requested()
    render = engine.load()
    from .final_ui import video_settings
    video_settings(engine, render, project_id, animations=True)
    try:
        service.director.validate_plan(service.director.load(required=True).plan)
    except (OSError, ValueError):
        st.info("Build and approve the current direction plan first.")
        return
    st.caption("Generate silent scene motion from approved snapshots and direction, then approve each clip. Voices and inputs are added next; composition fits visual timing to recorded voices without another video API request.")
    production = service.assets.load()
    for scene in service.assets.creative.artifact("storyboard").scenes:
        sid = scene.scene_id
        with st.container(border=True):
            st.markdown("**" + sid + "**")
            image = production.records.get("scene_images:" + sid)
            if image and image.asset and service.assets.local_path(image.asset.file_path).exists():
                st.image(str(service.assets.local_path(image.asset.file_path)), width=400)
            if not service.director.load():
                st.info("Build and approve direction first.")
                continue
            current = service.visual_current(sid, render.options)
            approved = service.visual_current(sid, render.options, approved=True)
            record = service.visuals().get(sid)
            st.caption((record.get("status", "pending") if record else "pending") + (" · Approved" if approved else " · Not approved"))
            if record and record.get("asset") and service.assets.local_path(record["asset"]["filePath"]).exists():
                if not record.get("formats"):
                    st.video(str(service.assets.local_path(record["asset"]["filePath"])))
            if record and record.get("formats"):
                from .animation_formats import FORMAT_LABELS
                for name,version in record["formats"].items():
                    st.markdown("**"+FORMAT_LABELS[name]+"** · "+version["status"])
                    if version.get("asset"):
                        path = service.assets.local_path(version["asset"]["filePath"])
                        if path.is_file():
                            st.video(str(path))
                            st.download_button("Download "+FORMAT_LABELS[name],lambda path=path:path.read_bytes(),
                                file_name=f"{sid}_{name}.mp4",mime="video/mp4",key=f"visual-download-{project_id}-{sid}-{name}")
            if record and record.get("error"):
                render_error(record["error"], project_path=service.path, task_key=f"animation:visual:{sid}", key=report_widget_key('scene_production_ui:103', locals()))
            with st.expander("Direction and generation details"):
                st.json(service.direction(sid).model_dump(mode="json", by_alias=True))
                if record:
                    st.json(record)
            if generation_button("Regenerate animated scene" if current else "Generate animated scene", key=f"scene-generate-visual-{project_id}-{sid}",
                         disabled=busy() or not service.direction_approved(sid)):
                launch_render(engine, lambda sid=sid, force=current: service.generate_visual(sid, render.options, force=force), "Generating animated scene " + sid)
            try:
                expected = service.visual_hash(sid, render.options)
            except ValueError as exc:
                st.info(str(exc))
                continue
            if st.button("Approved" if approved else "Approve animated scene", key=f"scene-approve-visual-{project_id}-{sid}", disabled=busy() or not current or approved):
                launch(lambda sid=sid, expected=expected: service.approve_visual(sid, render.options, expected=expected), "Approving animated scene " + sid)
            st.caption("For AI review or prompt-guided changes, edit this scene's direction on Direction & performance, approve it, then regenerate the visual.")


def sound_effects(manager, project_id):
    engine = RenderPipeline(manager, project_id)
    render = engine.load()
    if not engine.assets.load().scenes:
        st.info("Generate and approve Voices & input first to set sound timing.")
        return
    board = engine.assets.creative.artifact("storyboard").scenes
    if any(scene.scene_id not in render.music_plan.scenes for scene in board):
        st.info("Approve scene inputs before configuring sound timing and effects.")
        return
    from .final_ui import sound_plan
    state = engine.finishing.load()
    provider = st.selectbox("Sound effect provider", ["local", "elevenlabs"], index=0 if state.plan.sound_provider == "local" else 1,
                            key=f"finishing-setting-{project_id}-sound-provider-separate", disabled=busy())
    if st.button("Save sound provider", key="sound-provider-save-" + project_id, disabled=busy() or provider == state.plan.sound_provider):
        plan = state.plan.model_copy(update={"sound_provider": provider})
        expected = state.plan.model_dump_json(by_alias=True)
        launch(lambda: engine.finishing.configure(plan, expected=expected), "Saving sound provider")
    sound_plan(engine, render, project_id + "-sound")
    tasks = {key: value for key, value in engine.finishing.tasks(engine.assets.load(), board, render).items() if key.startswith("sfx:")}
    rows = [{"Regenerate": False, "Item": key, "Scene": value[0], "Description": value[1],
             "Status": state.records[key].status if key in state.records else "pending"} for key, value in tasks.items()]
    if rows:
        selected = st.data_editor(rows, hide_index=True, key="sound-tasks-" + project_id,
                                  disabled=busy() or ["Item", "Scene", "Description", "Status"])
        keys = [row["Item"] for row in selected if row["Regenerate"]]
        if generation_button("Generate / regenerate selected sounds", key="sound-generate-" + project_id, disabled=busy() or not keys):
            launch_render(engine, lambda: engine.finishing.generate_selected(keys, force=True), "Generating selected sound effects")
    for key, record in state.records.items():
        if key.startswith("sfx:") and record.asset and engine.assets.local_path(record.asset.file_path).exists():
            with st.expander(key + " · revision " + str(record.revision)):
                st.audio(str(engine.assets.local_path(record.asset.file_path)))
                st.json(record.model_dump(mode="json", by_alias=True))
