"""Editable original/proposal comparisons with an explicit decision per field."""
import streamlit as st

from .files import input_hash
from .review_changes import (approve_story_unchanged, approve_storyboard_unchanged, current_session, issues_for,
                             prepare_review, review_inputs, save_review, storyboard_approval_snapshot)
from .ui_tasks import busy, collect, launch


def launch_review(pipeline, kind, callback):
    owner = st.session_state.get("active-stage-browser")
    error_key = f"ai-review-error-{pipeline.project_id}-{kind}"
    st.session_state.pop(error_key, None)

    def failed(exc):
        st.session_state[error_key] = str(exc)
        return True

    launch(callback, f"Running AI Review ({kind.title()})", inline=True, rerun_on_start=False,
           dialog_owner=("active-stage-browser", owner) if owner else None, failure=failed)


def run_story_review(pipeline):
    pipeline.generate("review", force=True)
    return prepare_review(pipeline, "story", force=True)


def run_storyboard_review(pipeline):
    from .dialogue_review import review_dialogue
    review_dialogue(pipeline, force=True)
    return prepare_review(pipeline, "storyboard", force=True)


@st.fragment(run_every=1)
def review_task_progress():
    task = st.session_state.get("workspace-task")
    if task and task.get("dialog_owner") and task["future"].done():
        collect()
        st.rerun(scope="app")
    if task and task.get("dialog_owner") and busy():
        st.status("Preparing or saving AI review…", state="running")


def review_editor(pipeline, kind, *, ready=True):
    # Poll only the status region: idle timed reruns must not reset text that
    # the user is still typing in a suggested-change editor.
    review_task_progress()
    return review_diff_editor(pipeline, kind, ready=ready)


def unchanged_approval_button(pipeline, context=None, report=None, *, kind="story", disabled=False):
    if kind == "storyboard":
        expected = storyboard_approval_snapshot(pipeline) if not disabled else None
        if st.button("Remain unchanged and approve", key="ai-review-approve-unchanged-storyboard", disabled=busy() or disabled,
                     help="Keep the saved Script and Storyboard and decline every pending suggestion. No additional AI request."):
            launch_review(pipeline, kind, lambda: approve_storyboard_unchanged(pipeline, expected=expected))
        return
    revision = pipeline.manager.load(pipeline.project_id).steps["review"].revision
    if st.button("Remain unchanged and approve", key="ai-review-approve-unchanged-story", disabled=busy(),
                 help="Keep the saved original Story, discard all draft edits and decline every suggestion. No additional AI request."):
        launch_review(pipeline, "story", lambda: approve_story_unchanged(pipeline,
            source_hash=input_hash(context), report_hash=input_hash(report), report_revision=revision))


def unreviewed_storyboard_actions(pipeline, *, ready):
    save, unchanged = st.columns(2)
    save.button("Save and approve", key="ai-review-save-storyboard", type="primary", disabled=True,
                help="Run AI Review to prepare suggested changes, or keep the original unchanged.")
    with unchanged:
        unchanged_approval_button(pipeline, kind="storyboard", disabled=not ready)


