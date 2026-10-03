"""pic2x': a Discord bot. React to an image with 🔃 or 🔄 and it gets rotated by Octave."""

import asyncio
import io
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp
import discord
from PIL import Image

import animate
from markdown import Context
from render import render_message

TOKEN = os.environ["DISCORD_TOKEN"]
BOT_NAME = os.environ.get("BOT_NAME", "pic2x'")
TIMEZONE = ZoneInfo(os.environ.get("BOT_TIMEZONE", "America/New_York"))  # for card timestamps
MAX_CHARS = 4000  # Discord's longest (Nitro) message
OCTAVE = os.environ.get("OCTAVE_BIN", "octave-cli")
OCTAVE_TIMEOUT = 60  # seconds per image
MAX_BYTES = 20 * 1024 * 1024
MB = 1024 * 1024
UPLOAD_LIMITS = {2: 50 * MB, 3: 100 * MB}  # by server boost level; otherwise 10 MB
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
    await octave(f"pic2x('{src}', '{dst}', {k})", OCTAVE_TIMEOUT)
    if not dst.exists():
        raise RuntimeError("Octave didn't write an image")


async def run_octave_frames(folder: Path, n: int, k: int) -> None:
    """Rotate in_0001.png..in_NNNN.png -> out_*.png with pic2x.m, in one Octave process."""
    await octave(f"d = '{folder}'; for i = 1:{n}, "
                 f"pic2x(sprintf('%s/in_%04d.png', d, i), sprintf('%s/out_%04d.png', d, i), {k}); end",
                 OCTAVE_TIMEOUT + n)
    if not (folder / f"out_{n:04d}.png").exists():
        raise RuntimeError("Octave didn't write all the frames")


