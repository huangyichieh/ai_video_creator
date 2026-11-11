"""Scene rendering controls and approved-media final assembly."""
from .error_report_ui import render_error, error_project, report_widget_key
from .generation_ui import generation_button
import streamlit as st
import json

from .rendering import RenderPipeline
from .render_models import MusicPlan
from .ui_tasks import busy, launch, launch_render
from .modification_ui import open_modification
from .action_labels import action_label


def render_workspace(manager, project_id, *, animations=False, show_scene_review=True, state=None, show_settings=True, show_effects=True):
    episode_composition = bool(manager.load(project_id).world_project_id)
    st.header("Scene composition" if animations and episode_composition else "Video generation" if animations else "Final")
    engine = RenderPipeline(manager, project_id)
    try:
        if state is None:
            state = engine.load() if busy() else engine.inspect()
    except (OSError, ValueError, RuntimeError) as exc:
        st.info(str(exc))
        return

    def perform(callback, label="Rendering video"):
        launch_render(engine, callback, label)

    st.caption("Compose approved visuals with voices, subtitles and effects, then review the finished scenes below." if animations and episode_composition else
               "Generate individual scenes / shots here; review and approve the finished scenes below on Final." if animations else "Assemble approved scenes into a finished publishable video. Final assembly does not call generation APIs.")
    if animations and show_settings:
        video_settings(engine, state, project_id, animations=True)
    if animations:
        if show_effects:
            from .finishing_ui import finishing_panel
            finishing_panel(engine, state)
        generate, resume = st.columns(2)
        composition_ready = not episode_composition or engine.assets.load().next_stage == "complete" and "assets" in manager.load(project_id).asset_approvals
        if generation_button("Compose scenes (voices & effects)" if episode_composition else "Generate scenes / shots", key="generate-animation-scenes", type="primary", disabled=busy() or not composition_ready, container=generate):
            perform(engine.generate_scenes, "Generating scenes")
        if generation_button("Resume / retry failed scenes", key="resume-animation-scenes", disabled=busy() or not composition_ready, container=resume):
            perform(engine.generate_scenes, "Generating scenes")
    else:
        task = st.session_state.get("workspace-task")
        assembling = bool(task and not task["future"].done() and task["label"] == "Assembling approved scenes")

        def assemble():
            engine.assembly_progress(0, "Starting final assembly")
            launch(engine.assemble_final, "Assembling approved scenes", inline=True, rerun_on_start=False)

        st.button("Assembling final video…" if assembling else "Assemble final video",
                  key="render-final-video", type="primary", disabled=busy(), on_click=assemble)
        assembly_progress_panel(engine)
    st.write("Status:", state.status.replace("_", " ").title())
    completed = sum(record.status == "succeeded" for record in state.scenes.values())
    st.progress(completed / max(1, len(state.scenes)), text=f"{completed} / {len(state.scenes)} scenes rendered")
    st.dataframe([{"Scene": record.scene_id, "Status": "Rendering…" if record.status == "running" else record.status,
                   "Revision": record.revision, "Video attempts": record.attempts,
                   "Remote progress": f"{record.remote_progress}%" if record.remote_job_id else "—", "Error": record.error or ""}
                  for record in state.scenes.values()], hide_index=True, width="stretch")
    if state.error:
        render_error(state.error, project_path=error_project(locals()), key=report_widget_key('final_ui:60', locals()))
    if animations and show_scene_review:
        scene_review_panel(engine, state)

    if not animations and state.final_file:
        final = engine.assets.local_path(state.final_file)
        if final.exists():
            if state.status not in ("review", "approved"):
                st.warning("This is a previous final version. Render current inputs before approving.")
            st.subheader("Final review")
            st.video(str(final))
            st.download_button("Download final.mp4", lambda final=final:final.read_bytes(), file_name="final.mp4",mime="video/mp4")
            for name in ("final.srt", "final.vtt", "timeline.json"):
                path = final.parent / name
                if path.exists():
                    st.download_button("Download " + name, lambda path=path:path.read_bytes(), file_name=name)
            review = engine.path / "output" / "final_review.md"
            if review.exists():
                st.markdown(review.read_text(encoding="utf-8"))
            note = st.text_input("Final review note", key=f"render-note-final-{project_id}")
            checked = st.checkbox("I reviewed visuals, voices, subtitles, music and timing", key=f"render-accept-{project_id}-{state.final_hash}")
            if st.button("Approve final video", key="approve-final-video", type="primary", disabled=busy() or not checked or state.status not in ("review", "approved")):
                launch(lambda: engine.approve(note), "Final approval")
            if state.approval:
                st.success("Final video approved. Publishing remains a separate workflow.")


