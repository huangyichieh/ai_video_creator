"""Reviewable editor for reusable identities; all file writes go through the service."""
import streamlit as st

from .identity import IdentityManager, IdentityLibrary
from .ui_tasks import busy, launch


def identity_library(manager, project_id, *, engine=None, state=None):
    service = IdentityManager(manager, project_id)
    library = service.load()
    key = f"identity-draft-{project_id}"
    approved = False
    if library and library.approval:
        try:
            service.require_approved()
            approved = True
        except (ValueError, OSError):
            pass
    status = (":green[Approved]" if approved else ":orange[Approval outdated]" if library and library.approval
              else ":orange[Not approved]" if library else ":gray[Not built]")
    st.markdown("**Identity library status:** " + status)
    from .assets import AssetPipeline
    from .assets_workflow import identity_checklist
    engine = engine or AssetPipeline(manager, project_id)
    st.table(identity_checklist(engine, state if state is not None else engine.load()))
    st.caption("Identity approval needs approved character/background references, registered costumes, and explicit character Voice IDs. Voice auditions are generated and approved afterward; props are optional.")
    with st.expander("Identity library: voices, locations, costumes and props",
                     key=f"identity-panel-{project_id}", on_change="rerun"):
        st.caption("Available after Characters. Build/edit the library, generate and approve character references and backgrounds, then approve the library before animation generation. Background generation also requires the completed creative pipeline.")
        st.caption("Use stable costumeId/propId across scenes. Set explicit provider Voice IDs before library approval; no voice is guessed.")
        if st.button("Build / rebuild identity library", key=f"identity-build-{project_id}", disabled=busy()):
            launch(service.prepare, "Building identity library", lambda _: st.session_state.pop(key, None))
        if library is None:
            return
        markdown = service.path / "identity_library.md"
        if markdown.exists():
            st.markdown(markdown.read_text(encoding="utf-8"))
        baseline = library.model_dump_json(by_alias=True, indent=2)
        st.session_state.setdefault(key, baseline)
        text = st.text_area("Identity library JSON", key=key, height=280, disabled=busy())
        changed = text != baseline
        if changed:
            st.caption("Modified - not saved")
        save, revert = st.columns(2)
        if save.button("Save identity library", key=f"identity-save-{project_id}", disabled=busy() or not changed):
            try:
                draft = IdentityLibrary.model_validate_json(text)
                expected = library.model_dump_json(by_alias=True)
                launch(lambda: service.save(draft, expected=expected), "Saving identity library",
                       lambda _: st.session_state.pop(key, None))
            except ValueError as exc:
                st.error(str(exc))
        revert.button("Revert all changes", key=f"identity-revert-{project_id}", disabled=busy() or not changed,
                      on_click=lambda: st.session_state.update({key: baseline}))
        st.caption("Save library changes, then rebuild Animation direction to use the new profiles and registered assets. Generated references may become stale.")
        approval_ready, reason = approved, ""
        if not approved:
            try:
                service.signature(library)
                approval_ready = True
            except (OSError, ValueError) as exc:
                reason = str(exc)
        if reason:
            st.caption("Before approval: " + reason)
        if st.button("Approved" if approved else "Approve identity library", key=f"identity-approve-{project_id}", disabled=busy() or changed or approved or not approval_ready):
            launch(service.approve, "Approving identity library", lambda _: st.session_state.pop(key, None))
        if not approved and st.session_state.get(f"identity-approval-highlight-{project_id}"):
            st.html("""<style>
                .st-key-identity-approve-__PROJECT__ {
                    outline: 2px solid var(--st-primary-color, #58a6ff);
                    outline-offset: 4px;
                    border-radius: 0.5rem;
                    scroll-margin-top: 6rem;
                }
            </style>""".replace("__PROJECT__", project_id))
        scroll_request = st.session_state.pop(f"identity-approval-scroll-{project_id}", None)
        if scroll_request:
            st.html("""<script data-scroll-request="__REQUEST__">
                (() => {
                    let attempts = 0;
                    const scrollToApproval = () => {
                        const target = document.querySelector('.st-key-identity-approve-__PROJECT__');
                        if (target && target.getClientRects().length && target.offsetHeight) {
                            target.scrollIntoView({behavior: 'smooth', block: 'center'});
                        } else if (++attempts < 60) {
                            setTimeout(scrollToApproval, 50);
                        }
                    };
                    requestAnimationFrame(scrollToApproval);
                })();
            </script>""".replace("__PROJECT__", project_id).replace("__REQUEST__", scroll_request), unsafe_allow_javascript=True)
        if approved:
            st.caption(f"Approved {library.approval.approved_at:%Y-%m-%d %H:%M %Z}. Listen to and approve voice samples before dialogue production.")
        elif library.approval:
            st.warning("Identity approval is outdated. Review current profiles and references again.")
