"""Parse Discord-flavored markdown into blocks of styled runs, for render.py to draw."""

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, tzinfo

import emoji

TWEMOJI = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{}.png"
CUSTOM_EMOJI = "https://cdn.discordapp.com/emojis/{}.png?size=96"


@dataclass(frozen=True)
class Style:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False
    link: bool = False
    spoiler: int = 0                # spoiler group id, 0 = not a spoiler
    pill: tuple | None = None       # (kind, color, group id) for mentions/timestamps

    def but(self, **kw) -> "Style":
        return replace(self, **kw)


@dataclass
class Run:
    text: str
    style: Style
    emoji: str | None = None        # image URL when this run is an emoji; text is the fallback


@dataclass
class Block:
    kind: str                       # "p", "h1", "h2", "h3", "sub", "li", "code"
    runs: list[Run]
    quote: bool = False
    indent: int = 0                 # list nesting level
    marker: str = ""                # "•" or "1." for list items


@dataclass
class Context:
    """What the parser needs from Discord to resolve <@id>, <#id>, <t:...> etc."""
    users: dict[int, str] = field(default_factory=dict)
    roles: dict[int, tuple[str, tuple | None]] = field(default_factory=dict)
    channels: dict[int, str] = field(default_factory=dict)
    tz: tzinfo | None = None
    now: datetime | None = None


def twemoji_url(e: str) -> str:
    cps = [ord(c) for c in e]
    if 0x200D not in cps:  # Twemoji file names drop VS16 except in ZWJ sequences
        cps = [c for c in cps if c != 0xFE0F]
    return TWEMOJI.format("-".join(f"{c:x}" for c in cps))