@st.fragment(run_every=1)
def assembly_progress_panel(engine):
    path = engine.path / "assembly_progress.json"
    if not path.exists():
        return
    try:
        progress = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    percent = max(0, min(100, int(progress.get("percent", 0))))
    status = progress.get("status")
    task = st.session_state.get("workspace-task")
    active = bool(task and not task["future"].done() and task["label"] == "Assembling approved scenes")
    if active:
        with st.status("Assembling final video…", state="running"):
            st.progress(percent / 100, text=f"{percent}% · {progress['stage']}")
    elif status == "completed" and engine.load().status in ("review", "approved"):
        st.progress(1., text="100% · Final video ready")
    elif status == "failed":
        render_error("Final assembly failed: " + (progress.get("error") or "Please retry."), project_path=error_project(locals()), key=report_widget_key('final_ui:107', locals()))
        st.progress(percent / 100, text=f"{percent}% · Assembly stopped")



def video_settings(engine, state, project_id, *, animations, refresh_target=None):
    from .ui_panels import persistent_expander
    with persistent_expander("Video settings" if animations else "Compositing & Output settings",
        key=f"render-settings-panel-{engine.project_id}-{animations}", expanded=not bool(state.final_file),
        on_change=(lambda:st.rerun(scope=refresh_target)) if refresh_target else "rerun"):
        with st.container():
            options = state.options
            left, right = st.columns(2)

            def select(column, label, choices, value, **kwargs):
                key = f"render-setting-{project_id}-{label}"
                st.session_state.setdefault(key, value)
                return column.selectbox(label, choices, key=key, **kwargs)

            def check(column, label, value):
                key = f"render-setting-{project_id}-{label}"
                st.session_state.setdefault(key, value)
                return column.checkbox(label, key=key)

            def number(column, label, value, minimum, maximum, step):
                key = f"render-setting-{project_id}-{label}"
                st.session_state.setdefault(key, value)
                return column.number_input(label, min_value=minimum, max_value=maximum, step=step, key=key)
            provider, mode, model, seconds = options.provider, options.generation_mode, options.video_model, options.video_seconds
            delivery = options.delivery_formats
            portrait_safe = options.portrait_safe_area
            if animations:
                provider = select(left, "Video provider", ("local", "runway", "openai"), options.provider,
                                          format_func=lambda x: "Local image motion (no video API cost)" if x == "local" else "Kling O3 Pro (Runway)" if x == "runway" else "OpenAI video (legacy)")
                mode = select(right, "Video input", ("image", "text"), options.generation_mode,
                                       format_func=lambda x: "Image-to-video" if x == "image" else "Text-to-video (OpenAI)")
                model = select(left, "Video model", ("klingO3_pro", "sora-2", "sora-2-pro"), options.video_model)
                seconds = select(right, "AI clip seconds", (4, 8, 12), options.video_seconds)
            sizes = ("1280x720", "1920x1080", "720x1280", "320x180")
            selected = f"{options.width}x{options.height}"
            if selected not in sizes:
                sizes = (*sizes, selected)
            if options.delivery_formats:
                portrait = options.delivery_formats == ["portrait_9_16"]
                sizes = tuple(value for value in sizes if ((int(value.split("x")[1]) > int(value.split("x")[0])) == portrait))
                size_key = f"render-setting-{project_id}-Output resolution"
                if st.session_state.get(size_key,selected) not in sizes:
                    st.session_state[size_key] = selected
            size = select(left, "Output resolution", sizes, selected)
            fps = select(right, "Frames per second", tuple(sorted({12, 24, 30, 60, options.fps})), options.fps)
            motion = select(left, "Local camera motion", ("zoom_pan", "static"), options.motion) if animations else options.motion
            transition = select(right, "Scene transition", ("fade", "cut"), options.transition)
            subtitles = select(left, "Subtitles", ("burn", "soft", "none"), options.subtitles,
                                       format_func=lambda x: {"burn": "Visible subtitles", "soft": "Selectable subtitle track", "none": "Sidecar files only"}[x])
            font_key = f"render-setting-{project_id}-font"
            st.session_state.setdefault(font_key, options.font)
            font = right.text_input("Subtitle font", key=font_key)
            font_size = number(left, "Subtitle font size", options.font_size, 8, 100, 1)
            transition_seconds = number(right, "Fade duration (seconds)", options.transition_seconds, 0., 2., .05)
            music = check(left, "Background music", options.music)
            sfx = check(right, "SFX", options.sfx)
            duck = check(left, "Lower music while voices speak", options.duck_music)
            normalize = check(right, "Normalize voice loudness", options.normalize_voice)
            continuity_animation = check(left, "Continuity animation (approved shots and voices)", options.continuity_animation) if animations else options.continuity_animation
            music_gain = number(left, "Music volume", options.music_gain, 0., 1., .01)
            sfx_gain = number(right, "Sound effect volume", options.sfx_gain, 0., 1., .01)
            st.caption("Local mode animates approved images. OpenAI clips are fitted/looped to each scene's recorded voice duration; generated clip audio is replaced by the approved voice tracks. Fade transitions preserve scene timing.")
            if st.button("Save video settings", key="save-video-settings", disabled=busy()):
                try:
                    width, height = map(int, size.split("x"))
                    updated = options.model_dump()
                    updated.update(provider=provider, generation_mode="image" if provider == "runway" else mode, video_model="klingO3_pro" if provider == "runway" else "sora-2" if provider == "openai" and model == "klingO3_pro" else model, video_seconds=seconds,
                                   delivery_formats=delivery,portrait_safe_area=portrait_safe,
                                   video_size="720x1280" if height > width else "1280x720", width=width, height=height,
                                   fps=fps, motion=motion, transition=transition, subtitles=subtitles, font=font,
                                   music=music, sfx=sfx, duck_music=duck, normalize_voice=normalize,
                                   font_size=font_size, transition_seconds=transition_seconds, music_gain=music_gain, sfx_gain=sfx_gain,
                                   continuity_animation=continuity_animation)
                    engine.configure(options=updated)
                    st.rerun()
                except (OSError, ValueError, RuntimeError) as exc:
                    render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('final_ui:176', locals()))



