"""Scene production is separate from final video assembly."""
import streamlit as st
import json
from threading import Event

from .animations_workflow import TABS, animation_snapshot, generate_animations
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
    dirty = bool(baseline and st.session_state.get(f"animation-plan-draft-{project_id}", baseline) != baseline)
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
    generate_next = next_button.button(("Next stage: " if approval_step else "Generate next stage: ") + action.title,
                                      key=f"animation-workflow-next-{project_id}", disabled=busy() or (dirty or not action.runnable) and not approval_step,
                                      on_click=navigate if approval_step else None)
    generate_next = generate_next and not approval_step
    generate_pipeline = pipeline_button.button("Generate animations (pipeline)", key=f"animation-workflow-run-{project_id}", disabled=busy() or dirty or not action.runnable,
                                               help="Generate missing work on Animations until a manual approval or setting is required. Never assemble or publish Final.")
    if generate_next or generate_pipeline:
        from .animation_queue import AnimationQueue
        queue = AnimationQueue(engine)
        try:
            run_id = queue.start(action, pipeline=generate_pipeline)
        except (OSError, ValueError, RuntimeError) as exc:
            queue.finish("failed", str(exc))
            st.error(str(exc))
            return
        event = Event()
        engine.stop_requested = engine.assets.stop_requested = event.is_set
        launch(lambda: generate_animations(engine, pipeline=generate_pipeline, queue_run_id=run_id), "Generating Animations: " + action.title,
               stop_event=event, progress_path=engine.state_path, asset_engine=engine.assets)
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


def animations_page(manager, project_id):
    st.caption("Animations generates individual scenes and shots. Final assembles approved scenes into a complete video.")
    st.markdown("**1. Scene images → 2. Direction & performance → 3. Voices & input review → 4. Video & effects → 5. Scene review & continuity → Final**")
    snapshot = animation_pipeline_controls(manager, project_id)
    production, render = snapshot if snapshot else (None, None)
    key = f"page-tab-animations-{project_id}"
    old_tabs = {"Scene / Shot list & Performance": TABS[1], "Scene images & Voice generation": TABS[0],
                "Video, Lip-sync & SFX / VFX": TABS[3], "Scene chaining & Continuity QA": TABS[4]}
    if st.session_state.get(f"page-tab-scene-inputs-{project_id}") in ("Voice generation", "Scene input QA"):
        old_tabs["Scene images & Voice generation"] = TABS[2]
    if st.session_state.get(key) in old_tabs:
        st.session_state[key] = old_tabs[st.session_state[key]]
    tabs = st.tabs(list(TABS),
                   key=f"page-tab-animations-{project_id}", on_change="rerun")
    with tabs[0]:
        from .animation_inputs_ui import input_stage
        input_stage(manager, project_id, images=True, state=production)
    with tabs[1]:
        st.subheader("2. Review direction and performance")
        st.caption("Review the scene images from step 1. Edit the shots and dialogue delivery, then approve animation/references and performance direction before step 3.")
        from .animation_ui import animation_direction
        animation_direction(manager, project_id)
    with tabs[2]:
        input_stage(manager, project_id, images=False, state=production)
    with tabs[3]:
        st.subheader("4. Generate video and effects")
        st.caption("Configure saved video and effect settings, then generate scenes/shots and optional effects. Review the resulting scenes in step 5.")
        from .final_ui import render_workspace
        render_workspace(manager, project_id, animations=True, show_scene_review=False, state=render)
    with tabs[4]:
        st.subheader("5. Review scenes and continuity")
        st.caption("Review each finished scene and approve continuity when enabled. Approve every scene here, then continue to Final for assembly.")
        from .final_ui import scene_review_workspace
        scene_review_workspace(manager, project_id, state=render)
        from .rendering import RenderPipeline
        from .animation_ui import animation_continuity
        engine = RenderPipeline(manager, project_id)
        if engine.load().options.continuity_animation:
            animation_continuity(engine)
        else:
            st.caption("Continuity animation is not enabled. Review and approve each generated scene above before continuing to Final.")
    from .approval_navigation_ui import show_animation_approval_highlight
    show_animation_approval_highlight(project_id)
