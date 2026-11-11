"""One world-scoped episode selection shared with the sidebar and production pages."""
import streamlit as st
from .ui_panels import polling_fragment


def episode_status(manager,project):
    from .file_cache import read_model
    from .files import input_hash
    from .modules import MODULES
    def content_approved(name):
        approval = project.content_approvals.get(name)
        if not approval:
            return False
        try:
            if name == "outline":
                digest = input_hash({"title":project.title,"outline":manager.read_outline(project.project_id),"duration":project.target_duration_sec})
            else:
                module = MODULES[name]
                output = read_model(manager.project_path(project.project_id)/module.filename,module.model)
                digest = input_hash(output.model_dump(mode="json",by_alias=True))
            return approval.content_hash == digest
        except (OSError,ValueError):
            return False
    prompts = project.steps.get("prompts")
    if prompts and prompts.status == "succeeded" and content_approved("prompts"):
        return "READY TO ANIMATE"
    if not content_approved("outline"):
        return "OUTLINE · AWAITING APPROVAL"
    for name in ("story","script","storyboard","prompts"):
        state = project.steps.get(name)
        title = "ANIMATION PREPARATION" if name == "prompts" else name.upper()
        if not state or state.status in ("not_started","pending"):
            return title+" · NOT STARTED"
        if state.status in ("running","failed","stale"):
            return title+" · "+{"running":"GENERATING","failed":"FAILED","stale":"NEEDS UPDATE"}[state.status]
        approved = name in project.approvals if name in ("story","storyboard") else content_approved(name)
        if not approved:
            review = project.steps.get("review") if name == "story" else None
            if review and review.status in ("running","failed"):
                return "STORY REVIEW · "+("REVIEWING" if review.status == "running" else "FAILED")
            return title+" · AWAITING APPROVAL"
    return "ANIMATION PREPARATION · PENDING"


def select_episode(world_id,episode_id):
    st.session_state[f"selected-episode-{world_id}"] = episode_id
    for key in ("active-stage-browser","modify-target","studio-modify-target"):
        st.session_state.pop(key,None)


def episode_list(world_id,episodes,selected,*,manager=None,status_view="story"):
    episodes = sorted(episodes,key=lambda project:(project.created_at,project.project_id),reverse=True)
    st.markdown("### Episodes")
    if not episodes:
        st.caption("No episodes yet. Create one on Story/Episode.")
        return
    from .animation_episode_status import animation_episode_status
    status_column = "Animation status" if status_view == "animation" else "Story/Episode status"
    rows = [{"Episode":index+1,"Title":project.title,"ID":project.project_id[:8],
        "Target length":f"{project.target_duration_sec//3600:02d}:{project.target_duration_sec//60%60:02d}:{project.target_duration_sec%60:02d}",
        status_column:(animation_episode_status(manager,project) if status_view == "animation" else episode_status(manager,project))
            if manager else project.status.value} for index,project in enumerate(episodes)]
    # A new widget key follows external/sidebar selection and restores its row highlight.
    from .files import input_hash
    order = input_hash([project.project_id for project in episodes])[:12]
    key = f"episode-list-{world_id}-{selected}-{order}"
    def changed():
        event = st.session_state.get(key,{})
        indices = event.get("selection",{}).get("rows",[])
        if indices and 0 <= indices[0] < len(episodes):
            select_episode(world_id,episodes[indices[0]].project_id)
    st.dataframe(rows,key=key,hide_index=True,width="stretch",on_select=changed,
        selection_mode="single-row",selection_default={"selection":{"rows":[next(i for i,p in enumerate(episodes) if p.project_id == selected)]}})
    st.caption("Select an episode row. The tabs below show only that episode; drafts stay with their episode.")


@polling_fragment
def animation_episode_list(manager,world_id,selected):
    from .studio import Episodes
    episodes = Episodes(manager,world_id).list()
    key = f"selected-episode-{world_id}"
    current = st.session_state.get(key,selected)
    if current not in {project.project_id for project in episodes}:
        current = episodes[0].project_id if episodes else None
        if current:
            st.session_state[key] = current
        else:
            st.session_state.pop(key,None)
    if current != selected:
        st.rerun(scope="app")
    episode_list(world_id,episodes,selected,manager=manager,status_view="animation")
