"""Draw a Discord message as an image (light theme), for pic2x.m to rotate."""

import io
import re
import urllib.request
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps

from markdown import Block, Context, Style, parse

S = 2  # render at 2x so text stays crisp; layout constants below are in 1x (CSS) px
INTER = "/usr/share/fonts/opentype/inter/Inter-{}.otf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono{}.ttf"

# Discord light theme
BG = (255, 255, 255)
TEXT = (49, 51, 56)          # #313338
NAME = (6, 6, 7)             # #060607, also headings; used when the author has no role color
MUTED = (92, 94, 102)        # #5c5e66, timestamp and -# subtext
LINK = (0, 103, 224)
CODE_BG = (242, 243, 245)    # code block
CODE_BORDER = (227, 229, 232)
INLINE_CODE_BG = (235, 236, 238)
MENTION = ((80, 92, 220), (230, 232, 253))   # (text, background)
TIME_BG = (235, 236, 238)
SPOILER = (32, 34, 37)
QUOTE_BAR = (196, 201, 206)

PAD_X, PAD_Y = 16, 12
AVATAR, CONTENT_X, HEADER_H = 40, 72, 22
MAX_TEXT_W = 600
QUOTE_INDENT, QUOTE_BAR_W = 16, 4
LIST_INDENT = 22
CODE_PAD = 8
HEADING_GAP = 8              # space above a heading that isn't the first block
EMOJI_SCALE, EMOJI_MARGIN = 1.375, 0.05     # emoji are 1.375em, spaced 0.05em per side
JUMBO, JUMBO_LINE_H, JUMBO_MAX = 48, 54, 27  # emoji-only messages get big emoji
MAX_IMG_W, MAX_IMG_H, IMG_GAP, IMG_RADIUS = 550, 350, 8, 8

# block kind -> (font size, line height, color, bold)
BLOCK_STYLE = {
    "p": (16, 22, TEXT, False), "li": (16, 22, TEXT, False),
    "h1": (24, 33, NAME, True), "h2": (20, 28, NAME, True), "h3": (16, 22, NAME, True),
    "sub": (13, 18, MUTED, False), "code": (14, 18, TEXT, False),
}
TOKEN_RE = re.compile(r"\n| +|[^ \n]+")

_images: dict[str, Image.Image] = {}


@lru_cache(maxsize=None)
def font(path: str, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, round(size * S))


def inter(weight: str, size: float) -> ImageFont.FreeTypeFont:
    return font(INTER.format(weight), size)


def fetch_image(url: str) -> Image.Image | None:
    """Download an emoji image, caching successes only."""
    if url not in _images:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "pic2x"})
            with urllib.request.urlopen(req, timeout=5) as r:
                _images[url] = Image.open(io.BytesIO(r.read())).convert("RGBA")
        except Exception:
            return None
    return _images[url]


@dataclass
class Piece:
    kind: str                # "text", "space", "emoji", "nl"
    text: str
    style: Style
    font: ImageFont.FreeTypeFont | None
    w: float
    img: Image.Image | None = None
    x: float = 0.0


@dataclass
class Laid:
    """A block after layout, in device pixels."""
    block: Block
    x: float                 # left edge of the text, relative to the content column
    lines: list[tuple[list[Piece], float]]
    size: float
    line_h: float
    color: tuple
    gap: float               # space above
    emoji_px: float

    @property
    def pad(self) -> float:
        return CODE_PAD * S if self.block.kind == "code" else 0

    @property
    def height(self) -> float:
        return self.gap + len(self.lines) * self.line_h + 2 * self.pad

    @property
    def width(self) -> float:
        return self.x + max((w for _, w in self.lines), default=0) + 2 * self.pad


def piece_font(style: Style, block: Block, size: float) -> ImageFont.FreeTypeFont:
    bold = style.bold or BLOCK_STYLE[block.kind][3]
    if block.kind == "code":
        return font(MONO.format("-Bold" if bold else ""), size)
    if style.code:
        return font(MONO.format("-Bold" if bold else ""), size * 0.85)
    if style.pill:
        return inter("SemiBold" if bold else "Medium", size)
    return inter({(False, False): "Regular", (True, False): "Bold",
                  (False, True): "Italic", (True, True): "BoldItalic"}[bold, style.italic], size)


def pieces(block: Block, size: float, emoji_px: float) -> list[Piece]:
    out = []
    for run in block.runs:
        img = fetch_image(run.emoji) if run.emoji else None
        if img:
            out.append(Piece("emoji", run.text, run.style, None, emoji_px * (1 + 2 * EMOJI_MARGIN), img))
            continue
        fnt = piece_font(run.style, block, size)
        for t in TOKEN_RE.findall(run.text):
            kind = "nl" if t == "\n" else "space" if t[0] == " " else "text"
            out.append(Piece(kind, t, run.style, fnt, 0 if kind == "nl" else fnt.getlength(t)))
    return out


