"""Create a reusable asset declaration and link it to one storyboard draft scene."""
from copy import deepcopy
from uuid import uuid4

from .creative import Location,StoryboardProp,StoryboardBGM


FIELDS = {"Background":("backgrounds","locationId","background"),
          "Prop":("props","propId","prop"),"BGM":("bgm","bgmId","bgm")}


def add_requirement(board,catalog,scene_id,kind,description,title=""):
    if kind not in FIELDS:
        raise ValueError("Choose Background, Prop or BGM.")
    description = description.strip()
    if not description:
        raise ValueError("Enter a short asset description.")
    data = deepcopy(board)
    scene = next((item for item in data["scenes"] if item["sceneId"] == scene_id),None)
    if scene is None:
        raise ValueError("This storyboard scene no longer exists.")
    field,id_field,prefix = FIELDS[kind]
    known = set(catalog[field]) | {item[id_field] for item in data[field]}
    identity = prefix+"_"+uuid4().hex[:12]
    while identity in known:
        identity = prefix+"_"+uuid4().hex[:12]
    title = title.strip() or " ".join(description.split())[:60]
    if kind == "Background":
        item = Location(location_id=identity,name=title,description=description)
        scene["locationId"],scene["targetArea"] = identity,""
    elif kind == "Prop":
        item = StoryboardProp(prop_id=identity,title=title,description=description)
        scene["propIds"] = [*scene["propIds"],identity]
    else:
        item = StoryboardBGM(bgm_id=identity,title=title,description=description)
        scene["bgmId"] = identity
    data[field].append(item.model_dump(mode="json",by_alias=True))
    return data,identity
