"""Crop-guided regeneration of one detail background; retain source and history."""
import math
from uuid import uuid4

from .files import input_hash


def rectangle(value, size):
    if not isinstance(value,dict):
        raise ValueError("Drag a rectangle over the area to zoom in.")
    points = [value.get(key) for key in ("left","top","right","bottom")]
    if any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) for n in points):
        raise ValueError("Choose a valid zoom rectangle.")
    left,top,right,bottom = points
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("Drag from the top-left to the bottom-right, inside the image.")
    box = (math.floor(left*size[0]),math.floor(top*size[1]),math.ceil(right*size[0]),math.ceil(bottom*size[1]))
    if box[2]-box[0] < 8 or box[3]-box[1] < 8:
        raise ValueError("Select an area at least 8 pixels wide and high.")
    return box


def generate(engine,lid,identity,zoom):
    from PIL import Image
    from .background_library import BackgroundLibrary,detail_yaw
    from .location_details import current,DETAIL_IMAGE_SIZE,validate_image_size
    from .dual_panorama import pair_ready,PANORAMA_MODEL
    from .models import ProviderConfig
    from .library_media import LibraryMedia
    from .style_rules import apply_rules,style_reference
    from .media_utils import file_hash
    library = BackgroundLibrary(engine)
    library._root()
    state = engine.load()
    reference = state.location_reference_sets.get(lid,{}).get("detail_references",{}).get(identity)
    if not reference or input_hash(reference) != zoom.get("expected"):
        raise ValueError("This detail changed. Reopen Zoom on its latest image.")
    if not pair_ready(engine,state,lid) or not current(engine,state,lid,reference):
        raise ValueError("Regenerate this detail from the approved masters before zooming.")
    source = engine.local_path(reference["file"])
    record = state.records[reference["key"]]
    params = record.asset.generation_params
    camera = params.get("camera",{})
    # Validate persisted metadata before a paid request, rather than at commit.
    source_yaw = detail_yaw(reference.get("yaw",camera.get("yaw",0)))
    folder = engine.path/"references"/"detail_candidates"/uuid4().hex
    folder.mkdir(parents=True,exist_ok=True)
    crop_file,output = folder/"zoom_crop.png",folder/"image.png"
    with Image.open(source) as image:
        size = image.size
        box = rectangle(zoom.get("rectangle"),size)
        image.convert("RGB").crop(box).save(crop_file)
    prompt = (
        "Create one high-resolution finished background image by zooming into Image 1, the selected crop of an existing scene. "
        "Use ONLY this crop as the composition reference. Enlarge its contents to fill the output frame; do not return to a wider view "
        "or reintroduce surrounding objects outside the crop. Preserve the selected objects, their relative positions, proportions, "
        "perspective, colors, materials, lighting and visual style. Add fine surface detail and clean edges where the crop lacks resolution, "
        "without inventing, duplicating, removing or moving major objects. No new camera viewpoint. "
        "Keep the complete selected region as much as possible. If its aspect ratio differs from the landscape output, "
        "extend only the minimum necessary edges naturally; never stretch objects. No people, reflections, labels or visible text. "
        "Any additional image is STYLE ONLY. Original detail description (context only; the selected crop determines framing): "
        +reference.get("description",reference.get("area",identity)))
    prompt = apply_rules(prompt,state.project_style_rules)
    prompt,refs = style_reference(engine,state,prompt,[crop_file])
    from .background_constraints import EMPTY_BACKGROUND
    prompt += "\n"+EMPTY_BACKGROUND
    config = ProviderConfig(provider="demo" if engine.demo else "openai",model=PANORAMA_MODEL,
        options={"output_path":str(output),"size":DETAIL_IMAGE_SIZE})
    provider = engine.panorama_image_provider
    asset = LibraryMedia(engine.manager,engine.project_id)._call(provider,provider.generate,(prompt,refs),config,
        "location_detail:"+lid+":"+identity)
    with Image.open(output) as image:
        output_size = image.size
        validate_image_size(output_size,demo=engine.demo)
        image.convert("RGB").save(output,format="PNG")
    # Validate the source pixels again before applying this new revision.
    if file_hash(source) != reference["hash"]:
        raise ValueError("The source image changed during generation. Reopen Zoom.")
    import json
    return library.add_detail_reference(lid,reference.get("area",identity),output.read_bytes(),detail_id=identity,
        object_ids=reference.get("objectIds",[]),description=reference.get("description",""),
        position=reference.get("position"),source_yaw=source_yaw,
        source_assets=json.loads(reference.get("sourceAssets","{}")),expected_reference=zoom["expected"],generation={
            "provider":asset.provider,"model":asset.model,"cost":asset.cost,"prompt":prompt,
            "inputImages":[p.relative_to(engine.path).as_posix() for p in refs],"camera":camera,
            "requestedSize":DETAIL_IMAGE_SIZE,"providerRequestSize":DETAIL_IMAGE_SIZE,"outputSize":list(output_size),
            "sceneReadyBackground":True,"role":"local_detail_only",
            "zoom":{"sourceAssetId":record.asset.asset_id,"sourceFile":reference["file"],"sourceHash":reference["hash"],
                "sourceSize":list(size),"rectangle":zoom["rectangle"],"pixelBox":list(box),
                "cropFile":crop_file.relative_to(engine.path).as_posix(),"cropHash":file_hash(crop_file)}})
