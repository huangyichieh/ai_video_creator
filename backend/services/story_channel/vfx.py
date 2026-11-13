"""Deterministic transparent effect layers; never repaints the base animation."""
import math
from pathlib import Path
import random

from PIL import Image, ImageDraw, ImageFilter

from .files import input_hash
from .models import GeneratedAsset


def generate_layer(effect, destination, options, ffmpeg):
    width = min(640, options.width)
    height = max(2, round(width * options.height / options.width))
    folder = destination.parent / "frames"
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(effect.seed)
    particles = [(rng.random(), rng.random(), .2 + rng.random(), rng.random()) for _ in range(round(30 + effect.intensity * 160))]
    count = max(1, math.ceil(effect.duration * options.fps))
    for index in range(count):
        t = index / options.fps
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for px, py, speed, phase in particles:
            x, y = px * width, ((py + t * speed * .45) % 1) * height
            if effect.kind == "rain":
                draw.line((x, y, x - 3, y + 9 + speed * 8), fill=(170, 205, 255, 140), width=1)
            elif effect.kind == "snow":
                x += math.sin(t + phase * 6) * 8
                draw.ellipse((x, y, x + 2 + speed, y + 2 + speed), fill=(245, 250, 255, 210))
            elif effect.kind in ("smoke", "fire"):
                y = height - ((py + t * speed * .12) % 1) * height
                radius = 8 + phase * 30
                color = (145, 155, 165, 24) if effect.kind == "smoke" else (255, int(70 + phase * 130), 15, 60)
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
            elif effect.kind == "sparks":
                y = height - ((py + t * speed * .4) % 1) * height
                draw.line((x, y, x + 2, y + 5), fill=(255, 180, 50, 220), width=2)
        if effect.kind in ("smoke", "fire"):
            image = image.filter(ImageFilter.GaussianBlur(7))
        draw = ImageDraw.Draw(image)
        if effect.kind == "lightning" and int(t * 7) % 5 == 0:
            points = [(width * (.5 + math.sin(i * 2 + effect.seed) * .1), height * i / 8) for i in range(9)]
            draw.line(points, fill=(200, 225, 255, 240), width=3)
        elif effect.kind in ("energy", "hud"):
            radius = min(width, height) * (.2 + .04 * math.sin(t * 3))
            cx, cy = width / 2, height / 2
            color = (40, 220, 255, 210)
            draw.arc((cx - radius, cy - radius, cx + radius, cy + radius), t * 80, t * 80 + 300, fill=color, width=3)
            if effect.kind == "hud":
                draw.line((cx - radius - 15, cy, cx + radius + 15, cy), fill=color, width=1)
                draw.line((cx, cy - radius - 15, cx, cy + radius + 15), fill=color, width=1)
        image.save(folder / f"{index:06d}.png")
    ffmpeg.run(["-framerate", options.fps, "-i", folder / "%06d.png", "-t", effect.duration,
                "-c:v", "qtrle", "-pix_fmt", "argb", "-threads", "2", destination])
    preview = destination.parent / "layer_preview.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={options.fps}:d={effect.duration}",
        "-i", destination, "-filter_complex", "[0:v][1:v]overlay=shortest=1", "-t", effect.duration,
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-threads", "2", preview])
    return GeneratedAsset(type="video", provider="local", model="procedural-vfx-v1", file_path=str(destination),
        duration=effect.duration, prompt_hash=input_hash(effect.model_dump(mode="json")),
        generation_params={"alpha": True, "kind": effect.kind, "seed": effect.seed, "prompt": effect.prompt, "previewFile": str(preview)})
