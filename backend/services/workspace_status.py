"""Read-only next-step labels for the workspace header."""
from story_channel.file_cache import read_json


def next_step(manager, manifest, page, computed, assets):
    action = computed.get('nextAction')
    if isinstance(action, dict) and action.get('title'):
        return f"{action.get('page') or 'Animation'}: {action['title']}"
    owner = manifest.world_project_id or manifest.project_id
    file = manager.project_path(owner) / 'world_setting.json'
    if file.exists():
        world = read_json(file)
        if not world.get('approval'):
            review = world.get('review')
            if not review:
                return 'World: AI Review'
            if not review.get('decision'):
                return 'World: Apply / revert suggestions'
            return 'World: Approve world settings'
    if page in ('world', 'assets'):
        from story_channel.assets_workflow import minimum_asset_inventory
        characters, backgrounds = minimum_asset_inventory(assets)
        if not characters:
            return 'Assets: Add a character'
        if not backgrounds:
            return 'Assets: Add a background'
        return 'Story/Episode: Create or select an episode'
    if page == 'story':
        if manifest.workflow_version == 'world_episode' and not manifest.world_project_id:
            return 'Story/Episode: Create or select an episode'
        if manifest.world_project_id:
            from story_channel.episode_status import episode_status
            status = episode_status(manager, manifest)
            if status == 'READY TO ANIMATE':
                return 'Animation: Check assets'
            return 'Story/Episode: ' + status.capitalize()
        return 'Story/Episode: ' + manifest.current_step.replace('_', ' ').title()
    if page == 'animations':
        return 'Animation: Check assets'
    if page == 'final':
        if manifest.final_approval:
            return 'Final: Complete'
        if manifest.render_state == 'review':
            return 'Final: Review and approve final video'
        return 'Final: Timeline / compositing'
    return 'Analytics: Review project costs'
