FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends octave fonts-inter fonts-dejavu-core python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py render.py markdown.py pic2x.m ./

RUN useradd --create-home bot
USER bot

CMD ["python", "bot.py"]
