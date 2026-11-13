"""Remove one scene through the normal versioned storyboard save path."""
from .creative import Storyboard
from .storyboard_editor import save


def remove(pipeline,scene_id,expected):
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    if source != expected:
        raise ValueError("Content changed in another session. Reload before removing this scene.")
    board = Storyboard.model_validate_json(source)
    if not any(scene.scene_id == scene_id for scene in board.scenes):
        raise ValueError("This storyboard scene no longer exists.")
    if len(board.scenes) == 1:
        raise ValueError("Keep at least one storyboard scene.")
    scenes = [scene for scene in board.scenes if scene.scene_id != scene_id]
    for index,scene in enumerate(scenes,1):
        scene.scene_id,scene.order = f"scene_{index:02d}",index
        scene.status,scene.asset_refs = "draft",[]
    data = {**board.model_dump(mode="json",by_alias=True),"scenes":[scene.model_dump(mode="json",by_alias=True) for scene in scenes]}
    return save(pipeline,data,expected)
