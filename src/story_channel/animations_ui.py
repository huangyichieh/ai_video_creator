"""Scene production is separate from final video assembly."""
from .error_report_ui import render_error, error_project, report_widget_key
from .generation_ui import generation_button
import streamlit as st
import json
from threading import Event

from .animations_workflow import TABS, STAGE_TABS, animation_snapshot, generate_animations
from .animation import AnimationDirector
from .rendering import RenderPipeline
from .ui_tasks import busy, launch


def animation_pipeline_controls(manager, project_id):
    engine = RenderPipeline(manager, project_id)
    try:
        action, rows = animation_snapshot(engine, refresh=not busy())
    except (OSError, ValueError, RuntimeError) as exc:
        st.info(str(exc))
        return
    destination = action.tab if action.page == "Animations" else action.page
    st.markdown("Next step:  \n**" + destination + " — " + action.title + "**")
    st.caption(action.detail)
    direction = AnimationDirector(manager, project_id).load()
    baseline = direction.plan.model_dump_json(by_alias=True, indent=2) if direction else None
    from .animation_ui import sync_plan_draft
    dirty = bool(baseline and sync_plan_draft(project_id,baseline) != baseline)
    if dirty:
        st.caption("Save or revert the animation direction draft before generating.")

    def navigate():
        if action.page != "Animations":
            st.session_state["workspace_page"] = action.page
        else:
            st.session_state[f"page-tab-animations-{project_id}"] = action.tab
            from .approval_navigation_ui import open_animation_approval
            open_animation_approval(project_id, action.stage, engine.load())

    # Navigation has its own key, so its click cannot trigger a generation
    # button when the target workspace renders during the same app run.
    if action.stage == "scene_review":
        def approve_all():
            subjects = list(engine.load().scenes)
            launch(lambda: engine.approve_scenes(subjects), "Approving all scenes",
                   inline=True, rerun_on_start=False)

        task = st.session_state.get("workspace-task")
        saving = bool(task and not task["future"].done() and task["label"] == "Approving all scenes")
        st.button("Approving all scenes…" if saving else "Approve all scenes",
                  key=f"animation-workflow-review-{project_id}", on_click=approve_all, disabled=busy(),
                  help="Approve every current generated scene together. Updated scenes require approval again.")
    elif not action.runnable:
        st.button(action.title, key=f"animation-workflow-review-{project_id}", on_click=navigate, disabled=busy())
    next_button, pipeline_button = st.columns(2)
    approval_step = action.stage.endswith("_review") and action.page == "Animations"
    generate_next = generation_button(("Next stage: " if approval_step else "Generate next stage: ") + action.title,
                                      key=f"animation-workflow-next-{project_id}", disabled=busy() or (dirty or not action.runnable) and not approval_step,
                                      on_click=navigate if approval_step else None, container=next_button)
    generate_next = generate_next and not approval_step
    generate_pipeline = generation_button("Generate animations (pipeline)", key=f"animation-workflow-run-{project_id}", disabled=busy() or dirty or not action.runnable,
                                               help="Generate missing work on Animations until a manual approval or setting is required. Never assemble or publish Final.", container=pipeline_button)
    if generate_next or generate_pipeline:
        from .animation_queue import AnimationQueue
        queue = AnimationQueue(engine)
        try:
            run_id = queue.start(action, pipeline=generate_pipeline)
        except (OSError, ValueError, RuntimeError) as exc:
            queue.finish("failed", str(exc))
            render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('animations_ui:67', locals()))
            return
        event = Event()
        engine.stop_requested = engine.assets.stop_requested = event.is_set
        launch(lambda: generate_animations(engine, pipeline=generate_pipeline, queue_run_id=run_id), "Generating Animations: " + action.title,
               stop_event=event, progress_path=engine.state_path, asset_engine=engine.assets,
               queue_enabled=True,rerun_on_start=False)
    task = st.session_state.get("workspace-task")
    if task and busy() and task["label"].startswith("Generating Animations:"):
        title = action.title
        try:
            progress = json.loads((engine.path / "animation_workflow_progress.json").read_text(encoding="utf-8"))
            if progress.get("status") == "running":
                title = progress.get("title", title)
        except (OSError, ValueError):
            pass
        st.info(f"Generating {title}… Completed work and costs are saved. Use Stop after running tasks to stop safely.")
    from .animation_queue_ui import waiting_table
    waiting_table(engine)
    st.markdown("**All animation stages**")
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("Includes generation, configured effects, scene approvals and continuity checks. Optional/unconfigured layers are skipped. Generation never grants approval.")
    # The snapshot already validated these inputs. Reuse them across tabs instead
    # of inspecting/writing and hashing the same files several times per click.
    return engine.assets.load(), engine.load()


