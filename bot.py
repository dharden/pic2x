"""pic2x': a Discord bot. React to an image with 🔃 or 🔄 and it gets rotated by Octave."""

import asyncio
import io
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from PIL import Image

from markdown import Context
from render import render_message

TOKEN = os.environ["DISCORD_TOKEN"]
BOT_NAME = os.environ.get("BOT_NAME", "pic2x'")
TIMEZONE = ZoneInfo(os.environ.get("BOT_TIMEZONE", "America/New_York"))  # for card timestamps
MAX_CHARS = 4000  # Discord's longest (Nitro) message
OCTAVE = os.environ.get("OCTAVE_BIN", "octave-cli")
OCTAVE_TIMEOUT = 60  # seconds per image
MAX_BYTES = 20 * 1024 * 1024
HERE = Path(__file__).resolve().parent

# Reaction emoji -> k for rot90 (positive = counterclockwise)
ROTATIONS = {"🔃": -1, "🔄": 1}  # 🔃 :arrows_clockwise:, 🔄 :arrows_counterclockwise:
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


async def rotate_bytes(data: bytes, suffix: str, k: int) -> tuple[bytes, str]:
    """Run image bytes through pic2x.m. k=0 just applies the photo's EXIF orientation."""
    out_suffix = suffix if suffix in KEEP_FORMAT else ".png"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in{suffix or '.img'}"
        dst = Path(tmp) / f"out{out_suffix}"
        src.write_bytes(data)
        if suffix == ".gif":
            # Octave's GIF reader scrambles small-palette GIFs, so hand it a PNG (first frame)
            png = Path(tmp) / "in.png"
            await asyncio.to_thread(lambda: Image.open(src).convert("RGBA").save(png))
            src = png
        async with octave_slots:
            await run_octave(src, dst, k)
        return dst.read_bytes(), out_suffix


async def rotate_attachment(att: discord.Attachment, k: int) -> discord.File:
    data, out_suffix = await rotate_bytes(await att.read(), Path(att.filename).suffix.lower(), k)
    name = f"{Path(att.filename).stem}_rotated{out_suffix}"
    return discord.File(io.BytesIO(data), filename=name)


def card_timestamp(created: datetime) -> str:
    local, now = created.astimezone(TIMEZONE), datetime.now(TIMEZONE)
    if local.date() == now.date():
        return local.strftime("%-I:%M %p")
    return local.strftime("%-m/%-d/%y, %-I:%M %p")


async def rotate_message(message: discord.Message, images: list[discord.Attachment], k: int) -> discord.File:
    """Draw the message (text + images) as a Discord-style card and rotate that."""
    author = message.author
    color = author.color.to_rgb() if author.color.value else None
    try:
        avatar = await author.display_avatar.with_format("png").with_size(128).read()
    except discord.HTTPException:
        avatar = None
    # upright each image the way Discord displays it (EXIF orientation) -- also via pic2x.m
    pics = [(await rotate_bytes(await a.read(), Path(a.filename).suffix.lower(), 0))[0] for a in images]
    ctx = Context(
        users={u.id: u.display_name for u in message.mentions},
        roles={r.id: (r.name, r.color.to_rgb() if r.color.value else None) for r in message.role_mentions},
        channels={c.id: c.name for c in message.channel_mentions},
        tz=TIMEZONE,
    )
    card = await asyncio.to_thread(
        render_message, author.display_name, color, avatar,
        card_timestamp(message.created_at), message.content.strip()[:MAX_CHARS], pics, ctx,
    )
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.png", Path(tmp) / "out.png"
        await asyncio.to_thread(card.save, src)
        async with octave_slots:
            await run_octave(src, dst, k)
        data = dst.read_bytes()
    return discord.File(io.BytesIO(data), filename="message_rotated.png")


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
    has_text = bool(message.content.strip())
    if not images and not has_text:
        return

    files, errors = [], []
    async with channel.typing():
        if has_text:  # text (+ any images) -> one rotated Discord-style card
            try:
                files.append(await rotate_message(message, images, k))
            except Exception as e:
                log.exception("Failed on message %s", message.id)
                errors.append(f"the message: {e}")
        else:  # images only -> rotate each at full resolution
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
