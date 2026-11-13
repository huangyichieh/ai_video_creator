"""Delivery formats and the shared portrait-safe composition contract."""
import json

LANDSCAPE = "landscape_16_9"
PORTRAIT = "portrait_9_16"
FORMAT_LABELS = {LANDSCAPE:"Landscape · 16:9",PORTRAIT:"Portrait · 9:16"}


def delivery_formats(options):
    return options.delivery_formats or ([PORTRAIT] if options.video_size == "720x1280" else [LANDSCAPE])


def composition_guidance(options):
    if not options.delivery_formats and not options.portrait_safe_area:
        return ""
    text = "Delivery settings: " + json.dumps({"deliveryFormats":delivery_formats(options),
        "portraitSafeArea":options.portrait_safe_area}) + ". "
    if options.portrait_safe_area:
        text += ("Keep important characters, faces, dialogue actions, and story-critical props inside the portrait-safe center region. "
            "For landscape 16:9, this is the central 31.6% of the frame width (x=34.2% to 65.8%), through the full height. "
            "Keep these subjects inside that region throughout motion; do not place key action at either edge. "
            "Keep faces and critical props fully visible with breathing room.")
    if delivery_formats(options) == [PORTRAIT]:
        text += " Compose a native portrait 9:16 frame."
    return text


def saved_options(path):
    from .render_models import RenderState
    file = path/"render.json"
    if not file.exists():
        return None
    return RenderState.model_validate_json(file.read_text(encoding="utf-8")).options


def saved_guidance(path):
    options = saved_options(path)
    return composition_guidance(options) if options else ""


def create_portrait(ffmpeg,source,destination,options):
    """Reframe shared animation pixels; never issue a second paid video request."""
    width,height = min(options.width,options.height),max(options.width,options.height)
    # Delivery is exactly 9:16 even for a legacy/custom landscape resolution.
    width = max(2,round(height*9/16/2)*2)
    ffmpeg.run(["-i",source,"-map","0:v:0","-an","-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={options.fps}",
        "-c:v","libx264","-pix_fmt","yuv420p",destination])
    return ffmpeg.validate(destination)