def animations_page(manager, project_id, *, check_assets_only=False):
    from .storyboard_asset_check import available
    if not available(manager,project_id):
        st.info("Approve this episode's Storyboard on Story/Episode to open Check assets.")
        return
    if check_assets_only:
        st.caption("Prepare the assets used by this episode's saved Storyboard. Story approvals are required before generating animations.")
        tabs = st.tabs(["Check assets"],key=f"page-tab-check-assets-only-{project_id}")
        with tabs[0]:
            from .storyboard_asset_check_ui import render as check_assets
            check_assets(manager,project_id,allow_animation=False)
        st.info("Approve this episode's Outline, Story, Script, Storyboard and Prompts on Story/Episode to unlock the remaining animation stages.")
        return
    from .animation_formats_ui import settings
    settings(RenderPipeline(manager,project_id))
    st.caption("Animations generates individual scenes and shots. Final assembles approved scenes into a complete video.")
    st.markdown("**Check assets → Scene images → Direction & performance → Animated scenes → Voices & input → VFX → SFX → Scene review & continuity → Final**")
    snapshot = animation_pipeline_controls(manager, project_id)
    production, render = snapshot if snapshot else (None, None)
    key = f"page-tab-animations-{project_id}"
    old_tabs = {"Video effects": STAGE_TABS[4], "Sound effects": STAGE_TABS[5],
                "Scene / Shot list & Performance": STAGE_TABS[1], "Scene images & Voice generation": STAGE_TABS[0],
                "Video, Lip-sync & SFX / VFX": STAGE_TABS[2], "Scene chaining & Continuity QA": STAGE_TABS[6],
                "1. Scene images": STAGE_TABS[0], "2. Direction & performance": STAGE_TABS[1], "3. Voices & input review": STAGE_TABS[3],
                "4. Video & effects": STAGE_TABS[2], "5. Scene review & continuity": STAGE_TABS[6]}
    if st.session_state.get(f"page-tab-scene-inputs-{project_id}") in ("Voice generation", "Scene input QA"):
        old_tabs["Scene images & Voice generation"] = STAGE_TABS[3]
    if st.session_state.get(key) in old_tabs:
        st.session_state[key] = old_tabs[st.session_state[key]]
    tabs = st.tabs(list(TABS),
                   key=f"page-tab-animations-{project_id}", on_change="rerun")
    from .storyboard_asset_check import check
    required = check(manager,project_id)
    with tabs[0]:
        from .storyboard_asset_check_ui import render as check_assets
        required = check_assets(manager,project_id,required)
    if any(not item["Ready"] for item in required) and manager.load(project_id).world_project_id:
        st.info("Complete Check assets before continuing to the other animation stages.")
        return
    with tabs[1]:
        from .animation_inputs_ui import input_stage
        input_stage(manager, project_id, images=True, state=production)
    with tabs[2]:
        st.subheader("Direction & performance")
        st.caption("Review each scene snapshot and its editable direction suggestions, save any changes, then continue to Animated scenes without a direction approval step.")
        from .animation_ui import animation_direction
        animation_direction(manager, project_id)
    with tabs[3]:
        if manager.load(project_id).world_project_id:
            from .scene_production_ui import animated_scenes
            with st.container(key=f"animated-visual-review-{project_id}"):
                animated_scenes(manager, project_id)
        else:
            from .final_ui import render_workspace
            render_workspace(manager, project_id, animations=True, show_scene_review=False, state=render, show_effects=False)
    with tabs[4]:
        input_stage(manager, project_id, images=False, state=production)
    with tabs[5]:
        from .episode_effects_ui import render as effect_list
        effect_list(manager,project_id,"VFX")
    with tabs[6]:
        effect_list(manager,project_id,"SFX")
    with tabs[7]:
        from .scene_continuity_review_ui import render as review_scenes
        review_scenes(manager,project_id)
    from .approval_navigation_ui import show_animation_approval_highlight
    show_animation_approval_highlight(project_id)
