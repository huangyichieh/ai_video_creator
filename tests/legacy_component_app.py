"""Regression harness for retained standalone-story components.

The production app now uses the World/episode shell. These older integration
scenarios exercise the reused editors, media controls and approval dialogs in
isolation. The actual app shell is covered by test_studio_ui and
 test_scene_production; this harness is never imported by application code.
"""
from story_channel.ui import *
from story_channel.costs import CostTracker
from legacy_pages import new_project, story_workflow, outline_editor, assets_page, story_settings

PAGES = ("Story & Settings", "Assets", "Animations", "Final", "Analytics")

def _main():
    st.session_state.pop("external-project-busy", None)
    preserve_page_widgets()
    collect()
    st.set_page_config(page_title="Story Studio", page_icon="📖", layout="wide")
    st.title("Story Studio")
    from story_channel.api_keys_ui import api_key_button
    api_key_button()
    st.caption("From a story outline to a reviewed production plan")
    if "browser-save-message" in st.session_state:
        st.success(st.session_state.pop("browser-save-message"))
    try:
        settings = Settings.load()
    except (OSError, ValueError) as exc:
        st.error(str(exc))
        return
    with st.sidebar:
        st.header("Workspace")
        root = st.text_input("Project storage", value=str(settings.data_root))
        settings.data_root = Path(root)
        manager = ProjectManager(settings)
        projects = manager.list_projects()
        if st.button("＋ New project", use_container_width=True):
            st.session_state["creating_project"] = True
        if projects:
            ids = [p.project_id for p in projects]
            titles = {p.project_id: p.title for p in projects}
            selected = st.session_state.get("selected_project", ids[0])
            selected = selected if selected in ids else ids[0]
            if st.session_state.get("project-selector") != selected:
                st.session_state["project-selector"] = selected

            def select_project():
                st.session_state["selected_project"] = st.session_state["project-selector"]
                st.session_state["creating_project"] = False
                st.session_state.pop("active-stage-browser", None)
                st.session_state.pop("modify-target", None)
                st.session_state.pop("voice-design-target", None)

            project_id = st.selectbox("Project", ids, key="project-selector", on_change=select_project,
                                      format_func=lambda key: f"{titles[key]} · {key[:8]}")
            st.session_state.setdefault("selected_project", project_id)
            if st.button("Open selected project", use_container_width=True):
                st.session_state["creating_project"] = False
            project = next(item for item in projects if item.project_id == project_id)
            previous = project.provider_config.get("llm")
            default_demo = previous.provider == "demo" if previous else settings.llm_provider == "demo"
        else:
            project_id, default_demo = None, settings.llm_provider == "demo"
        settings_key = project_id or "new"
        mode_key = f"project-setting-{settings_key}-generation-mode"
        model_key = f"project-setting-{settings_key}-llm-model"
        st.session_state.setdefault(mode_key, "Demo (offline)" if default_demo else "OpenAI")
        st.session_state.setdefault(model_key, previous.model if project_id and previous and previous.provider != "demo" else settings.llm_model)
        demo = st.session_state[mode_key].startswith("Demo")
        settings.llm_model = st.session_state[model_key]
        st.markdown("### Workspace pages")
        st.markdown("""<style>
        .st-key-workspace-navigation button {
            min-height: 2.8rem; border-radius: 0.6rem; justify-content: flex-start;
            padding: 0.4rem 0.8rem; border-width: 2px;
        }
        .st-key-workspace-navigation button p {font-size: 1.1rem; font-weight: 600;}
        .st-key-workspace-navigation button[kind="primary"] {
            background-color: #1d4ed8; border-color: #60a5fa; color: white;
            box-shadow: 0 0 0 1px #60a5fa;
        }
        </style>""", unsafe_allow_html=True)
        page = st.session_state.get("workspace_page", "Story & Settings")
        if page == "Outline" and project_id:
            st.session_state[f"page-tab-story-{project_id}"] = "Story outline"
        if page not in PAGES:
            page = "Story & Settings"
        st.session_state["workspace_page"] = page

        def choose_page(name):
            st.session_state["workspace_page"] = name

        with st.container(key="workspace-navigation"):
            for name in PAGES:
                st.button(name, key="nav-" + ("story-settings" if name == "Story & Settings" else name.lower()), use_container_width=True,
                          type="primary" if name == page else "secondary", on_click=choose_page, args=(name,))
    if st.session_state.get("creating_project") or not project_id:
        new_project(manager)
        return
    llm_config = ProviderConfig(provider="demo" if demo else "openai", model="demo-v1" if demo else settings.llm_model,
                                options={"max_output_tokens": settings.llm_max_output_tokens})
    pipeline = Pipeline(manager, project_id, llm_config=llm_config)
    task_status(pipeline.path)
    message = st.session_state.pop("workspace-task-message", None)
    if message:
        getattr(st, message[0])(message[1])
        if message[0] == "error" and st.session_state.get("outline-dialog-project") == project_id:
            st.session_state[f"outline-dialog-error-{project_id}"] = message[1]
        voice_target = st.session_state.get("voice-design-target")
        if message[0] == "error" and voice_target and voice_target["project_id"] == project_id:
            voice_subject = "_narrator" if voice_target.get("narrator") else voice_target["character_id"]
            st.session_state[f"voice-design-error-{project_id}-{voice_subject}"] = message[1]
    from story_channel.locking import ProjectBusyError
    from story_channel.ui_tasks import external_project_status
    try:
        project = manager.load(project_id) if busy() else pipeline.inspect()
    except ProjectBusyError:
        project = manager.load(project_id)
        st.session_state["external-project-busy"] = True
        external_project_status(pipeline.path)
    except (OSError, ValueError, RuntimeError) as exc:
        st.error(str(exc))
        return
    if demo:
        st.info("Demo mode uses fixed sample content to demonstrate the workflow. It does not generate a custom story or call an API.")
    st.subheader(project.title)
    from story_channel.billing_ui import billing_panel
    billing_panel(project, detailed=page == "Analytics", manager=manager)
    if page == "Story & Settings":
        story_settings(manager, project_id, pipeline, project, story_workflow, outline_editor)
    elif page == "Assets":
        assets_page(manager, project_id)
    elif page == "Animations":
        from story_channel.animations_ui import animations_page
        animations_page(manager, project_id)
    elif page == "Final":
        from story_channel.final_ui import final_page
        final_page(manager, project_id)
    elif page == "Analytics":
        from story_channel.workspace_ui import analytics_overview
        analytics_overview(manager, project_id)
        from story_channel.billing_allocations import assigned_billing
        _, billing_assignments = assigned_billing(manager, project_id)
        if billing_assignments:
            with st.expander("Assigned billed costs"):
                st.caption("Account billing snapshots assigned to this Story Studio project by explicit instruction.")
                st.json([record.model_dump(mode="json", by_alias=True) for record in billing_assignments])
        from story_channel.request_tracking import request_rows
        requests = request_rows(pipeline.path / "requests.jsonl")
        if requests:
            with st.expander("API request IDs and story attribution"):
                st.caption("Client request IDs are sent with future requests. Historical entries are assigned by your instruction; unavailable server IDs are left empty. Request IDs do not expose per-request billed costs.")
                st.dataframe(requests, hide_index=True, width="stretch")
        records = CostTracker(manager, project_id).records()
        if records:
            from story_channel.cost_estimates import estimate_usage
            st.caption("Estimated USD uses recorded usage and rough rates. Unknown means no usable usage was recorded. Job polls are deduplicated in the project total.")
            rows = [dict(r.model_dump(mode="json", by_alias=True),
                         estimatedCost=f"{r.estimated_cost:.2f}" if r.pricing_known else "Unknown",
                         cost=f"{r.cost:.2f}" if r.pricing_known else "Unknown",
                         roughEstimateUSD=f"{estimate_usage(r, manager.settings)[0]:.2f}" if estimate_usage(r, manager.settings)[0] is not None else "Unknown",
                         estimateBasis=estimate_usage(r, manager.settings)[1]) for r in records]
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("No generation usage recorded yet. Publishing performance is planned for a future milestone.")
    show_modification(manager, project_id)
    from story_channel.voice_design_ui import show_voice_design
    show_voice_design(manager, project_id)


def main():
    try:
        _main()
    except PermissionError:
        st.error("Project files cannot be accessed right now. Wait for the current save to finish, then retry. If this continues, check file access permissions or whether another application is holding the files open.")
        if st.button("Retry loading project", key="retry-project-file-access"):
            st.rerun()


main()
