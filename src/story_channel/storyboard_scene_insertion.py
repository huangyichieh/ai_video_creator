"""Generate one scene at a selected gap without rewriting its neighbors."""
from .creative import Storyboard
from .models import Scene
from .storyboard_editor import inventory,materialize,save


def insert(pipeline,position,expected,instructions,*,provider=None):
    if not instructions.strip():
        raise ValueError("Enter a prompt for the new scene.")
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    if source != expected:
        raise ValueError("Content changed in another session. Reload before adding a scene.")
    board = Storyboard.model_validate_json(source)
    if isinstance(position,bool) or not isinstance(position,int) or not 0 <= position <= len(board.scenes):
        raise ValueError("This scene insertion position is no longer valid.")
    catalog = inventory(pipeline)
    context = pipeline._context("storyboard")
    context.update(manualStoryboardEdit=True,storyboard=board.model_dump(mode="json",by_alias=True),
        insertionPosition=position+1,
        previousScene=board.scenes[position-1].model_dump(mode="json",by_alias=True) if position else None,
        nextScene=board.scenes[position].model_dump(mode="json",by_alias=True) if position < len(board.scenes) else None,
        availableBackgrounds=catalog["backgrounds"],availableProps=catalog["props"],availableBGM=catalog["bgm"],
        availableDetailBackgrounds=catalog["detail_backgrounds"])
    from .storyboard_details import reference_context
    context.update(reference_context(pipeline.manager,pipeline.project_id))
    neighbor = board.scenes[max(0,position-1)]
    seed = Scene(scene_id="new_scene",order=position+1,duration_sec=5,location_id=neighbor.location_id)

    def candidate(scene):
        scenes = [*board.scenes[:position],scene,*board.scenes[position:]]
        scenes = [item.model_copy(deep=True) for item in scenes]
        for index,item in enumerate(scenes,1):
            item.scene_id,item.order = f"scene_{index:02d}",index
            item.status,item.asset_refs = "draft",[]
        data = {**board.model_dump(mode="json",by_alias=True),
            "scenes":[item.model_dump(mode="json",by_alias=True) for item in scenes]}
        return materialize(data,catalog)

    def validate(scene):
        from .modules import validate_output
        validate_output("storyboard",candidate(scene),context)
        return scene

    from .storyboard_planning import MINIMAL_ASSETS
    request = (MINIMAL_ASSETS+"Create exactly ONE NEW storyboard scene at insertionPosition, between previousScene "
        "and nextScene (null means the start or end of the episode). Follow this user prompt: "+instructions.strip()
        +". Preserve narrative, spatial, character and language continuity with the script and neighboring scenes. "
        "Return only the new scene, never rewrite or repeat existing scenes. Choose a reasonable durationSec for its action; "
        "existing scene durations stay unchanged and the storyboard becomes longer. CURRENT is only an unfinished template; "
        "its location and duration may change to fit the prompt. Reuse known cast IDs and declared/available backgrounds, "
        "detail backgrounds, props and BGM; never invent unregistered IDs. For a detail background use its parent locationId "
        "and detailId as targetArea. Only include new dialogue or narration when the prompt requires it, using existing "
        "characters and appropriate audio tags. Populate action, visualDescription, shotType and cameraMovement. "
        "Use status draft and empty assetRefs. The app assigns consecutive scene IDs and orders.")
    from .modification import modify_content
    result = modify_content(pipeline,seed,request,"storyboard",context=context,provider=provider,creation=True,
        allow_new_ids=True,task_key=f"storyboard_insert:{position}",validate_output=validate,retry_validation=True)
    return save(pipeline,candidate(result).model_dump(mode="json",by_alias=True),expected)
