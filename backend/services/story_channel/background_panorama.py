"""Panorama sources and diagnostics; dimensions alone never prove 360° coverage."""
from pathlib import Path

import numpy as np
from PIL import Image

from .files import input_hash
from .media_providers import ThreadLocalUsage
from .models import GeneratedAsset

VIEWS = ("left", "right", "front", "back")
YAW = {"left": -90, "right": 90, "front": 0, "back": 180}
MODEL = "panorama-perspective-v1"


def view_subject(lid, view):
    return lid if view == "front" else lid + "__" + view


def subjects(lid, *, panorama=True):
    return [lid + "__panorama"] if panorama else [view_subject(lid, view) for view in VIEWS]


def panorama_mode(state, lid):
    if lid in state.location_reference_sets:
        return False
    if state.background_modes.get(lid) == "panorama" or "backgrounds:" + lid + "__panorama" in state.records:
        return True
    # Already-generated four-image libraries remain usable until explicitly
    # regenerated. New backgrounds use the panorama workflow immediately.
    return not any("backgrounds:" + subject in state.records for subject in subjects(lid, panorama=False))


def panorama_tasks(task_type, lid, prompt):
    source = task_type("backgrounds", lid + "__panorama",
        "Create ONE full spherical equirectangular 360-degree environment panorama from a SINGLE fixed camera at the room center. "
        "This is a COMPLETE CIRCLE, not a 180-degree POV, not a wide-angle photo and not a cropped frontal room. "
        "Include the distinct rear surroundings behind the camera as well as front, left and right. "
        "Map the entire horizontal revolution across the whole image, with ceiling and floor at the top and bottom. Horizon at mid-height; "
        "front at the image center, left at one-quarter width, right at three-quarter width, back at both outer edges. "
        "Both outer edges depict the SAME rear wall cut at the SAME meridian: align shelf heights, wall boundaries, floor lines and lighting so they join seamlessly. "
        "A single coherent room: continuous walls, shelves, floor and ceiling; "
        "consistent scale, lighting, materials and object placement across every direction. No panels, collage, text or labels. "
        + prompt, operation="panorama")
    return [source]


def normalize_panorama(path):
    """Keep the provider's complete image. Never stretch a wide POV into 360°."""
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.save(path, format="PNG")


def assess_panorama(path):
    """A boundary discontinuity check, not a semantic coverage certification."""
    with Image.open(path) as image:
        width, height = image.size
        sample = np.asarray(image.convert("RGB").resize((512, 256)), dtype=np.float32)[32:224]
    seam = float(np.abs(sample[:, 0] - sample[:, -1]).mean())
    interior = float((np.abs(sample[:, 1] - sample[:, 0]).mean() + np.abs(sample[:, -1] - sample[:, -2]).mean()) / 2)
    return {"width": width, "height": height, "isEquirectangularLayout": width == height * 2,
            "edgeDifference": round(seam, 2), "nearEdgeDifference": round(interior, 2),
            "seamSuspected": seam > max(18, interior * 4), "coverage360Verified": False}


def reference_keys(state, lid):
    from .location_references import set_keys
    keys = set_keys(state, lid)
    if keys is not None:
        return keys
    return ["backgrounds:" + lid + "__panorama"] if panorama_mode(state, lid) else [
        "backgrounds:" + item for item in subjects(lid, panorama=False)]


def project_view(source, destination, *, yaw, size, fov=100,pitch=0):
    """Bilinear spherical reprojection, wrapping longitude at the panorama seam."""
    width, height = size
    if not 1 <= width <= 4096 or not 1 <= height <= 4096 or not 0 < fov < 180 or not np.isfinite(yaw) or not np.isfinite(pitch) or not -89 <= pitch <= 89:
        raise ValueError("Invalid panorama view dimensions or field of view.")
    with Image.open(source) as image:
        pixels = np.asarray(image.convert("RGB"))
    ph, pw = pixels.shape[:2]
    if pw != 2 * ph:
        raise ValueError("The saved panorama must have a 2:1 equirectangular layout.")
    output = np.empty((height, width, 3), dtype=np.uint8)
    tangent = np.tan(np.deg2rad(fov) / 2)
    angle = np.deg2rad(yaw)
    x = ((np.arange(width, dtype=np.float32) + .5) * 2 / width - 1) * tangent
    tilt = np.deg2rad(pitch)
    for start in range(0, height, 64):
        end = min(height, start + 64)
        y = (1 - (np.arange(start, end, dtype=np.float32) + .5) * 2 / height) * tangent * height / width
        vertical = y[:,None]*np.cos(tilt)+np.sin(tilt)
        forward = np.cos(tilt)-y[:,None]*np.sin(tilt)
        rx = x[None,:]*np.cos(angle)+forward*np.sin(angle)
        rz = forward*np.cos(angle)-x[None,:]*np.sin(angle)
        longitude = np.arctan2(rx,rz)
        sx = (longitude/(2*np.pi)+.5)*pw-.5
        x0 = np.floor(sx).astype(np.int32)
        wx = (sx-x0)[...,None]
        latitude = np.arctan2(vertical,np.sqrt(rx*rx+rz*rz))
        sy = np.clip((.5 - latitude / np.pi) * ph - .5, 0, ph - 1)
        y0 = np.floor(sy).astype(np.int32)
        y1 = np.minimum(y0 + 1, ph - 1)
        wy = (sy - y0)[:, :, None]
        a = pixels[y0, x0 % pw] * (1 - wx) + pixels[y0, (x0 + 1) % pw] * wx
        b = pixels[y1, x0 % pw] * (1 - wx) + pixels[y1, (x0 + 1) % pw] * wx
        output[start:end] = np.clip(np.rint(a * (1 - wy) + b * wy), 0, 255).astype(np.uint8)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(output).save(destination, format="PNG")


class PanoramaViewProvider(ThreadLocalUsage):
    def generate(self, prompt, references, config):
        self.last_usage = {}
        if len(references) != 1:
            raise ValueError("A background view requires exactly one saved panorama.")
        view = config.options["view"]
        width, height = map(int, config.options["size"].split("x"))
        project_view(references[0], config.options["output_path"], yaw=YAW[view], size=(width, height))
        return GeneratedAsset(type="image", provider="local", model=MODEL, file_path=config.options["output_path"],
            prompt_hash=input_hash(prompt), generation_params={"projection": "equirectangular-to-perspective",
                "view": view, "yawDegrees": YAW[view], "horizontalFovDegrees": 100, "sourcePanoramaName": Path(references[0]).name})