@st.fragment
def review_diff_editor(pipeline, kind, *, ready=True):
    error = st.session_state.get(f"ai-review-error-{pipeline.project_id}-{kind}")
    if error:
        st.error(error)
    try:
        context, report = review_inputs(pipeline, kind)
        session = current_session(pipeline, kind)
        issues = issues_for(kind, report)
    except (OSError, ValueError, KeyError):
        st.info("Run this AI review to see the original content and proposed changes.")
        return False
    if not ready:
        st.warning("The review sources are outdated. Generate current sources before resolving changes.")
        return False
    if session is None and not issues and not busy():
        try:
            session = prepare_review(pipeline, kind)
        except (OSError, ValueError, RuntimeError) as exc:
            st.warning(str(exc))
            if kind == "storyboard":
                unreviewed_storyboard_actions(pipeline, ready=False)
            return False
    if session is None:
        st.info("Prepare exact suggested changes from the saved findings. Saved content stays unchanged until you save and approve.")
        if st.button("Prepare suggested changes", key=f"ai-review-prepare-{kind}", disabled=busy()):
            launch_review(pipeline, kind, lambda: prepare_review(pipeline, kind, force=True))
        save, unchanged = st.columns(2)
        save.button("Save and approve", key=f"ai-review-save-{kind}", type="primary", disabled=True)
        with unchanged:
            unchanged_approval_button(pipeline, context, report, kind=kind)
        return False
    digest = input_hash(session.model_dump(mode="json"))
    prefix = f"ai-review-{pipeline.project_id}-{kind}-{digest}"
    if session.saved_hash:
        manifest = pipeline.manager.load(pipeline.project_id)
        approved = kind in manifest.approvals
        st.success("All differences resolved and saved. Story approved." if kind == "story" else
                   "All differences resolved and saved. Storyboard approved." if approved else
                   "Dialogue decisions saved. Review the current Storyboard, then save and approve here.")
        with st.expander("Saved decisions"):
            for change, decision in zip(session.changes, session.decisions):
                st.write(" / ".join(map(str, change.path)), decision["choice"].title())
        if kind == "storyboard" and not approved:
            expected = storyboard_approval_snapshot(pipeline)
            save, unchanged = st.columns(2)
            if save.button("Save and approve", key="ai-review-save-storyboard", type="primary", disabled=busy()):
                launch_review(pipeline, kind, lambda: approve_storyboard_unchanged(pipeline, expected=expected))
            with unchanged:
                unchanged_approval_button(pipeline, kind=kind)
        return True
    with st.expander("Original data", expanded=not bool(session.changes)):
        from .approval_exports import json_markdown
        st.markdown(json_markdown(session.original, "Original Story" if kind == "story" else "Original Script"))
    choices_key = prefix + "-decisions"
    choices = st.session_state.setdefault(choices_key, {})
    for index, change in enumerate(session.changes):
        st.markdown(f"**{index + 1}. {' / '.join(map(str, change.path))}**")
        st.caption(change.reason)
        left, right = st.columns(2)
        left.text_area("Original", value=change.original, disabled=True, key=prefix + f"-original-{index}")
        draft_key = prefix + f"-draft-{index}"
        st.session_state.setdefault(draft_key, change.suggested)

        def edited(index=index):
            st.session_state[choices_key].pop(str(index), None)

        right.text_area("Suggested change — editable", key=draft_key, disabled=busy(), on_change=edited)

        def decide(choice, index=index, draft_key=draft_key):
            st.session_state[choices_key][str(index)] = {"choice": choice, "text": st.session_state[draft_key]}

        selected = choices.get(str(index), {}).get("choice")
        apply, revert = st.columns(2)
        apply.button("Applied" if selected == "apply" else "Apply change", key=prefix + f"-apply-{index}",
                     disabled=busy() or selected == "apply", on_click=decide, args=("apply",))
        revert.button("Reverted — keep original" if selected == "revert" else "Revert change", key=prefix + f"-revert-{index}",
                      disabled=busy() or selected == "revert", on_click=decide, args=("revert",))
        st.caption("Pending decision" if not selected else "Applied to draft" if selected == "apply" else "Original text will be kept")
    resolved = len(choices) == len(session.changes)
    st.caption(f"{len(choices)} / {len(session.changes)} differences resolved. Editing a suggestion requires choosing Apply or Revert again.")
    if not session.changes:
        st.info("No suggested differences. Review the original data, then save and approve.")
    save, unchanged = st.columns(2)
    approval_expected = storyboard_approval_snapshot(pipeline) if kind == "storyboard" else None
    if save.button("Save and approve", key=f"ai-review-save-{kind}", type="primary", disabled=busy() or not resolved):
        decisions = [choices[str(index)] for index in range(len(session.changes))]
        launch_review(pipeline, kind, lambda: save_review(pipeline, kind, decisions, expected=digest, approval_expected=approval_expected))
    with unchanged:
        unchanged_approval_button(pipeline, context, report, kind=kind)
    message = st.session_state.pop("workspace-task-message", None)
    if message:
        getattr(st, message[0])(message[1])
    return False