def sound_plan(engine, state, project_id):
    from .ui_panels import persistent_expander
    with persistent_expander("Music and sound effects",key=f"render-audio-panel-{engine.project_id}"):
        st.caption("The default plan synthesizes a simple local music pad and event cues. Edit the plan to use your own licensed audio; every source is retained. Cue times are relative to the scene.")
        plan_key = f"render-plan-{project_id}"
        st.session_state.setdefault(plan_key, state.music_plan.model_dump_json(by_alias=True, indent=2))
        plan = st.text_area("Music / SFX plan JSON", key=plan_key, height=300, disabled=busy())
        save, revert = st.columns(2)
        if save.button("Save sound plan", disabled=busy()):
            try:
                engine.configure(music_plan=MusicPlan.model_validate_json(plan).model_dump())
                st.rerun()
            except (OSError, ValueError, RuntimeError) as exc:
                render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('final_ui:192', locals()))

        def reset_plan():
            st.session_state[plan_key] = state.music_plan.model_dump_json(by_alias=True, indent=2)

        revert.button("Revert sound plan", disabled=busy(), on_click=reset_plan)


def final_page(manager, project_id):
    tabs = st.tabs(["Timeline & Compositing", "Subtitle", "Audio mixing & Visual finishing", "Outputs & Publish approval"],
                   key=f"page-tab-final-{project_id}", on_change="rerun")
    with tabs[0]:
        engine = RenderPipeline(manager, project_id)
        state = engine.load()
        st.subheader("Timeline & Compositing")
        st.caption("Scene order and durations follow the approved storyboard and voice tracks. Compose and review the finished scenes here before assembly.")
        render_workspace(manager,project_id,animations=True,show_settings=False,show_effects=False)
        state=engine.load()
        if state.options.continuity_animation and not manager.load(project_id).world_project_id:
            from .animation_ui import animation_continuity
            animation_continuity(engine)
        rows = []
        cursor = 0.
        for record in state.scenes.values():
            rows.append({"Scene": record.scene_id, "Start": cursor, "End": cursor + record.duration,
                         "Approved": bool(record.approved_hash and record.approved_hash == record.file_hash and record.status == "succeeded")})
            cursor += record.duration
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        st.caption("Timeline reordering and separate color-grading controls are not implemented yet.")
    with tabs[1]:
        from .assets import AssetPipeline
        from .subtitles_ui import show_subtitle_progress, show_subtitle_scenes
        subtitle_engine = AssetPipeline(manager, project_id)
        subtitle_state = subtitle_engine.load()
        show_subtitle_progress(subtitle_engine, subtitle_state)
        show_subtitle_scenes(subtitle_engine, subtitle_state)
    with tabs[2]:
        st.caption("Changes to mix levels, subtitles or transitions require recomposing and approving affected scene previews on Timeline & Compositing. Cached source clips are reused.")
        video_settings(engine, state, project_id, animations=False)
        sound_plan(engine, state, project_id)
        from .finishing_ui import finishing_panel
        finishing_panel(engine,state,section="video")
    with tabs[3]:
        render_workspace(manager, project_id)
        st.markdown("**Final render - Long-form output**")
        st.info("Thumbnail generation, metadata editing, Shorts exports and publishing integration are not implemented yet. Final video approval is a local review gate; it does not publish the video.")


