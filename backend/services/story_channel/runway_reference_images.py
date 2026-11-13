"""Conservative Gen-4 reference bounds, without cropping or altering originals."""
import math
from PIL import Image,ImageOps

MIN_RATIO = .5
MAX_RATIO = 2.3  # Leave margin below the endpoint's reported 2.355 maximum.


def padded_size(width,height):
    return max(width,math.ceil(height*MIN_RATIO)),max(height,math.ceil(width/MAX_RATIO))


def prepare(references,destination):
    from .media_utils import file_hash
    paths,metadata = [],[]
    for index,path in enumerate(references,1):
        with Image.open(path) as raw:
            image = ImageOps.exif_transpose(raw)
            width,height = image.size
            target = padded_size(width,height)
            transposed = raw.getexif().get(274,1) != 1
            left,top = (target[0]-width)//2,(target[1]-height)//2
            submitted = path
            if target != image.size or transposed:
                canvas = Image.new("RGB",target,"white")
                rgba = image.convert("RGBA")
                canvas.paste(rgba,(left,top),rgba.getchannel("A"))
                submitted = destination.with_suffix(f".reference-{index}-safe.png")
                canvas.save(submitted)
            paths.append(submitted)
            metadata.append({"index":index,"sourceFile":str(path.resolve()),"submittedFile":str(submitted.resolve()),
                "sourceSize":[width,height],"submittedSize":list(target),"exifTransposed":transposed,
                "paddingLTRB":[left,top,target[0]-width-left,target[1]-height-top],
                "sourceHash":file_hash(path),"submittedHash":file_hash(submitted)})
    return paths,metadata