async def octave(expr: str, timeout: float) -> None:
    proc = await asyncio.create_subprocess_exec(
        OCTAVE, "--quiet", "--no-window-system", "--eval", expr,
        cwd=HERE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("Octave timed out")
    if proc.returncode != 0:
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
        if suffix in (".gif", ".webp"):
            # Octave's GIF reader scrambles small-palette GIFs, and either can be animated,
            # so hand Octave a PNG of the first frame
            png = Path(tmp) / "in.png"
            await asyncio.to_thread(lambda: Image.open(src).convert("RGBA").save(png))
            src = png
        async with octave_slots:
            await run_octave(src, dst, k)
        return dst.read_bytes(), out_suffix


@dataclass
class Source:
    """An image in a message: an uploaded attachment or an image/GIF link embed."""
    filename: str
    read: Callable[[], Awaitable[bytes]]                      # the image (a still frame for GIF links)
    read_video: Callable[[], Awaitable[bytes]] | None = None  # GIF links: the MP4 they really are

    @property
    def suffix(self) -> str:
        return Path(self.filename).suffix.lower()


async def download(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
            r.raise_for_status()
            data = bytearray()
            async for chunk in r.content.iter_chunked(64 * 1024):
                data += chunk
                if len(data) > MAX_BYTES:
                    raise RuntimeError("image is too large")
    return bytes(data)


def image_sources(message: discord.Message) -> list[Source]:
    """Images in the order Discord shows them: attachments, then image/GIF embeds."""
    sources = [Source(a.filename, a.read) for a in message.attachments
               if (a.content_type or "").startswith("image/") and a.size <= MAX_BYTES]
    for e in media_embeds(message):
        url = e.thumbnail.proxy_url or e.thumbnail.url
        name = Path(urlparse(e.thumbnail.url or url).path).name or "image.png"
        video = (e.video.proxy_url or e.video.url) if e.type == "gifv" else None
        sources.append(Source(name, lambda url=url: download(url),
                              (lambda video=video: download(video)) if video else None))
    return sources[:10]


def media_embeds(message: discord.Message) -> list[discord.Embed]:
    # "image" = a direct image link, "gifv" = Tenor/Klipy/Giphy etc. (thumbnail is a still frame)
    return [e for e in message.embeds if e.type in ("image", "gifv") and (e.thumbnail.proxy_url or e.thumbnail.url)]


def visible_text(message: discord.Message) -> str:
    """The message text as Discord shows it: a message that's only image/GIF links
    shows just the images, not the links."""
    text = message.content.strip()
    if text and set(text.split()) <= {e.url for e in media_embeds(message)}:
        return ""
    return text


async def rotate_source(src: Source, k: int, upload_limit: int) -> discord.File:
    stem = Path(src.filename).stem
    if src.read_video:
        data = await rotate_animation(await src.read_video(), True, k, upload_limit)
        return discord.File(io.BytesIO(data), filename=f"{stem}_rotated.gif")
    raw = await src.read()
    if await asyncio.to_thread(animate.is_animated, raw):
        data = await rotate_animation(raw, False, k, upload_limit)
        return discord.File(io.BytesIO(data), filename=f"{stem}_rotated.gif")
    data, out_suffix = await rotate_bytes(raw, src.suffix, k)
    return discord.File(io.BytesIO(data), filename=f"{stem}_rotated{out_suffix}")


async def rotate_animation(data: bytes, is_video: bool, k: int, upload_limit: int) -> bytes:
    """Split into frames, rotate every frame with pic2x.m, and rebuild as a GIF."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        if is_video:
            video = folder / "in.mp4"
            video.write_bytes(data)
            delays, loop = await animate.video_frames(video, folder)
        else:
            delays, loop = await asyncio.to_thread(animate.image_frames, data, folder)
        async with octave_slots:
            await run_octave_frames(folder, len(delays), k)
        return await asyncio.to_thread(animate.make_gif, folder, delays, loop, upload_limit)


def upload_limit(guild: discord.Guild | None) -> int:
    limit = UPLOAD_LIMITS.get(guild.premium_tier, 10 * MB) if guild else 10 * MB
    return int(limit * 0.95)  # leave room for the rest of the upload


def card_timestamp(created: datetime) -> str:
    local, now = created.astimezone(TIMEZONE), datetime.now(TIMEZONE)
    if local.date() == now.date():
        return local.strftime("%-I:%M %p")
    return local.strftime("%-m/%-d/%y, %-I:%M %p")


async def as_member(guild: discord.Guild | None, user: discord.abc.User) -> discord.abc.User:
    """Fetched messages carry plain users (no nickname/roles) unless the member is cached,
    and without the privileged members intent it usually isn't, so look them up."""
    if guild is None or isinstance(user, discord.Member):
        return user
    try:
        return guild.get_member(user.id) or await guild.fetch_member(user.id)
    except discord.HTTPException:  # e.g. they've left the server
        return user


async def rotate_message(message: discord.Message, text: str, images: list[Source], k: int) -> discord.File:
    """Draw the message (text + images) as a Discord-style card and rotate that."""
    author = await as_member(message.guild, message.author)
    mentions = [await as_member(message.guild, u) for u in message.mentions[:25]]
    color = author.color.to_rgb() if author.color.value else None
    try:
        avatar = await author.display_avatar.with_format("png").with_size(128).read()
    except discord.HTTPException:
        avatar = None
    # upright each image the way Discord displays it (EXIF orientation) -- also via pic2x.m
    pics = [(await rotate_bytes(await s.read(), s.suffix, 0))[0] for s in images]
    ctx = Context(
        users={u.id: u.display_name for u in mentions},
        roles={r.id: (r.name, r.color.to_rgb() if r.color.value else None) for r in message.role_mentions},
        channels={c.id: c.name for c in message.channel_mentions},
        tz=TIMEZONE,
    )
    card = await asyncio.to_thread(
        render_message, author.display_name, color, avatar,
        card_timestamp(message.created_at), text[:MAX_CHARS], pics, ctx,
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
    images = image_sources(message)
    text = visible_text(message)
    if not images and not text:
        return

    files, errors = [], []
    async with channel.typing():
        if text:  # text (+ any images) -> one rotated Discord-style card
            try:
                files.append(await rotate_message(message, text, images, k))
            except Exception as e:
                log.exception("Failed on message %s", message.id)
                errors.append(f"the message: {e}")
        else:  # images only -> rotate each at full resolution
            for src in images:
                try:
                    files.append(await rotate_source(src, k, upload_limit(message.guild)))
                except Exception as e:
                    log.exception("Failed on %s", src.filename)
                    errors.append(f"`{src.filename}`: {e}")

    text = "\n".join(f"Couldn't rotate {err}" for err in errors) or None
    await message.reply(content=text, files=files, mention_author=False)


if __name__ == "__main__":
    client.run(TOKEN, root_logger=True)  # also show our own log.info lines, not just discord.py's