def format_timestamp(ts: int, fmt: str, ctx: Context) -> str:
    dt = datetime.fromtimestamp(ts, ctx.tz)
    hm = f"{dt:%-I:%M %p}"
    if fmt == "R":
        now = ctx.now or datetime.now(ctx.tz)
        secs = (dt - now).total_seconds()
        for unit, size in (("year", 31536000), ("month", 2592000), ("day", 86400),
                           ("hour", 3600), ("minute", 60), ("second", 1)):
            n = int(abs(secs) // size)
            if n:
                label = f"{n} {unit}{'s' if n != 1 else ''}"
                return f"in {label}" if secs > 0 else f"{label} ago"
        return "now"
    return {
        "t": hm,
        "T": f"{dt:%-I:%M:%S %p}",
        "d": f"{dt:%m/%d/%Y}",
        "D": f"{dt:%B %-d, %Y}",
        "F": f"{dt:%A, %B %-d, %Y} {hm}",
    }.get(fmt, f"{dt:%B %-d, %Y} {hm}")  # "f" is the default


class Inline:
    """Inline rules, tried in order at each position (same precedence as Discord)."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.groups = 0  # ids for spoiler/pill groups so adjacent ones stay separate

    def _group(self) -> int:
        self.groups += 1
        return self.groups

    def parse(self, text: str, style: Style = Style()) -> list[Run]:
        runs, i = [], 0
        while i < len(text):
            for regex, handler in self.RULES:
                m = regex.match(text, i)
                if m and (handler is not Inline._underscore or i == 0 or not text[i - 1].isalnum()):
                    runs += handler(self, m, style)
                    i = m.end()
                    break
            else:  # unreachable: the last rule always matches
                i += 1
        return _merge(runs)

    def _escape(self, m, s):
        return [Run(m[1], s)]

    def _code(self, m, s):
        return [Run(m[2], s.but(code=True))]

    def _masked(self, m, s):
        return self.parse(m[1], s.but(link=True))

    def _url(self, m, s):
        return [Run(m[1] if m.lastindex else m[0], s.but(link=True))]

    def _custom_emoji(self, m, s):
        return [Run(f":{m[2]}:", s, CUSTOM_EMOJI.format(m[3]))]

    def _user(self, m, s):
        name = self.ctx.users.get(int(m[1]), "unknown-user")
        return [Run(f"@{name}", s.but(pill=("mention", None, self._group())))]

    def _role(self, m, s):
        name, color = self.ctx.roles.get(int(m[1]), ("deleted-role", None))
        return [Run(f"@{name}", s.but(pill=("role", color, self._group())))]

    def _channel(self, m, s):
        name = self.ctx.channels.get(int(m[1]), "unknown")
        return [Run(f"#{name}", s.but(pill=("mention", None, self._group())))]

    def _slash(self, m, s):
        return [Run(f"/{m[1]}", s.but(pill=("mention", None, self._group())))]

    def _everyone(self, m, s):
        return [Run(m[0], s.but(pill=("mention", None, self._group())))]

    def _timestamp(self, m, s):
        text = format_timestamp(int(m[1]), m[2] or "f", self.ctx)
        return [Run(text, s.but(pill=("time", None, self._group())))]

    def _bold(self, m, s):
        return self.parse(m[1], s.but(bold=True))

    def _underline(self, m, s):
        return self.parse(m[1], s.but(underline=True))

    def _italic(self, m, s):
        return self.parse(m[1], s.but(italic=True))

    def _underscore(self, m, s):
        return self.parse(m[1], s.but(italic=True))

    def _strike(self, m, s):
        return self.parse(m[1], s.but(strike=True))

    def _spoiler(self, m, s):
        return self.parse(m[1], s.but(spoiler=self._group()))

    def _text(self, m, s):
        return [Run(m[0], s)]

    RULES = [
        (re.compile(r"\\([^0-9A-Za-z\s])"), _escape),
        (re.compile(r"(`+)([\s\S]*?[^`])\1(?!`)"), _code),
        (re.compile(r"\[([^\[\]]+)\]\(<?(https?://[^\s)>]+)>?\)"), _masked),
        (re.compile(r"<(https?://[^\s>]+)>"), _url),
        (re.compile(r"https?://[^\s<]+[^<.,:;\"')\]\s]"), _url),
        (re.compile(r"<(a?):(\w+):(\d+)>"), _custom_emoji),
        (re.compile(r"<@!?(\d+)>"), _user),
        (re.compile(r"<@&(\d+)>"), _role),
        (re.compile(r"<#(\d+)>"), _channel),
        (re.compile(r"</([\w -]+):\d+>"), _slash),
        (re.compile(r"<t:(-?\d+)(?::([tTdDfFR]))?>"), _timestamp),
        (re.compile(r"@(?:everyone|here)\b"), _everyone),
        (re.compile(r"\*\*([\s\S]+?)\*\*(?!\*)"), _bold),
        (re.compile(r"__([\s\S]+?)__(?!_)"), _underline),
        (re.compile(r"\*(?=\S)([\s\S]*?\S)\*(?!\*)"), _italic),
        (re.compile(r"_((?:__|\\[\s\S]|[^\\_])+?)_(?![0-9A-Za-z_])"), _underscore),
        (re.compile(r"~~([\s\S]+?)~~"), _strike),
        (re.compile(r"\|\|([\s\S]+?)\|\|"), _spoiler),
        (re.compile(r"[^\\`\[<*_~|h@]+|[\s\S]"), _text),
    ]


def _merge(runs: list[Run]) -> list[Run]:
    """Join neighbouring plain runs with the same style, then split out unicode emoji."""
    merged: list[Run] = []
    for r in runs:
        if merged and not r.emoji and not merged[-1].emoji and merged[-1].style == r.style:
            merged[-1] = Run(merged[-1].text + r.text, r.style)
        else:
            merged.append(r)
    out = []
    for r in merged:
        if r.emoji:
            out.append(r)
            continue
        pos = 0
        for e in emoji.emoji_list(r.text):
            if e["match_start"] > pos:
                out.append(Run(r.text[pos:e["match_start"]], r.style))
            out.append(Run(e["emoji"], r.style, twemoji_url(e["emoji"])))
            pos = e["match_end"]
        if pos < len(r.text):
            out.append(Run(r.text[pos:], r.style))
    return out


FENCE = re.compile(r"```(?:([\w+#.-]+)\n)?\n*([\s\S]*?)\n*```")
HEADING = re.compile(r"(#{1,3}) +(\S.*)")
SUBTEXT = re.compile(r"-# +(\S.*)")
LIST_ITEM = re.compile(r"( *)([*-]|\d+\.) +(\S.*)")


def parse(content: str, ctx: Context) -> list[Block]:
    inline = Inline(ctx)
    blocks: list[Block] = []
    quote_rest = False
    pos = 0
    for m in FENCE.finditer(content):
        b, quote_rest = _lines(content[pos:m.start()], inline, quote_rest)
        blocks += b
        if m[2]:
            blocks.append(Block("code", [Run(m[2], Style(code=True))], quote=quote_rest))
        pos = m.end()
    blocks += _lines(content[pos:], inline, quote_rest)[0]
    return blocks


def _lines(text: str, inline: Inline, quote_rest: bool) -> tuple[list[Block], bool]:
    blocks = []
    text = text.strip("\n")
    if not text:
        return blocks, quote_rest
    for line in text.split("\n"):
        quote = quote_rest
        if not quote:
            if line.startswith(">>> ") or line == ">>>":
                quote = quote_rest = True
                line = line[4:]
            elif line.startswith("> "):
                quote, line = True, line[2:]
        if m := HEADING.fullmatch(line):
            blocks.append(Block(f"h{len(m[1])}", inline.parse(m[2]), quote))
        elif m := SUBTEXT.fullmatch(line):
            blocks.append(Block("sub", inline.parse(m[1]), quote))
        elif m := LIST_ITEM.fullmatch(line):
            marker = "•" if m[2] in "*-" else m[2]
            blocks.append(Block("li", inline.parse(m[3]), quote, len(m[1]) // 2, marker))
        else:
            blocks.append(Block("p", inline.parse(line), quote))
    return blocks, quote_rest
