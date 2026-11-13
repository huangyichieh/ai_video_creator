"""Small cached UI thumbnails; generated originals remain untouched."""
from hashlib import sha256
from threading import Lock
from pathlib import Path
from PIL import Image,ImageOps
from backend.paths import storage_paths

_lock=Lock()

def thumbnail(source:Path,width:int):
    stamp=source.stat()
    key=sha256(f'{source}:{stamp.st_mtime_ns}:{stamp.st_size}:{width}'.encode()).hexdigest()
    folder=storage_paths().ensure().temp/'thumbnails'
    folder.mkdir(exist_ok=True)
    target=folder/(key+'.webp')
    with _lock:
        if not target.is_file():
            with Image.open(source) as original:
                image=ImageOps.exif_transpose(original)
                image.thumbnail((width,width),Image.Resampling.LANCZOS)
                image.save(target,'WEBP',quality=82)
    return target
