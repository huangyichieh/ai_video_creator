"""Open the modal shell before resolving data or doing provider work."""
from functools import wraps
import streamlit as st


class Deferred:
    def __init__(self, callback):
        self.callback = callback


def request_open():
    st.session_state["dialog-open-requested"] = True


def dialog(title, **options):
    def decorate(function):
        @st.dialog(title, **options)
        @wraps(function)
        def content(*args, **kwargs):
            pending = any(isinstance(value, Deferred) for value in (*args, *kwargs.values()))
            if pending:
                loading = st.empty()
                try:
                    with loading.container(), st.spinner("Loading…"):
                        args = tuple(value.callback() if isinstance(value, Deferred) else value for value in args)
                        kwargs = {key: value.callback() if isinstance(value, Deferred) else value for key, value in kwargs.items()}
                finally:
                    loading.empty()
            return function(*args, **kwargs)

        @wraps(function)
        def show(*args, **kwargs):
            from streamlit.runtime.scriptrunner import get_script_run_ctx
            context = get_script_run_ctx()
            if context and context.has_dialog_opened:
                return
            return content(*args, **kwargs)
        return show
    return decorate


def show_pending_dialog():
    """Dispatch before page inspection, asset tables, billing or other slow work."""
    st.session_state.pop("dialog-open-requested", None)
    manager = st.session_state.get("dialog-context-manager")
    target = st.session_state.get("error-report-target")
    if target:
        from .error_report_ui import report_dialog
        report_dialog(target)
    elif st.session_state.get("api-key-dialog-open"):
        from .api_keys_ui import api_key_dialog
        api_key_dialog()
    elif manager is not None:
        if target := st.session_state.get("scene-keyframes-target"):
            from .scene_keyframes_ui import show
            show(manager,target)
        elif target := st.session_state.get("scene-video-extend-target"):
            from .scene_video_extension_ui import show
            show(manager,target)
        elif target := st.session_state.get("scene-video-modify-target"):
            from .scene_video_modify_ui import show
            show(manager,target)
        elif target := st.session_state.get("delete-project-target"):
            from .project_deletion_ui import delete_dialog
            delete_dialog(manager, target)
        elif target := st.session_state.get("remove-episode-target"):
            from .episode_removal_ui import removal_dialog
            removal_dialog(manager,target)
        elif target := st.session_state.get("remove-location-detail-target"):
            from .location_detail_removal_ui import show
            show(manager,target)
        elif target := st.session_state.get("detail-zoom-target"):
            from .detail_zoom_ui import show
            show(manager,target)
        elif target := st.session_state.get("floor-plan-preview-target"):
            from .floor_plan_preview_ui import preview_dialog
            preview_dialog(manager,target)
        elif target := st.session_state.get("floor-plan-revisions-target"):
            from .floor_plan_revisions_ui import show
            show(manager,target)
        elif target := st.session_state.get("prop-revisions-target"):
            from .prop_revisions_ui import show
            show(manager,target)
        elif target := st.session_state.get("remove-prop-target"):
            from .prop_removal_ui import show
            show(manager,target)
        elif target := st.session_state.get("storyboard-prop-removal-target"):
            from .storyboard_prop_removal_ui import show
            show(manager,target)
        elif target := st.session_state.get("storyboard-split-target"):
            from .storyboard_split_ui import show
            show(manager,target)
        elif target := st.session_state.get("storyboard-scene-removal-target"):
            from .storyboard_scene_removal_ui import show
            show(manager,target)
        elif target := st.session_state.get("storyboard-scene-insertion-target"):
            from .storyboard_scene_insertion_ui import show
            show(manager,target)
        elif target := st.session_state.get("layout-modify-target"):
            from .location_layout_modify_ui import show
            show(manager,target)
        elif st.session_state.get("generation-preferences-open"):
            from .generation_preferences_ui import preferences_dialog
            preferences_dialog(manager, None if st.session_state.get("creating_project") else st.session_state.get("selected_project"))
        elif target := st.session_state.get("background-enhance-target"):
            from .background_enhancement_ui import show
            show(manager, target)
        elif target := st.session_state.get("location-pair-revisions-target"):
            from .location_pairs_ui import show
            show(manager,target)
        elif target := st.session_state.get("background-revisions-target"):
            from .background_revisions_ui import show_revisions
            show_revisions(manager, target)
        elif target := st.session_state.get("modify-target"):
            from .modification_ui import modification_dialog
            modification_dialog(manager, target)
        elif target := st.session_state.get("studio-modify-target"):
            from .studio_ui import modify_dialog
            modify_dialog(manager, target)
        elif target := st.session_state.get("voice-design-target"):
            from .voice_design_ui import voice_design_dialog
            voice_design_dialog(manager, target["project_id"], target["character_id"], narrator=target.get("narrator", False))
        elif target := st.session_state.get("remove-character-target"):
            from .character_removal_ui import removal_dialog
            removal_dialog(manager, target)
        elif target := st.session_state.get("active-stage-browser"):
            from .pipeline import Pipeline
            from .ui import ai_story_review_dialog, storyboard_approval_dialog, browse_stage
            pipeline = Deferred(lambda: Pipeline(manager, target[0]))
            if target[1] in ("review", "story_approval"):
                ai_story_review_dialog(pipeline)
            elif target[1] == "storyboard_approval":
                storyboard_approval_dialog(pipeline)
            else:
                browse_stage(pipeline, target[1])
        else:
            for key in list(st.session_state):
                if key.startswith("background-removal-target-") and st.session_state[key]:
                    from .assets import AssetPipeline
                    from .background_assets_ui import confirm_remove
                    pid, lid = key.removeprefix("background-removal-target-"), st.session_state[key]
                    confirm_remove(Deferred(lambda: AssetPipeline(manager, pid)), lid)
                    break