def layout(items: list[Piece], max_w: float) -> list[tuple[list[Piece], float]]:
    """Word wrap like Discord (pre-wrap, break-word). Text pieces with no space between
    them (e.g. **bo**ld) form one word and wrap together."""
    lines, line, x, soft = [], [], 0.0, False

    def wrap(soft_wrap: bool):
        nonlocal line, x, soft
        while line and line[-1].kind == "space":
            x -= line.pop().w
        lines.append((line, x))
        line, x, soft = [], 0.0, soft_wrap

    def place(p: Piece):
        nonlocal x
        p.x = x
        line.append(p)
        x += p.w

    i = 0
    while i < len(items):
        p = items[i]
        if p.kind == "nl":
            wrap(False)
            i += 1
            continue
        if p.kind == "space":
            if line or not soft:   # a soft wrap swallows the space; real line starts keep it
                if x + p.w > max_w and line:
                    wrap(True)
                else:
                    place(p)
            i += 1
            continue
        word = [p]
        if p.kind == "text":
            while i + len(word) < len(items) and items[i + len(word)].kind == "text":
                word.append(items[i + len(word)])
        i += len(word)
        w = sum(q.w for q in word)
        if x + w > max_w and line:
            wrap(True)
        if w <= max_w:
            for q in word:
                place(q)
            continue
        for q in word:  # one word wider than the line: break it by characters
            for ch in q.text:
                cw = q.font.getlength(ch)
                if x + cw > max_w and line:
                    wrap(True)
                place(Piece("text", ch, q.style, q.font, cw))
    wrap(False)
    return lines


def lay_out_blocks(blocks: list[Block]) -> list[Laid]:
    runs = [r for b in blocks for r in b.runs]
    n_emoji = sum(1 for r in runs if r.emoji)
    jumbo = (0 < n_emoji <= JUMBO_MAX and all(b.kind == "p" and not b.quote for b in blocks)
             and all((r.emoji or not r.text.strip()) and r.style == Style() for r in runs))
    laid = []
    for i, b in enumerate(blocks):
        size, line_h, color, _ = BLOCK_STYLE[b.kind]
        emoji_px = JUMBO if jumbo else size * EMOJI_SCALE
        if jumbo:
            line_h = JUMBO_LINE_H
        x = (QUOTE_INDENT if b.quote else 0) + (LIST_INDENT * (b.indent + 1) if b.kind == "li" else 0)
        max_w = MAX_TEXT_W - x - (2 * CODE_PAD if b.kind == "code" else 0)
        gap = HEADING_GAP if b.kind in ("h1", "h2", "h3") and i else 4 if b.kind == "code" else 0
        lines = layout(pieces(b, size, emoji_px * S), max_w * S)
        laid.append(Laid(b, x * S, lines, size * S, line_h * S, color, gap * S, emoji_px * S))
    return laid