def scene_review_workspace(manager, project_id, *, state=None):
    engine = RenderPipeline(manager, project_id)
    try:
        if state is None:
            state = engine.load() if busy() else engine.inspect()
    except (OSError, ValueError, RuntimeError) as exc:
        st.info(str(exc))
        return
    if not state.scenes:
        st.info("Generate animated scenes, approve Voices & input, then compose scenes here for review.")
    scene_review_panel(engine, state)


def scene_review_panel(engine, state):
    for scene_id in state.scenes:
        scene_review_card(engine, scene_id)


@st.fragment
def scene_review_card(engine, scene_id):
    # Checkbox/expander interactions update this card only, without rerunning
    # the workspace or inspecting every asset/video. Approval validates on disk.
    state = engine.load()
    record = state.scenes.get(scene_id)
    if record is None:
        return
    project_id = engine.project_id

    def perform(callback, label):
        launch_render(engine, callback, label)

    from .ui_panels import persistent_expander
    panel = persistent_expander(f"{record.scene_id} · {record.status} · revision {record.revision}",
                     key=f"animation-scene-panel-{project_id}-{record.scene_id}")
    if not panel.open:
        return
    with panel:
        if record.error:
            render_error(record.error, project_path=engine.path, task_key=f"render:video:{record.scene_id}", key=f"render-error-{project_id}-{record.scene_id}")
        if record.file:
            path = engine.assets.local_path(record.file)
            if path.exists():
                st.video(str(path))
        retry, regenerate = st.columns(2)
        if generation_button("Generate / retry scene", key=f"retry-render-{record.scene_id}", disabled=busy(), container=retry):
            perform(lambda sid=record.scene_id: engine.generate_scenes(subject=sid), f"Rendering {record.scene_id}")
        if generation_button("Regenerate scene", key=f"regenerate-render-{record.scene_id}", disabled=busy(), container=regenerate):
            perform(lambda sid=record.scene_id: engine.generate_scenes(subject=sid, force=True), f"Regenerating {record.scene_id}")
        if record.clip and record.clip.provider == "openai" and state.options.provider == "openai" and not state.options.continuity_animation:
            st.button(action_label("Modify", record.clip.provider, record.clip.model), key=f"modify-render-{record.scene_id}", disabled=busy(), on_click=open_modification, args=(project_id, "video", "video",), kwargs={'subject': record.scene_id, 'expected': record.clip.asset_id, 'rerun': False})
        approved = bool(record.approved_hash and record.approved_hash == record.file_hash and record.status == "succeeded" and engine._scene_intact(record))
        st.caption("Approved for Final" if approved else "Review and approve this scene before Final assembly.")
        with st.container(key=f"animation-scene-approval-{project_id}-{record.scene_id}"):
            reviewed = st.checkbox("I reviewed this scene", key=f"render-accept-scene-{project_id}-{record.scene_id}-{record.file_hash}")
            task = st.session_state.get("workspace-task")
            saving = bool(task and not task["future"].done() and task["label"] == f"Approving scene {record.scene_id}")

            def approve():
                launch(lambda: engine.approve_scenes([record.scene_id]), f"Approving scene {record.scene_id}",
                       inline=True, rerun_on_start=False)

            st.button("Approved" if approved else "Approving\u2026" if saving else "Approve scene",
                      key=f"approve-scene-{record.scene_id}",
                      disabled=busy() or approved or not reviewed or record.status != "succeeded",
                      on_click=approve)
        st.json(record.model_dump(mode="json", by_alias=True))

