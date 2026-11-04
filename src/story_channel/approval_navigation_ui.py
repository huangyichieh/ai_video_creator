"""Temporary approval guidance; browser-only highlighting never grants approval."""
import json
from uuid import uuid4

import streamlit as st


def open_animation_approval(project_id, stage, render):
    targets = {
        "direction_review": f"animation-direction-approval-{project_id}",
        "performance_review": f"animation-performance-approval-{project_id}",
        "scene_direction_review": f"scene-direction-review-{project_id}",
        "visual_review": f"animated-visual-review-{project_id}",
        "input_review": f"animation-input-approval-{project_id}",
        "continuity_review": f"animation-continuity-approval-{project_id}",
    }
    if stage in ("direction_review", "performance_review", "scene_direction_review"):
        st.session_state[f"animation-direction-panel-{project_id}"] = True
    if stage == "scene_review":
        pending = next((r for r in render.scenes.values() if not r.approved_hash or r.approved_hash != r.file_hash), None)
        if pending:
            st.session_state[f"animation-scene-panel-{project_id}-{pending.scene_id}"] = True
            targets[stage] = f"animation-scene-approval-{project_id}-{pending.scene_id}"
    if stage in targets:
        st.session_state[f"animation-approval-navigation-{project_id}"] = {"target": targets[stage], "request": uuid4().hex}


def show_animation_approval_highlight(project_id):
    request = st.session_state.get(f"animation-approval-navigation-{project_id}")
    if not request:
        return
    payload = json.dumps({**request, "project": project_id}).replace("<", "\\u003c")
    st.html("""<style>
        .story-approval-highlight {
            outline: 2px solid var(--st-primary-color, #58a6ff);
            outline-offset: 6px;
            border-radius: 0.5rem;
            box-shadow: 0 0 0 8px rgba(88, 166, 255, .12);
            scroll-margin-top: 6rem;
        }
    </style><script>
    (() => {
        const data = __PAYLOAD__;
        const key = 'story-approval-dismissed-' + data.project;
        const registry = window.storyApprovalGuides ||= {};
        const previous = registry[data.project];
        if (previous && previous.request === data.request) return;
        if (previous) previous.cleanup();
        if (sessionStorage.getItem(key) === data.request) return;
        let scrolled = false, dismissed = false;
        const selector = '.st-key-' + data.target;
        const update = () => {
            if (dismissed) return;
            const target = document.querySelector(selector);
            if (!target || !target.getClientRects().length || !target.offsetHeight) return;
            target.classList.add('story-approval-highlight');
            if (!scrolled) {
                scrolled = true;
                target.scrollIntoView({behavior: 'smooth', block: 'center'});
            }
        };
        const observer = new MutationObserver(update);
        const cleanup = () => {
            dismissed = true;
            observer.disconnect();
            document.removeEventListener('pointerdown', press, true);
            document.removeEventListener('click', press, true);
            document.querySelectorAll(selector).forEach(el => el.classList.remove('story-approval-highlight'));
        };
        const press = event => {
            if (event.target instanceof Element && event.target.closest('button, [role="button"]')) {
                sessionStorage.setItem(key, data.request);
                cleanup();
            }
        };
        registry[data.project] = {request: data.request, cleanup};
        observer.observe(document.body, {childList: true, subtree: true});
        document.addEventListener('pointerdown', press, true);
        document.addEventListener('click', press, true);
        requestAnimationFrame(update);
    })();
    </script>""".replace("__PAYLOAD__", payload), unsafe_allow_javascript=True)
