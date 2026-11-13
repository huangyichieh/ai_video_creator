"""Local FFmpeg execution, clip generation, audio mixing and video assembly."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import wave
from threading import Event, Thread
from array import array

from .files import atomic_write


class FFmpeg:
    def __init__(self, executable=None, timeout=1800):
        if not executable:
            import imageio_ffmpeg
            executable = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
        self.executable, self.timeout = str(executable), timeout

    def run(self, args, *, cwd=None):
        try:
            result = subprocess.run([self.executable, "-hide_banner", "-nostdin", "-y", *map(str, args)], cwd=cwd,
                                    capture_output=True, encoding="utf-8", errors="replace", timeout=self.timeout,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"FFmpeg could not finish: {exc}") from exc
        if result.returncode:
            raise RuntimeError("FFmpeg failed: " + result.stderr[-2200:])
        return result

    def validate(self, path):
        result = self.run(["-i", path, "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"])
        match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        if not match:
            raise ValueError("Rendered media has no valid duration")
        return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])

    def visual(self, image, destination, duration, options):
        w, h, fps = options.width, options.height, options.fps
        base = f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,crop={w*2}:{h*2}"
        if options.motion == "zoom_pan":
            base += f",zoompan=z='min(zoom+0.0005,1.08)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={w}x{h}:fps={fps}"
        else:
            base += f",scale={w}:{h},fps={fps}"
        self.run(["-loop", "1", "-i", image, "-t", duration, "-vf", base, "-an", "-c:v", "libx264",
                  "-preset", "veryfast", "-pix_fmt", "yuv420p", "-threads", "2", "-movflags", "+faststart", destination])

    def compose_scene(self, visual, voice, srt, destination, duration, options, music=None, effects=(), overlays=()):
        work = destination.parent
        local_srt = work / "subtitles.srt"
        shutil.copyfile(srt, local_srt)
        args = ["-stream_loop", "-1", "-i", visual, "-i", voice]
        vf = f"scale={options.width}:{options.height}:force_original_aspect_ratio=increase,crop={options.width}:{options.height},setsar=1,fps={options.fps},trim=duration={duration},setpts=PTS-STARTPTS"
        finish_vf = ""
        fade = min(options.transition_seconds, duration / 3)
        if options.transition == "fade" and fade:
            finish_vf += f",fade=t=in:d={fade},fade=t=out:st={duration-fade}:d={fade}"
        if options.subtitles == "burn":
            finish_vf += f",subtitles=filename=subtitles.srt:charenc=UTF-8:force_style='FontName={options.font},FontSize={options.font_size},Outline=1,MarginV=20'"
        filters = [f"[0:v]{vf}[base]"]
        voice_filter = "aresample=48000,aformat=channel_layouts=stereo,"
        if options.normalize_voice:
            voice_filter += "loudnorm=I=-16:TP=-1.5:LRA=11,"
        voice_filter += f"apad,atrim=duration={duration},asetpts=PTS-STARTPTS"
        if music and options.duck_music:
            filters.append(f"[1:a]{voice_filter},asplit=2[voice][sidechain]")
        else:
            filters.append(f"[1:a]{voice_filter}[voice]")
        mix = ["[voice]"]
        input_index = 2
        if music:
            args += ["-stream_loop", "-1", "-i", music]
            filters.append(f"[{input_index}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={duration},asetpts=PTS-STARTPTS,volume={options.music_gain}[music]")
            if options.duck_music:
                filters.append("[music][sidechain]sidechaincompress=threshold=0.03:ratio=8:attack=10:release=300[ducked]")
                mix.append("[ducked]")
            else:
                mix.append("[music]")
            input_index += 1
        for i, (file, cue) in enumerate(effects):
            if cue.loop:
                args += ["-stream_loop", "-1"]
            args += ["-i", file]
            delay = round(cue.at * 1000)
            filters.append(f"[{input_index}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={cue.duration},asetpts=PTS-STARTPTS,volume={options.sfx_gain},adelay={delay}|{delay}[effect{i}]")
            mix.append(f"[effect{i}]")
            input_index += 1
        filters.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95:latency=1[audio]")
        base = "base"
        for index, (file, layer) in enumerate(overlays):
            args += ["-stream_loop", "-1", "-i", file]
            key = ",chromakey=0x00FF00:0.18:0.08" if layer.kind == "openai" else ""
            filters.append(f"[{input_index}:v]scale={options.width}:{options.height},setsar=1,fps={options.fps},"
                f"format=rgba{key},colorchannelmixer=aa={layer.opacity},trim=duration={layer.duration},"
                f"setpts=PTS-STARTPTS+{layer.at}/TB[layer{index}]")
            filters.append(f"[{base}][layer{index}]overlay=eof_action=pass:repeatlast=0:enable='between(t,{layer.at},{layer.at+layer.duration})'[over{index}]")
            base = f"over{index}"
            input_index += 1
        filters.append(f"[{base}]null{finish_vf}[video]")
        args += ["-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]", "-t", duration,
                 "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                 "-ar", "48000", "-ac", "2", "-threads", "2", "-movflags", "+faststart", destination.name]
        self.run(args, cwd=work)
        actual = self.validate(destination)
        if abs(actual - duration) > max(.15, 2/options.fps):
            raise ValueError("Rendered scene duration does not match its voice timeline")

    def assemble(self, clips, destination, subtitles=None, *, durations=None, fps=24, progress=None):
        # Copy/link names to a controlled folder: no concat-file quoting of user paths.
        work = destination.parent
        for index, clip in enumerate(clips):
            target = work / f"part_{index:06d}.mp4"
            try:
                os.link(clip, target)
            except OSError:
                shutil.copyfile(clip, target)
        atomic_write(work / "concat.txt", "".join(
            f"file 'part_{i:06d}.mp4'\n" + (f"duration {durations[i]:.9f}\n" if durations else "")
            for i in range(len(clips))))
        args = ["-f", "concat", "-safe", "1", "-i", "concat.txt"]
        if subtitles:
            shutil.copyfile(subtitles, work / "captions.srt")
            args += ["-i", "captions.srt", "-map", "0:v:0", "-map", "0:a:0", "-map", "1:0", "-c:s", "mov_text"]
        else:
            args += ["-map", "0:v:0", "-map", "0:a:0"]
        # Use actual voice offsets rather than accumulating MP4/AAC padding.
        args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-af", "aresample=async=1:first_pts=0"]
        if durations:
            args += ["-t", sum(durations)]
        progress_file = work / "ffmpeg-progress.txt"
        stopped = Event()

        def report():
            try:
                content = progress_file.read_text(encoding="utf-8")
                times = re.findall(r"^out_time_us=(\d+)$", content, re.MULTILINE)
                if times and durations:
                    progress(min(.99, int(times[-1]) / 1_000_000 / sum(durations)))
            except (OSError, ValueError):
                pass

        def monitor():
            while not stopped.wait(.25):
                report()

        thread = Thread(target=monitor, daemon=True) if progress else None
        if thread:
            thread.start()
        try:
            self.run([*args, *(["-progress", str(progress_file.resolve()), "-nostats"] if progress else []),
                      "-movflags", "+faststart", destination.name], cwd=work)
        finally:
            stopped.set()
            if thread:
                thread.join()
                report()
                progress_file.unlink(missing_ok=True)
        if progress:
            progress(1.)
        actual = self.validate(destination)
        if durations and abs(actual - sum(durations)) > max(.15, 2/fps):
            raise ValueError("Final duration does not match the recorded voice timeline")

    def cut_frame(self, clip, destination, seconds):
        self.run(["-ss", max(0, seconds), "-i", clip, "-frames:v", "1", "-update", "1", destination])
        if not destination.exists():
            raise ValueError("Could not extract a continuity frame")

    def slice_visual(self, clip, destination, start, duration, options):
        self.run(["-stream_loop", "-1", "-i", clip, "-ss", start, "-t", duration, "-an", "-vf",
            f"scale={options.width}:{options.height}:force_original_aspect_ratio=increase,crop={options.width}:{options.height},setsar=1,fps={options.fps}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-threads", "2", destination])

    def join_visuals(self, clips, durations, destination, options):
        work = destination.parent
        for index, (clip, duration) in enumerate(zip(clips, durations)):
            self.run(["-i", clip, "-t", duration, "-an", "-vf",
                f"scale={options.width}:{options.height}:force_original_aspect_ratio=increase,crop={options.width}:{options.height},setsar=1,fps={options.fps}",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-threads", "2", work / f"shot_{index:05d}.mp4"])
        atomic_write(work / "shots.txt", "".join(f"file 'shot_{i:05d}.mp4'\nduration {duration:.9f}\n" for i, duration in enumerate(durations)))
        self.run(["-f", "concat", "-safe", "1", "-i", "shots.txt", "-an", "-c:v", "copy", "-t", sum(durations), "-movflags", "+faststart", destination.name], cwd=work)
        self.validate(destination)


def synthesized_sound(path, name, duration, *, music=False):
    """A deterministic local pad/cue; provenance is stored in the music plan."""
    import hashlib
    seed = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)
    frequency = (110 + seed % 110) if music else (300 + seed % 700)
    rate = 24000
    # Music is looped, so a short pad suffices for arbitrarily long scenes.
    seconds = min(duration, 4) if music else min(duration, 10)
    count = max(1, round(seconds * rate))
    samples = array("h")
    for index in range(count):
        t = index / rate
        envelope = min(1, t/.1, (seconds-t)/.1) if music else math.exp(-5*t/max(seconds,.01))
        value = (math.sin(2*math.pi*frequency*t) + .4*math.sin(2*math.pi*frequency*1.5*t)) / 1.4
        samples.append(round(9000 * max(0, envelope) * value))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples.tobytes())


def combined_subtitles(scenes, engine):
    from .asset_models import Cue
    from .media_utils import subtitle_text
    cues, cursor = [], 0.0
    for scene in scenes:
        data = json.loads(engine.local_path(scene.timing).read_text(encoding="utf-8"))
        for item in data["segments"]:
            cue = Cue.model_validate(item)
            cues.append(Cue(start=cursor+cue.start, end=cursor+cue.end, text=cue.text, speaker=cue.speaker))
        cursor += scene.duration
    return subtitle_text(cues), subtitle_text(cues, vtt=True)
