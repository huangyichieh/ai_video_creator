"""Read-only episode progress shared by HTTP and compatibility UI."""
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
