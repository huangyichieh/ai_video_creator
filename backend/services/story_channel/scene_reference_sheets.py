"""Pack scene identities separately from environments under Runway's three-slot limit."""
from PIL import Image,ImageDraw,ImageOps
from .runway_reference_images import padded_size

NOTICE = "Numbered Inputs are labeled on @cast (identity/style), @location (layout), @objects (props/guides). STYLE ONLY panels never define identity. "


def pack(references,roles,destination):
    groups = {"cast":[],"location":[],"objects":[]}
    for index,(path,item) in enumerate(zip(references,roles),1):
        group = "cast" if item["role"] == "character" else "location" if item["role"] in ("background","current") else "objects"
        groups[group].append((index,path,item))
    paths,tags,metadata = [],[],[]
    for tag,items in groups.items():
        if not items:
            continue
        # Larger cells retain facial features and model-sheet linework.
        columns = min(2,len(items))
        base_width,base_height = 1024*columns,896*((len(items)+columns-1)//columns)
        size = padded_size(base_width,base_height)
        sheet = Image.new("RGB",size,"white")
        left,top = (size[0]-base_width)//2,(size[1]-base_height)//2
        draw = ImageDraw.Draw(sheet)
        for position,(index,path,item) in enumerate(items):
            x,y = left+position%columns*1024,top+position//columns*896
            with Image.open(path) as source:
                tile = ImageOps.contain(ImageOps.exif_transpose(source).convert("RGB"),(1000,840))
                sheet.paste(tile,(x+(1024-tile.width)//2,y+40+(840-tile.height)//2))
            label = f"Input {index}: {item['role']} {item['id']}"+(" STYLE ONLY" if item["role"] == "style" else "")
            draw.text((x+12,y+10),label,fill="black")
        file = destination.with_suffix(".references-"+tag+".jpg")
        sheet.save(file,quality=95)
        paths.append(file)
        tags.append(tag)
        metadata.append({"tag":tag,"file":str(file),"inputs":[index for index,_,_ in items]})
    return paths,tags,metadata
