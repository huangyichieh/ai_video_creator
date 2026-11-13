"""Project style controls and the shared visual consistency report."""
from .image_preview_ui import image_preview
import streamlit as st
from datetime import datetime, timedelta, timezone

from .style_rules import StyleRules
from .style_check import candidates, check_style, load_report, signature, pending_candidates, REBUILD_MESSAGE
from .ui_tasks import busy, collect, launch
from .error_report_ui import render_error
from .files import input_hash


@st.cache_data(show_spinner=False, max_entries=32)
def _candidate_snapshot(_engine, _state, key):
    return candidates(_engine, _state)


def visible_candidates(engine, state):
    # Poll file metadata cheaply; rehash image contents only when inputs change.
    def stat(path):
        try:
            value = path.stat()
            return value.st_mtime_ns, value.st_size
        except OSError:
            return None
    files = []
    for key, record in state.records.items():
        if record.stage in ("character_references", "backgrounds") and record.asset:
            try:
                changed = stat(engine.local_path(record.asset.file_path))
            except ValueError:
                changed = None
            files.append((key, record.asset.asset_id, record.file_hash, record.asset.file_path, changed))
    source = {name: stat(engine.path / name) for name in ("characters.json", "world.json", "background_library.json")}
    key = input_hash({"path": str(engine.path), "files": files, "source": source, "modes": state.background_modes,
                      "referenceSets":state.location_reference_sets})
    return _candidate_snapshot(engine, state, key)


def style_rules_box(engine):
    state = engine.load()
    key = "project-style-rules-" + engine.project_id
    with st.container(border=True, key=key + "-box"):
        st.subheader("Style rules")
        st.session_state.setdefault(key, state.project_style_rules)
        rules = st.text_area("Shared style rules", key=key, height=140,
            placeholder="2D anime, thin clean outlines, soft cel shading, muted blue-grey palette, subtle paper texture.\nSFX: restrained, natural sound effects.",
            help="Shared across asset generation. Describe medium, linework, shading, palette, texture and proportions. Prefix optional audio-specific rules with Audio:, Voice: or SFX:.")
        if st.button("Save style rules", key=key + "-save", disabled=busy() or rules.strip() == state.project_style_rules):
            try:
                StyleRules(engine).save(rules)
                st.rerun()
            except (ValueError, OSError, RuntimeError) as exc:
                render_error(str(exc), project_path=engine.path, key=key + "-error")
        image_key = key + "-image-" + (state.project_style_image or {}).get("hash", "empty")[:12]
        image_error = key + "-image-error"
        def upload_style():
            upload = st.session_state.get(image_key)
            if upload is not None:
                try:
                    StyleRules(engine).set_image(upload.getvalue())
                    st.session_state.pop(image_error, None)
                except (ValueError, OSError, RuntimeError) as exc:
                    st.session_state[image_error] = str(exc)
        st.file_uploader("Shared style reference image (optional)", type=["png", "jpg", "jpeg", "webp"],
                         key=image_key, disabled=busy(), on_change=upload_style,
                         help="PNG, JPEG or WebP, up to 20 MB. Guides medium, linework, shading, palette and texture only; it does not define character identity or background layout. Saved immediately.")
        # Upload callbacks can change the saved image before this render.
        state = engine.load()
        if state.project_style_image:
            try:
                image_preview(str(StyleRules(engine).image(state)), width=256, caption="Shared style guide · applies in text and image modes")
            except (ValueError, OSError) as exc:
                st.warning(str(exc))
            def remove_style():
                try:
                    StyleRules(engine).set_image(None)
                    for saved_key in list(st.session_state):
                        if saved_key.startswith(key + "-image-"):
                            st.session_state.pop(saved_key, None)
                except (ValueError, OSError, RuntimeError) as exc:
                    st.session_state[image_error] = str(exc)
            st.button("Remove style reference", key=key + "-remove-image", disabled=busy(), on_click=remove_style)
        if image_error in st.session_state:
            render_error(st.session_state[image_error], project_path=engine.path, key=image_error + "-report")
        st.caption("Applies to new generations across the project. Existing images are retained; changed rules mark earlier style reviews out of date, but another review is optional. Use audio-specific rules for sounds and voice design; visual instructions are not spoken.")


@st.fragment(run_every=2)
def style_check_controls(engine, tab):
    task = st.session_state.get("workspace-task")
    if task and task.get("style_check_project") == engine.project_id and task["future"].done():
        collect()
        message = st.session_state.pop("workspace-task-message", None)
        if message and message[0] == "error":
            st.session_state["style-check-error-" + engine.project_id] = message[1]
    state = engine.load()
    entries = visible_candidates(engine, state)
    report = load_report(engine)
    pending = pending_candidates(engine,state,entries,report)
    st.caption("Optional: review new or updated assets only. SFX, props and style checks never block completion of the required Assets flow.")
    st.caption(f"{len(pending)} assets awaiting style review; unchanged checked assets are comparison context only.")
    from .shared_room import enabled
    if any(enabled(spec) for spec in state.location_reference_sets.values()):
        st.caption("Style Check includes enhanced canonical backgrounds; raw 3D blockouts remain geometry guides and are excluded.")
    key = "style-check-" + engine.project_id + "-" + tab
    task = st.session_state.get("workspace-task")
    running = bool(task and task.get("style_check_project") == engine.project_id and not task["future"].done())
    def start():
        if busy():
            return
        st.session_state.pop("style-check-error-" + engine.project_id, None)
        launch(lambda: check_style(engine), "Checking character and background styles", rerun_on_start=False,
               progress_path=engine.state_path, inline=True)
        st.session_state["workspace-task"]["style_check_project"] = engine.project_id
    from .generation_preferences import openai_config
    model = openai_config(engine.creative, "style_check", force=True).model
    assessable = len(entries) >= 2 or bool(state.project_style_rules.strip() or state.project_style_image)
    st.button("Checking…" if running else "Style check", key=key, disabled=busy() or not pending or not assessable,
              on_click=start, help=f"Review only new or updated assets with OpenAI / {model}; existing assets provide style context. Optional; API usage is recorded. Does not change approvals.")
    error = st.session_state.get("style-check-error-" + engine.project_id)
    if error:
        render_error(error, project_path=engine.path, task_key="style_check", key=key + "-error")
    if report is None:
        st.caption("Previously unchecked assets are reviewed together on the first check. Add two visual assets, or one asset with style rules/a reference image.")
        return
    if report.get("demo"):
        st.info(report["summary"])
        return
    current_keys = {entry["key"] for entry in entries if entry not in pending}
    issues = [issue for issue in report["issues"] if issue["asset_key"] in current_keys]
    checked_at = datetime.fromisoformat(report["checkedAt"]).astimezone(timezone(timedelta(hours=8)))
    st.caption(f"Checked {len(report['assets'])} generated assets · {checked_at:%Y-%m-%d %H:%M:%S} (Taipei)")
    if len(issues) > 5:
        st.warning(REBUILD_MESSAGE)
    elif issues:
        st.warning(f"{len(issues)} assets may look inconsistent.")
        names = {asset["key"]: asset for asset in report["assets"]}
        for issue in issues:
            asset = names[issue["asset_key"]]
            st.markdown("**" + asset["kind"] + " · " + asset["name"] + "**")
            st.write(issue["reason"])
            st.caption("Suggested change: " + issue["recommendation"])
    else:
        if current_keys:
            st.success("No clear style inconsistencies found among reviewed assets.")
    st.caption("AI visual review is advisory. Missing images are not assessed; inspect final character/background combinations yourself.")
