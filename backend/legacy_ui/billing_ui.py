"""Automatic background billing refresh independent of generation workers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
import os
import time as clock

import streamlit as st
from .error_report_ui import render_error

from .billing import admin_key, fetch_billing
from .request_tracking import tracked_call
from .modules import stage_title


def next_step_label(project):
    if project.workflow_version == "world_episode":
        if not project.world_project_id:
            if "world" not in project.content_approvals:
                return "World: Review and approve World settings"
            if not project.steps.get("characters"):
                return "Assets: Generate characters"
            if project.asset_state != "approved":
                return "Assets: " + (project.asset_step or "Generate reusable references").replace("_", " ").title()
            return "Story/Episode: Create or select an episode"
        for name in ("outline", "script", "prompts"):
            if name not in project.content_approvals and (name == "outline" or project.steps.get(name) and project.steps[name].status == "succeeded"):
                return "Story/Episode: Review and approve " + name.title()
    if project.current_step != "complete":
        name = {"story_review": "AI Review (Story)", "storyboard_review": "AI Review (Storyboard)"}.get(
            project.current_step, stage_title(project.current_step))
        return f"Story/Episode: {name}"
    if project.asset_state != "approved":
        stage = project.asset_step or "character_references"
        name = "Review assets" if stage == "complete" else stage.replace("_", " ").title()
        page = "Assets" if stage in ("character_references", "character_review", "backgrounds", "background_review", "waiting_for_creative") else "Animations"
        return f"{page}: {name}"
    name = {"not_started": "Render video", "rendering": "Rendering", "stopped": "Resume rendering",
            "failed": "Retry rendering", "stale": "Render updated video", "review": "Human final approval",
            "approved": "Complete", "continuity_review": "Scene / continuity approval"}[project.render_state]
    page = "Final" if project.render_state in ("review", "approved") else "Animations"
    return f"{page}: {name}"


def workspace_metrics(project, manager):
    next_step, duration, billed = st.columns(3)
    label = next_step_label(project)
    if manager and project.workflow_version == "world_episode" and not project.world_project_id and "world" in project.content_approvals:
        from .assets import AssetPipeline
        from .assets_workflow import next_assets_action
        try:
            engine = AssetPipeline(manager, project.project_id)
            action = next_assets_action(engine, engine.load())
            label = "Story/Episode: Create or select an episode" if action.stage == "complete" else "Assets: " + action.title
        except (OSError, ValueError):
            pass
    page, stage = label.split(": ", 1)
    next_step.metric("Next step:", stage, help=f"Workspace: {page}")
    hours, remainder = divmod(project.target_duration_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    if project.workflow_version == "world_episode" and not project.world_project_id:
        duration.metric("World", "Approved" if "world" in project.content_approvals else "Pending approval")
    else:
        duration.metric("Target length (HH:MM:SS)", f"{hours:02d}:{minutes:02d}:{seconds:02d}")
    from .cost_estimates import project_cost_estimate
    total, entries = project_cost_estimate(manager, project.project_id) if manager else (0, [])
    unknown = sum(entry["amount"] is None for entry in entries)
    billed.metric("Project billed cost (USD)", f"${total:.2f}",
                  help="Rough estimate across this project's recorded providers/models, including metered retries and failures. Uses recorded costs, configured rates, then fallback rates. Not an invoice; excludes tax/subscriptions and unmetered requests.")
    st.caption("Project cost is estimated from recorded usage across providers." + (f" {unknown} records have no usable usage/rate and are excluded." if unknown else ""))


@st.fragment(run_every=5)
def billing_panel(project, *, detailed=False, manager=None):
    workspace_metrics(project, manager)

    def account_metric(value):
        if detailed:
            st.metric("OpenAI account billed cost (USD)", value)

    key = admin_key()
    if not key:
        account_metric("Not configured")
        if detailed:
            st.caption("Actual OpenAI billed costs: save OPENAI_ADMIN_KEY in API key settings to enable automatic updates.")
        return
    today = datetime.now(timezone.utc).date()
    if detailed:
        st.subheader("OpenAI billed costs")

        def save_period():
            st.session_state["billing-period-value"] = st.session_state["billing-period-input"]

        period = st.date_input("Billing period (UTC, inclusive dates)",
                               value=st.session_state.get("billing-period-value", (today.replace(day=1), today)),
                               max_value=today, key="billing-period-input", on_change=save_period)
        if len(period) != 2:
            account_metric("Select dates")
            st.info("Select both the start and end date.")
            return
    else:
        period = st.session_state.get("billing-period-value", (today.replace(day=1), today))
        if len(period) != 2:
            period = (today.replace(day=1), today)
    start = int(datetime.combine(period[0], time.min, timezone.utc).timestamp())
    # Use full UTC buckets, including the current day's available reported costs.
    end = int(datetime.combine(period[1] + timedelta(days=1), time.min, timezone.utc).timestamp())
    organization = os.environ.get("OPENAI_ORG_ID", "").strip() or None
    signature = ("account", sha256(key.encode()).hexdigest(), organization, start, end)
    state = st.session_state.get("billing-report-state")
    if state is None or state["signature"] != signature:
        state = {"signature": signature, "checked": 0., "report": None, "error": None, "future": None}
        st.session_state["billing-report-state"] = state
    refresh = st.button("Refresh billed costs", key="refresh-billed-costs", disabled=state["future"] is not None)
    if state["future"] is not None and state["future"].done():
        try:
            state["report"] = state["future"].result()
            state["error"] = None
        except (ValueError, RuntimeError) as exc:
            state["error"] = str(exc)
        except Exception:
            state["error"] = "Billing refresh failed. Check the admin key configuration and try again."
        finally:
            state["future"] = None
            state["checked"] = clock.monotonic()
    if state["future"] is None and (refresh or not state["checked"] or clock.monotonic() - state["checked"] >= 300):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openai-billing")
        if manager is not None:
            state["future"] = executor.submit(tracked_call, manager, project.project_id, "billing:costs", fetch_billing,
                                              start, end, key=key, organization=organization)
        else:
            state["future"] = executor.submit(fetch_billing, start, end, key=key, organization=organization)
        executor.shutdown(wait=False)
    report = state["report"]
    if not detailed:
        return
    if state["future"] is not None:
        st.caption("Updating billed costs in the background…")
    account_metric(f"${report.total:.2f}" if report else "Fetching…" if state["future"] else "Unavailable")
    scope = "Entire OpenAI organization (all Story Studio projects and other apps)"
    st.caption(f"{scope} · {period[0]} through {period[1]} UTC · Auto-refresh every 5 minutes while the app is open.")
    st.caption("OpenAI-reported costs may arrive later than generation results. This is an account-wide total, not an individual story's cost. Story attribution is available in Analytics under API request IDs and story attribution.")
    if state["error"]:
        render_error(state["error"] + (" Showing the last successful report." if report else ""),
                     project_path=manager.project_path(project.project_id) if manager else None,
                     task_key="billing:costs", key=f"billing-error-report-{project.project_id}")
    if report:
        st.caption("Last successful update: " + report.fetched_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
        if detailed:
            if report.rows:
                st.dataframe([{"Date (UTC)": row["date"], "OpenAI project": row["project_id"],
                               "Line item": row["line_item"], "Billed USD": f"{row['amount']:.2f}"}
                              for row in report.rows], hide_index=True, width="stretch")
            else:
                st.info("OpenAI returned no cost items for this scope and period yet.")
