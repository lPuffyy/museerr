FROM python:3.12-slim

# Install ffmpeg and system dependencies
RUN apt update && apt install -y ffmpeg git curl && apt clean && rm -rf /var/lib/apt/lists/*


WORKDIR /app
COPY ./app /app
# Install dependencies
# SpotDL 5.x is only on GitHub, so we install it directly from source
RUN pip install --no-cache-dir \
    fastapi uvicorn spotipy requests jinja2 python-multipart httpx ytmusicapi musicbrainzngs itsdangerous mutagen aiohttp yt-dlp \
    git+https://github.com/spotDL/spotify-downloader.git@master

# Pre-create download directory
RUN mkdir -p /downloads && chmod 777 /downloads

# Spotify credentials (replace with your real ones or inject later)
ENV SPOTIPY_CLIENT_ID="your_spotify_client_id"
ENV SPOTIPY_CLIENT_SECRET="your_spotify_client_secret"

EXPOSE 5001

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5001"]