"""Split animations into PNG frames and put rotated frames back together as a GIF.

The rotation itself happens in pic2x.m (see bot.py); this only decodes and encodes.
Uploaded GIFs / animated WebPs are split with Pillow; the MP4s behind GIF links
(Tenor, Klipy, Giphy...) are split with ffmpeg. Pillow builds the output GIF.
"""

import asyncio
import io
from pathlib import Path

from PIL import Image, ImageSequence

MAX_SIDE = 480        # px; reaction GIFs are rarely bigger, and it keeps uploads small
MAX_FRAMES = 300
VIDEO_FPS = 20        # GIF delays are in 1/100 s, so 20 fps = 5/100 s per frame
VIDEO_MAX_SECONDS = 15
SHRINK_STEPS = (1.0, 0.75, 0.5, 0.35)   # tried in turn until the GIF fits the upload limit
FFMPEG_TIMEOUT = 120


def is_animated(data: bytes) -> bool:
    try:
        return getattr(Image.open(io.BytesIO(data)), "n_frames", 1) > 1
    except Exception:
        return False


def image_frames(data: bytes, folder: Path) -> tuple[list[int], int | None]:
    """Write each frame as in_0001.png... Returns (delays in ms, loop count or None)."""
    im = Image.open(io.BytesIO(data))
    loop = im.info.get("loop")
    delays = []
    for i, frame in enumerate(ImageSequence.Iterator(im)):
        if i == MAX_FRAMES:
            break
        d = frame.info.get("duration") or 100
        delays.append(d if d >= 20 else 100)  # browsers show <20 ms delays as 100 ms
        frame = frame.convert("RGBA")  # Pillow composites each frame (disposal applied)
        frame.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        frame.save(folder / f"in_{i + 1:04d}.png")
    return delays, loop


async def video_frames(video: Path, folder: Path) -> tuple[list[int], int | None]:
    scale = f"scale='min({MAX_SIDE},iw)':'min({MAX_SIDE},ih)':force_original_aspect_ratio=decrease"
    await ffmpeg("-i", str(video), "-t", str(VIDEO_MAX_SECONDS), "-an",
                 "-vf", f"fps={VIDEO_FPS},{scale}", "-frames:v", str(MAX_FRAMES),
                 str(folder / "in_%04d.png"))
    n = len(list(folder.glob("in_*.png")))
    if not n:
        raise RuntimeError("couldn't read any frames from the video")
    return [1000 // VIDEO_FPS] * n, 0  # GIF-link videos loop forever in Discord


def make_gif(folder: Path, delays: list[int], loop: int | None, limit: int) -> bytes:
    """Encode out_0001.png... into a GIF with the original delays, shrinking until it
    fits in `limit` bytes. (Pillow keeps exact per-frame delays; ffmpeg rounds them.)"""
    frames = [Image.open(folder / f"out_{i + 1:04d}.png").convert("RGBA") for i in range(len(delays))]
    for scale in SHRINK_STEPS:
        sized = frames if scale == 1 else [
            f.resize((max(1, round(f.width * scale)), max(1, round(f.height * scale))), Image.LANCZOS)
            for f in frames]
        buf = io.BytesIO()
        extra = {} if loop is None else {"loop": loop}  # no loop key = play once
        sized[0].save(buf, "GIF", save_all=True, append_images=sized[1:], duration=delays,
                      disposal=2, optimize=False, **extra)
        if buf.tell() <= limit:
            return buf.getvalue()
    raise RuntimeError(f"the rotated GIF is too big to upload here ({buf.tell() // 1_000_000} MB)")


async def ffmpeg(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), FFMPEG_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("ffmpeg timed out")
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip()[-300:] or "ffmpeg failed")
