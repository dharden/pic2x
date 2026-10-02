"""pic2x': a Discord bot. React to an image with ↪️ or ↩️ and it gets rotated by Octave."""

import asyncio
import io
import logging
import os
import tempfile
from pathlib import Path

import discord
from PIL import Image

TOKEN = os.environ["DISCORD_TOKEN"]
BOT_NAME = os.environ.get("BOT_NAME", "pic2x'")
OCTAVE = os.environ.get("OCTAVE_BIN", "octave-cli")
OCTAVE_TIMEOUT = 60  # seconds per image
MAX_BYTES = 20 * 1024 * 1024
HERE = Path(__file__).resolve().parent

# Reaction emoji -> k for rot90 (positive = counterclockwise)
ROTATIONS = {"↪": -1, "↩": 1}
# Formats Octave can write back as-is; anything else (webp, gif, etc.) comes back as PNG
KEEP_FORMAT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}

log = logging.getLogger("pic2x")

intents = discord.Intents.default()
intents.message_content = True  # needed to see attachments on other people's messages
client = discord.Client(intents=intents)
octave_slots = asyncio.Semaphore(2)  # max concurrent Octave processes


async def run_octave(src: Path, dst: Path, k: int) -> None:
    # Paths are temp files we named ourselves, so they're safe to inline
    expr = f"pic2x('{src}', '{dst}', {k})"
    proc = await asyncio.create_subprocess_exec(
        OCTAVE, "--quiet", "--no-window-system", "--eval", expr,
        cwd=HERE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), OCTAVE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("Octave timed out")
    if proc.returncode != 0 or not dst.exists():
        # Octave 7 prints a harmless "ignoring const execution_exception" line on exit
        lines = [l for l in stderr.decode(errors="replace").splitlines() if "execution_exception" not in l]
        raise RuntimeError("\n".join(lines).strip()[-500:] or "Octave failed")


async def rotate_attachment(att: discord.Attachment, k: int) -> discord.File:
    suffix = Path(att.filename).suffix.lower()
    out_suffix = suffix if suffix in KEEP_FORMAT else ".png"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in{suffix or '.img'}"
        dst = Path(tmp) / f"out{out_suffix}"
        await att.save(src)
        if suffix == ".gif":
            # Octave's GIF reader scrambles small-palette GIFs, so hand it a PNG (first frame)
            png = Path(tmp) / "in.png"
            await asyncio.to_thread(lambda: Image.open(src).convert("RGBA").save(png))
            src = png
        async with octave_slots:
            await run_octave(src, dst, k)
        data = dst.read_bytes()
    name = f"{Path(att.filename).stem}_rotated{out_suffix}"
    return discord.File(io.BytesIO(data), filename=name)


@client.event
async def on_ready():
    log.info("Logged in as %s", client.user)
    if client.user.name != BOT_NAME:
        try:
            await client.user.edit(username=BOT_NAME)
            log.info("Renamed bot to %s", BOT_NAME)
        except discord.HTTPException as e:  # Discord rate-limits username changes
            log.warning("Couldn't rename bot to %s: %s", BOT_NAME, e)


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == client.user.id or payload.emoji.is_custom_emoji():
        return
    k = ROTATIONS.get(payload.emoji.name.replace("\ufe0f", ""))
    if k is None:
        return

    channel = client.get_channel(payload.channel_id) or await client.fetch_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    images = [
        a for a in message.attachments
        if (a.content_type or "").startswith("image/") and a.size <= MAX_BYTES
    ][:10]
    if not images:
        return

    files, errors = [], []
    async with channel.typing():
        for att in images:
            try:
                files.append(await rotate_attachment(att, k))
            except Exception as e:
                log.exception("Failed on %s", att.filename)
                errors.append(f"`{att.filename}`: {e}")

    text = "\n".join(f"Couldn't rotate {err}" for err in errors) or None
    await message.reply(content=text, files=files, mention_author=False)


if __name__ == "__main__":
    client.run(TOKEN, root_logger=True)  # also show our own log.info lines, not just discord.py's
