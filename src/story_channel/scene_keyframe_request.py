"""Build a keyframe edit around the user-selected scene, not its old storyboard pose."""
import json

from .scene_image_references import request as scene_request


def prepare(engine,state,task,config,reference,prompt):
    # Reuse verified asset/style inputs, but not a previous Modify operation or
    # the original generation prompt's authoritative action/camera instructions.
    support_state=state.model_copy(update={"asset_modifications":{
        key:value for key,value in state.asset_modifications.items() if key!=task.key}})
    config.options.pop("modification",None)
    config.options.pop("edit_mask",None)
    _,support_images=scene_request(engine,support_state,task,config)
    support_roles=config.options["scene_reference_roles"]
    path=engine.local_path(reference["asset"]["filePath"])
    roles=[{"role":"scene-edit-base","id":reference["id"],"file":reference["asset"]["filePath"],
        "fileHash":reference["fileHash"]}]
    images=[path]
    for image,role in zip(support_images,support_roles,strict=True):
        if image.resolve()!=path.resolve():
            images.append(image)
            roles.append(role)
    config.options["scene_reference_roles"]=roles
    config.options["reference_tags"]=["scene-edit-base"]+[
        "style" if role["role"]=="style" else f"support{index}" for index,role in enumerate(roles[1:],2)]
    config.options["keyframe_edit"]={"baseInputIndex":1,"referenceId":reference["id"],
        "referenceFile":reference["asset"]["filePath"],"referenceFileHash":reference["fileHash"]}
    legend="; ".join(f"Image {index}={role['role']}:{role['id']}" for index,role in enumerate(roles,1))
    scene=config.options.get("storyboardScene",{})
    context={key:scene[key] for key in ("sceneId","locationId","characterIds","propIds") if key in scene}
    instructions=("EDIT IMAGE 1: the user-selected scene image is the PRIMARY EDIT BASE, not a loose inspiration. "
        "Create the next keyframe by modifying this existing image. Preserve its composition, viewpoint, framing, "
        "room geometry, persistent object positions, character placement, faces, costumes, lighting and rendering style "
        "except where the user's requested change explicitly requires otherwise. "
        "Do not recreate the original storyboard's earlier action or camera framing. "
        "Do not generate a fresh interpretation of the whole scene.\n"
        "REQUESTED CHANGE TO IMAGE 1: "+prompt.strip()+"\n"
        "Images 2 onward are SUPPORTING REFERENCES ONLY: verify character identity, prop designs, location details "
        "and compatible style. They do not replace Image 1's composition or impose a model-sheet layout, panorama, "
        "different camera, different pose or new rendering style. Only apply the requested changes; preserve unrelated details. "
        "Return ONE scene image, no collage, panels, captions or timeline.\n"
        "REFERENCE ORDER: "+legend+"\nSCENE IDENTIFIERS (context only): "+json.dumps(context,ensure_ascii=False))
    styles=[value for value in (state.project_style_rules,config.options.get("generic_prompt"),config.options.get("style_prompt")) if value]
    if styles:
        instructions+="\nCompatible project style guidance; preserve Image 1's established appearance: "+"\n".join(styles)
    return instructions,images