def circle(img: Image.Image, size: int) -> Image.Image:
    img = ImageOps.fit(img.convert("RGBA"), (size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    img.putalpha(mask.resize((size, size), Image.LANCZOS))
    return img


def rounded(img: Image.Image, radius: int) -> Image.Image:
    img = img.convert("RGBA")
    mask = Image.new("L", (img.width * 2, img.height * 2), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width * 2 - 1, img.height * 2 - 1), radius * 2, fill=255)
    alpha = Image.composite(img.getchannel("A"), Image.new("L", img.size, 0), mask.resize(img.size, Image.LANCZOS))
    img.putalpha(alpha)
    return img


def tint(color: tuple, alpha: float) -> tuple:
    return tuple(round(c * alpha + 255 * (1 - alpha)) for c in color)


def groups(line: list[Piece], key) -> list[tuple[float, float, Piece]]:
    """Spans of consecutive pieces sharing a non-None key -> (x0, x1, first piece)."""
    out, cur = [], None
    for p in line:
        k = key(p)
        if k is not None and cur and cur[0] == k:
            cur[2] = p.x + p.w
            continue
        if cur:
            out.append((cur[1], cur[2], cur[3]))
        cur = [k, p.x, p.x + p.w, p] if k is not None else None
    if cur:
        out.append((cur[1], cur[2], cur[3]))
    return out


def baseline(fnt: ImageFont.FreeTypeFont, top: float, line_h: float) -> float:
    ascent, descent = fnt.getmetrics()
    return top + (line_h - ascent - descent) / 2 + ascent


def draw_line(card: Image.Image, draw: ImageDraw.ImageDraw, laid: Laid, line: list[Piece], cx: float, top: float):
    base = baseline(piece_font(Style(), laid.block, laid.size / S), top, laid.line_h)
    half, mid = laid.size * 0.62, base - laid.size * 0.35

    # backgrounds: inline code and pills (mentions, roles, timestamps)
    if laid.block.kind != "code":
        for x0, x1, _ in groups(line, lambda p: "code" if p.style.code else None):
            draw.rounded_rectangle((cx + x0 - S, mid - half, cx + x1 + S, mid + half), 3 * S, fill=INLINE_CODE_BG)
    for x0, x1, p in groups(line, lambda p: p.style.pill[2] if p.style.pill else None):
        kind, color, _ = p.style.pill
        bg = TIME_BG if kind == "time" else tint(color, 0.12) if color else MENTION[1]
        draw.rounded_rectangle((cx + x0 - 2 * S, mid - half, cx + x1 + 2 * S, mid + half), 3 * S, fill=bg)

    for p in line:
        x, s = cx + p.x, p.style
        if p.kind == "emoji":
            px = round(laid.emoji_px)
            im = p.img.resize((px, px), Image.LANCZOS)
            card.paste(im, (round(x + laid.emoji_px * EMOJI_MARGIN), round(top + (laid.line_h - px) / 2)), im)
            continue
        if s.pill:
            kind, color, _ = s.pill
            fill = TEXT if kind == "time" else color or MENTION[0]
        else:
            fill = LINK if s.link else laid.color
        if p.kind == "text":
            draw.text((x, base), p.text, font=p.font, fill=fill, anchor="ls")
        if s.underline:
            draw.line((x, base + 2 * S, x + p.w, base + 2 * S), fill=fill, width=S)
        if s.strike:
            draw.line((x, mid, x + p.w, mid), fill=fill, width=S)

    # spoilers go on top so they hide what's under them
    for x0, x1, _ in groups(line, lambda p: p.style.spoiler or None):
        draw.rounded_rectangle((cx + x0 - S, mid - half, cx + x1 + S, mid + half), 3 * S, fill=SPOILER)


def render_message(author: str, name_color: tuple | None, avatar: bytes | None,
                   timestamp: str, content: str, images: list[bytes],
                   ctx: Context | None = None) -> Image.Image:
    laid = lay_out_blocks(parse(content, ctx or Context()))

    pics = []
    for data in images:
        im = Image.open(io.BytesIO(data))  # already upright: bot.py runs it through pic2x.m first
        scale = min(1, MAX_IMG_W / im.width, MAX_IMG_H / im.height) * S
        size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
        pics.append(rounded(im.convert("RGBA").resize(size, Image.LANCZOS), IMG_RADIUS * S))

    name_font, time_font = inter("SemiBold", 16), inter("Medium", 12)
    name_w = name_font.getlength(author)
    header_w = name_w + 8 * S + time_font.getlength(timestamp)
    content_w = max([header_w] + [l.width for l in laid] + [p.width for p in pics])
    width = round(CONTENT_X * S + content_w + PAD_X * S)
    height = (PAD_Y + HEADER_H) * S + sum(l.height for l in laid) + PAD_Y * S
    height += sum(IMG_GAP * S + p.height for p in pics)

    card = Image.new("RGB", (width, round(height)), BG)
    draw = ImageDraw.Draw(card)

    av = Image.open(io.BytesIO(avatar)) if avatar else Image.new("RGB", (64, 64), (88, 101, 242))
    av = circle(av, AVATAR * S)
    card.paste(av, (PAD_X * S, PAD_Y * S), av)

    cx, top = CONTENT_X * S, PAD_Y * S
    base = baseline(name_font, top, HEADER_H * S)
    draw.text((cx, base), author, font=name_font, fill=name_color or NAME, anchor="ls")
    draw.text((cx + name_w + 8 * S, base), timestamp, font=time_font, fill=MUTED, anchor="ls")
    top += HEADER_H * S

    # one continuous bar per run of consecutive quoted blocks
    y = top
    for i, l in enumerate(laid):
        if l.block.quote and (i == 0 or not laid[i - 1].block.quote):
            start = y + l.gap
        if l.block.quote and (i + 1 == len(laid) or not laid[i + 1].block.quote):
            draw.rounded_rectangle((cx, start, cx + QUOTE_BAR_W * S - 1, y + l.height - 1), 2 * S, fill=QUOTE_BAR)
        y += l.height

    for l in laid:
        b = l.block
        top += l.gap
        x = cx + l.x
        if b.kind == "code":
            draw.rounded_rectangle((x, top, cx + content_w, top + l.height - l.gap), 4 * S,
                                   fill=CODE_BG, outline=CODE_BORDER, width=S)
        x, top = x + l.pad, top + l.pad
        if b.kind == "li":
            mbase = baseline(piece_font(Style(), b, l.size / S), top, l.line_h)
            draw.text((x - 6 * S, mbase), b.marker, font=piece_font(Style(), b, l.size / S), fill=TEXT, anchor="rs")
        for line, _ in l.lines:
            draw_line(card, draw, l, line, x, top)
            top += l.line_h
        top += l.pad

    for p in pics:
        top += IMG_GAP * S
        card.paste(p, (cx, round(top)), p)
        top += p.height
    return card
