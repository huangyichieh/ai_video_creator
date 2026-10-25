import hashlib
import io
import os
import tempfile
import wave
from pathlib import Path

from .asset_models import Cue


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        frames = audio.getnframes()
        width = audio.getnchannels() * audio.getsampwidth()
        if frames * width > Path(path).stat().st_size:
            # Streaming WAV responses can leave the data length at 0xffffffff.
            # Count actual PCM frames instead of treating that sentinel as audio.
            size = 0
            while block := audio.readframes(65536):
                size += len(block)
            frames = size // width
        return frames / audio.getframerate()


def silence(seconds: float = 0.25) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * max(1, round(seconds * 24000)))
    return output.getvalue()


def combine_wav(paths: list[Path]) -> bytes:
    if not paths:
        return silence()
    output = io.BytesIO()
    with wave.open(output, "wb") as result:
        signature = None
        for path in paths:
            with wave.open(str(path), "rb") as source:
                current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                if signature and current != signature:
                    raise ValueError("Voice tracks must use the same PCM WAV format")
                if signature is None:
                    result.setnchannels(current[0])
                    result.setsampwidth(current[1])
                    result.setframerate(current[2])
                signature = current
                result.writeframes(source.readframes(source.getnframes()))
    return output.getvalue()


def timestamp(seconds: float, vtt=False) -> str:
    ms = round(seconds * 1000)
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{'.' if vtt else ','}{ms:03d}"


def subtitle_text(cues: list[Cue], *, vtt=False) -> str:
    blocks = []
    for index, cue in enumerate(cues, 1):
        text = (f"{cue.speaker}: " if cue.speaker and cue.speaker != "narrator" else "") + cue.text
        blocks.append(f"{index}\n{timestamp(cue.start, vtt)} --> {timestamp(cue.end, vtt)}\n{text}\n")
    return ("WEBVTT\n\n" if vtt else "") + "\n".join(blocks)


def text_chunks(text: str, limit: int = 3000) -> list[str]:
    return [text[index:index + limit] for index in range(0, len(text), limit) if text[index:index + limit].strip()]


def pad_performance(path, performance):
    """Add directed pauses as real silence; alignment follows the resulting audio."""
    import wave
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        data = source.readframes(source.getnframes())
    frame_bytes = params.nchannels * params.sampwidth
    before = bytes(round(performance.get("pause_before", 0) * params.framerate) * frame_bytes)
    after = bytes(round(performance.get("pause_after", 0) * params.framerate) * frame_bytes)
    with wave.open(str(path), "wb") as target:
        target.setparams(params)
        target.writeframes(before + data + after)

