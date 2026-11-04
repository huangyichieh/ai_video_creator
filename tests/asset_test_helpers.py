"""Build complete offline character assets for approval-oriented fixtures."""
from story_channel.identity import IdentityManager
from story_channel.library_media import LibraryMedia


def complete_storyboard_assets(manager,project_id):
    """Supply small real files for offline scene-production prerequisites."""
    from story_channel.pipeline import Pipeline
    from story_channel.bgm_library import entries,mood_id
    from story_channel.storyboard_asset_check import sound_id
    from story_channel.media_utils import silence,write_bytes,file_hash
    from story_channel.files import atomic_write
    import json
    path = manager.project_path(project_id)
    board = Pipeline(manager,project_id).artifact("storyboard")
    music = entries(manager,project_id)
    audio = {}
    for scene in board.scenes:
        if scene.bgm_id or scene.bgm_mood.strip():
            key = scene.bgm_id or mood_id(scene.bgm_mood)
            relative = "audio/library/"+key+".wav"
            write_bytes(path/relative,silence(.2))
            music[key] = {"id":key,"title":scene.bgm_mood or key,"description":scene.bgm_mood or key,**music.get(key,{}),"revision":1,
                "file":relative,"fileHash":file_hash(path/relative),"source":"Original test recording"}
        for text in scene.sfx:
            key = sound_id(text)
            relative = "audio/library/"+key+".wav"
            write_bytes(path/relative,silence(.2))
            audio[key] = {"name":text,"file":relative,"fileHash":file_hash(path/relative),"source":"Original test recording"}
    atomic_write(path/"storyboard_bgm.json",json.dumps({"entries":music}))
    atomic_write(path/"storyboard_sfx.json",json.dumps({"entries":audio}))
    from story_channel.storyboard_asset_check import check,approve
    for row in check(manager,project_id):
        if row["Ready"]:
            approve(manager,project_id,row["Category"],row["ID"],expected=row["ContentHash"])


def complete_character_parts(engine, subjects=None):
    if engine.manager.load(engine.project_id).workflow_version != "world_episode":
        return
    identity = IdentityManager(engine.manager, engine.project_id)
    library = identity.load()
    subjects = set(subjects if subjects is not None else library.voices)
    expected = library.model_dump_json(by_alias=True)
    for cid in subjects:
        library.voices[cid].voice_id = library.voices[cid].voice_id or "nova"
    if library.model_dump_json(by_alias=True) != expected:
        identity.save(library, expected=expected)
    media = LibraryMedia(engine.manager, engine.project_id)
    media.assets = engine
    for cid in subjects:
        if not media.voice_approved(cid):
            media.talking_sample(cid, "Keep the beacon burning.", video=False)
            media.approve_voice(cid)
        if engine.character_requirements(cid, engine.load()).get("Costumes"):
            continue
        for costume in library.costumes.values():
            if costume.character_id == cid:
                media.generate_costume(cid, costume.costume_id)
