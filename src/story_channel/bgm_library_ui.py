"""Reusable BGM cards shared with episode Check assets."""


def render(manager,project_id):
    from .storyboard_assets_ui import show_requests
    from .audio_asset_cards import library
    show_requests(manager,project_id,"bgm")
    library(manager,project_id,"BGM")
