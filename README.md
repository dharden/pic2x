# pic2x'

React to any message with an image and the bot replies with the image rotated 90°.
The rotation is done by `rotate_image.m`, running in GNU Octave.

| React | Result |
|---|---|
| ↪️ | 90° clockwise |
| ↩️ | 90° counterclockwise |

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

`bot.py` calls `rotate_image(in_path, out_path, k)` via `octave-cli --eval`. To run another
function, change `run_octave()` in `bot.py` and the `ROTATIONS` emoji map.
