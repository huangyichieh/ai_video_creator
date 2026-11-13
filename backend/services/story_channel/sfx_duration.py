"""Storyboard SFX recommendations, separate from saved user overrides."""
def scene_settings(scene,cue):
    timing = next((item for item in scene.sfx_timing if item.description == cue),None)
    if timing:
        duration,loop = timing.duration_sec,timing.loop
    else:
        # Legacy/manual cues have no AI timing. Use conservative sound-type defaults.
        text = cue.casefold()
        ambience = any(word in text for word in ("ambience","ambient","room tone","rain","wind","環境音","雨聲","風聲"))
        transient = any(word in text for word in ("click","tick","snap","beep","knock","點擊","滴答","咔","嗶","敲"))
        duration,loop = (scene.duration_sec,True) if ambience else (.5,False) if transient else (2.,False)
    return {"duration":max(.5,min(30.,scene.duration_sec,duration)),"loop":loop}


def recommended(manager,project_id,identity):
    from .storyboard_asset_check import document,sound_id
    from .creative import Storyboard
    data = document(manager.project_path(project_id),"scenes.json")
    matches = []
    if data:
        board = Storyboard.model_validate(data)
        for scene in board.scenes:
            for cue in scene.sfx:
                if sound_id(cue) == identity:
                    matches.append(scene_settings(scene,cue))
    if matches:
        return {"duration":max(item["duration"] for item in matches),"loop":any(item["loop"] for item in matches)}
    from .storyboard_assets import requests
    row = next((row for row in requests(manager,project_id,"sfx") if row["id"] == identity),None)
    return {"duration":row["duration"],"loop":row.get("loop",False)} if row and "duration" in row else {}
