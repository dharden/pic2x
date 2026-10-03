# pic2x'

React to a message and the bot replies with it rotated 90°. Every rotation is done by
`pic2x.m`, running in GNU Octave.

| React | Result |
|---|---|
| 🔃 `:arrows_clockwise:` | 90° clockwise |
| 🔄 `:arrows_counterclockwise:` | 90° counterclockwise |

- **Image-only messages:** each image is rotated at full resolution. Sideways phone
  photos are handled using their EXIF orientation. Image and GIF links (Tenor, Klipy,
  Giphy...) count as images, since Discord shows them that way.
- **Animated GIFs** (uploads and GIF links) come back as animated GIFs: `animate.py`
  splits them into frames (Pillow, or ffmpeg for the MP4s behind GIF links), `pic2x.m`
  rotates every frame, and Pillow rebuilds the GIF, shrinking it to fit the server's
  upload limit.
- **Messages with text:** the message is drawn as a Discord-style card (light theme,
  avatar, role color, markdown, emoji, mentions, any attached images) by `render.py`
  and `markdown.py`, and the card is rotated. Card timestamps use `BOT_TIMEZONE`
  (default `America/New_York`).

## 1. Create the Discord bot

1. https://discord.com/developers/applications → **New Application**, named `pic2x'`.
   (The bot also renames its own user to `pic2x'`, or `BOT_NAME` if set, on startup.)
2. **Bot** tab → **Reset Token** → copy the token (this is `DISCORD_TOKEN`).
3. Same tab → turn on **Message Content Intent** (needed to see attachments).
4. **OAuth2 → URL Generator**: scope `bot`; permissions *View Channels*, *Send Messages*,
   *Attach Files*, *Read Message History*. Open the URL to invite it to your server.

## 2. Run it (on any server with Docker)

```bash
docker build -t pic2x .
docker run -d --restart unless-stopped --name pic2x -e DISCORD_TOKEN=... pic2x
```

The bot only makes outbound connections, so no ports need to be opened. Any VPS or a
Docker host like Railway, Fly.io, or Render works.

## Swapping in a different function

`bot.py` calls `pic2x(in_path, out_path, k)` via `octave-cli --eval`. To run another
function, change `run_octave()` in `bot.py` and the `ROTATIONS` emoji map.
