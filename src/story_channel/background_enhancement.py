"""Optional Runway derivatives; approved panorama/projection assets stay immutable."""
import json
import shutil
from uuid import uuid4

from PIL import Image, ImageDraw, ImageOps

from .dual_panorama import all_views_approved, view_digests, pair_signature, root, verify_protected
from .files import atomic_write, input_hash, read_text
from .media_utils import file_hash
from .locking import project_lock


def approval_signature(engine, state, lid):
    if not all_views_approved(engine, state, lid):
        return None
    return input_hash({"pair": pair_signature(engine, state, lid), "views": view_digests(engine, state, lid)})


def results(engine, lid):
    folder = root(engine, lid) / "runway"
    values = []
    for path in folder.glob("*/result.json"):
        try:
            values.append(json.loads(read_text(path)))
        except (OSError, ValueError):
            continue
    return values


def generate(engine, lid, *, mode="enhancement", yaw=0, prompt="", region=None, provider=None):
    from .models import ProviderConfig
    from .library_media import LibraryMedia
    from .balanced_providers import RunwayImageProvider
    from .media_providers import DemoImageProvider
    from .style_rules import StyleRules
    from .background_panorama import project_view
    from .studio import WorldStudio
    WorldStudio(engine.manager, engine.project_id).require_approved()
    if mode not in ("enhancement", "repair", "additional_reference"):
        raise ValueError("Choose enhancement, repair or additional reference.")
    if not isinstance(yaw, (int, float)) or not 0 <= yaw < 360:
        raise ValueError("Yaw must be between 0 and 359 degrees.")
    if mode != "additional_reference" and yaw not in range(0, 360, 45):
        raise ValueError("Enhancement/repair uses one of the eight approved yaw views.")
    if mode == "repair" and (not region or len(region) != 4 or not all(0 <= value <= 1 for value in region)
            or region[2] <= 0 or region[3] <= 0 or region[0]+region[2] > 1 or region[1]+region[3] > 1
            or region == (0, 0, 1, 1)):
        raise ValueError("Choose a local repair rectangle inside the image; some pixels must remain protected.")
    with project_lock(engine.path):
        state = engine.load()
        signature = approval_signature(engine, state, lid)
        if not signature:
            raise ValueError("Approve all eight current yaw images before optional Runway work.")
        work = root(engine, lid) / "runway" / uuid4().hex[:16]
        work.mkdir(parents=True)
        source = work / "source.png"
        if mode == "additional_reference":
            width, height = map(int, engine.settings.image_size.split("x"))
            project_view(root(engine, lid) / "panorama/atlas.png", source, yaw=yaw, size=(width, height), fov=70)
        else:
            record = state.records[f"backgrounds:{lid}__view_{int(yaw):03d}"]
            shutil.copyfile(engine.local_path(record.asset.file_path), source)
        guide = source
        mask = None
        if mode == "repair":
            with Image.open(source) as image:
                width, height = image.size
                x, y, w, h = region
                box = (round(x*width), round(y*height), round((x+w)*width), round((y+h)*height))
                if box[2] <= box[0] or box[3] <= box[1]:
                    raise ValueError("The repair region is too small.")
                mask = work / "mask.png"
                bitmap = Image.new("L", image.size, 0)
                bitmap.paste(255, box)
                if not bitmap.getextrema()[0] == 0:
                    raise ValueError("The repair region must leave protected pixels.")
                bitmap.save(mask)
                annotated = image.convert("RGB")
                ImageDraw.Draw(annotated).rectangle((box[0], box[1], box[2]-1, box[3]-1), outline="red", width=2)
                guide = work / "repair_guide.png"
                annotated.save(guide)
        side = "front_panorama" if min(yaw,360-yaw) <= 90 else "back_panorama"
        master = state.records[f"backgrounds:{lid}__{side}"].asset.file_path
        master_copy = work / "master.png"
        shutil.copyfile(engine.local_path(master), master_copy)
        refs, tags = [guide, master_copy], ["view", "master"]
        style = StyleRules(engine).image(state)
        if style:
            saved_style = work / ("style"+style.suffix)
            shutil.copyfile(style, saved_style)
            refs.append(saved_style)
            tags.append("style")
        layout = state.location_reference_sets[lid]
        source_assets = {key:[record.asset.asset_id, record.file_hash] for key,record in state.records.items()
                         if key.startswith("backgrounds:"+lid+"__") and record.asset
                         and key.rsplit("__",1)[-1] in {"front_panorama","back_panorama",*(f"view_{angle:03d}" for angle in range(0,360,45))}}
        context = json.dumps({"request": prompt, "floorPlan": layout.get("floor_plan"),
                              "objectLayout": layout.get("object_layout"), "styleRules": state.project_style_rules}, ensure_ascii=False)
    footer = (f"Improve @view, an optional {mode} reference at yaw {yaw}, pitch 0, FOV 70. "
        "Keep framing, camera center/height, room geometry, object positions, materials and lighting. "
        "@master is approved world geometry, not framing. No people, reflections or visible text. ")
    if mode == "repair":
        footer += "Repair only inside the red rectangle; remove its red outline. Other pixels are protected. "
    if style:
        footer += "@style STYLE ONLY; copy no objects/layout."
    if len((context+"\n"+footer).encode("utf-16-le"))//2 > 999 and not engine.demo:
        from .provider_prompts import compile_prompt
        context = compile_prompt(engine.creative, context, background=True,
                                 task_key=f"background:runway:{lid}:prompt", max_units=998-len(footer.encode("utf-16-le"))//2)
    submitted = context+"\n"+footer
    config = ProviderConfig(provider="demo" if engine.demo else "runway", model="gen4_image",
        options={"output_path": str(work / "candidate.png"), "size": engine.settings.image_size, "reference_tags": tags})
    provider = provider or (DemoImageProvider() if engine.demo else RunwayImageProvider())
    asset = LibraryMedia(engine.manager, engine.project_id)._call(provider, provider.generate,
        (submitted, refs), config, "background:runway:"+lid)
    candidate = engine.local_path(asset.file_path)
    result = work / "result.png"
    if mask:
        with Image.open(source) as original, Image.open(candidate) as changed, Image.open(mask) as bitmap:
            changed = ImageOps.fit(changed.convert("RGB"), original.size)
            Image.composite(changed, original.convert("RGB"), bitmap).save(result)
        verify_protected(source, result, mask)
    else:
        shutil.copyfile(candidate, result)
    asset.file_path = result.relative_to(engine.path).as_posix()
    metadata = {"mode": mode, "yaw": yaw, "sourceImage": source.relative_to(engine.path).as_posix(),
        "sourceHash": file_hash(source), "approvalSignature": signature, "prompt": asset.generation_params.get("submittedPrompt", submitted),
        "protectedPixelsVerified": bool(mask), "region": region,
        "fileHash": file_hash(result), "asset": asset.model_dump(mode="json", by_alias=True), "status": "needs_review",
        "canonicalYawReplaced": False}
    metadata["sourceAssets"] = source_assets
    with project_lock(engine.path):
        metadata["sourceStillCurrent"] = approval_signature(engine, engine.load(), lid) == signature
        atomic_write(work / "result.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    return metadata


def use_reference(engine, lid, result, area):
    from .background_library import BackgroundLibrary
    if not area.strip():
        raise ValueError("Enter a target area or reference name.")
    if result["approvalSignature"] != approval_signature(engine, engine.load(), lid):
        raise ValueError("The approved source images changed; generate/review a current optional reference first.")
    path = engine.local_path(result["asset"]["filePath"])
    if not path.resolve().is_relative_to((root(engine,lid)/"runway").resolve()) or file_hash(path) != result["fileHash"]:
        raise ValueError("The saved optional reference changed or is missing.")
    library = BackgroundLibrary(engine)
    library.add_detail_reference(lid, area, path.read_bytes(), source_yaw=result["yaw"], source_assets=result["sourceAssets"])
    library.approve(lid)
    metadata = {**result, "status":"approved_reference", "registeredArea":area.strip()}
    atomic_write(path.with_name("result.json"),json.dumps(metadata,ensure_ascii=False,indent=2))
    return metadata
