import os, time, hmac, hashlib, random, asyncio, re
from typing import Dict, Any, Optional, List

import requests
from urllib.parse import quote

from fastapi import FastAPI, Request, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    HTMLResponse, RedirectResponse, StreamingResponse, FileResponse, JSONResponse
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

# =========================
# CONFIG & ENV
# =========================

LIDARR_URL_RAW = os.getenv("LIDARR_URL", "http://192.168.5.47:8686").rstrip("/")
LIDARR_API_KEY = os.getenv("LIDARR_API_KEY", "")
LIDARR_ROOT_FOLDER = os.getenv("LIDARR_ROOT_FOLDER", "/music")

LIDARR_QUALITY_PROFILE = os.getenv("LIDARR_QUALITY_PROFILE", "").strip()
LIDARR_METADATA_PROFILE = os.getenv("LIDARR_METADATA_PROFILE", "").strip()

NAVIDROME_URL = os.getenv("NAVIDROME_URL", "http://192.168.5.47:4533").rstrip("/")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

if LIDARR_URL_RAW.endswith("/api/v1"):
    LIDARR_API_BASE = LIDARR_URL_RAW
else:
    LIDARR_API_BASE = f"{LIDARR_URL_RAW}/api/v1"

app = FastAPI(title="Museerr · Lidarr-only")

# =========================
# STATIC & TEMPLATE SETUP
# =========================
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/icons", StaticFiles(directory=os.path.join("static", "icons")), name="icons")
templates = Jinja2Templates(directory="templates")

@app.get("/style.css")
def style(): 
    return FileResponse(os.path.join("static", "style.css"))

@app.get("/app.js")
def js(): 
    return FileResponse(os.path.join("static", "app.js"))

@app.get("/manifest.webmanifest")
def manifest(): 
    return FileResponse(os.path.join("static", "manifest.webmanifest"))

@app.get("/service-worker.js")
def sw(): 
    return FileResponse(os.path.join("static", "service-worker.js"))

# =========================
# SIMPLE AUTH
# =========================
_sessions: Dict[str, Dict[str, Any]] = {}

def _sign(v: str) -> str:
    return hmac.new(SECRET_KEY.encode(), v.encode(), hashlib.sha256).hexdigest()

def _mk_session(u: str, p: str) -> str:
    sid = hashlib.sha1(f"{u}:{p}:{time.time()}".encode()).hexdigest()
    _sessions[sid] = {"u": u, "p": p, "ts": time.time()}
    return f"{sid}.{_sign(sid)}"

def _get_session_cookie(v: Optional[str]):
    if not v or "." not in v:
        return None
    sid, sig = v.split(".", 1)
    if _sign(sid) == sig:
        return _sessions.get(sid)
    return None

def require_auth(request: Request):
    s = request.cookies.get("session")
    d = _get_session_cookie(s)
    if not d:
        raise HTTPException(status_code=302, detail="Login required")
    return d

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if any(path.startswith(x) for x in [
            "/login", "/token", "/static", "/icons",
            "/manifest.webmanifest", "/style.css", "/app.js", "/service-worker.js"
        ]):
            return await call_next(request)
        try:
            require_auth(request)
        except HTTPException:
            return RedirectResponse("/login")
        return await call_next(request)

app.add_middleware(AuthMiddleware)

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/token")
def do_login(username: str = Form(...), password: str = Form(...)):
    # Best-effort ping to Navidrome
    try:
        requests.get(
            f"{NAVIDROME_URL}/rest/ping.view",
            params={"u": username, "p": password, "v": "1.13.0", "c": "museerr", "f": "json"},
            timeout=5
        )
    except Exception:
        pass
    cookie = _mk_session(username, password)
    r = RedirectResponse("/", status_code=302)
    r.set_cookie("session", cookie, httponly=True, samesite="lax")
    return r

@app.get("/logout")
def logout(request: Request):
    s = request.cookies.get("session")
    if s and "." in s:
        sid, _ = s.split(".", 1)
        _sessions.pop(sid, None)
    return RedirectResponse("/login", status_code=302)

# =========================
# LIDARR HELPERS
# =========================
def lidarr_headers(): 
    return {"X-Api-Key": LIDARR_API_KEY} if LIDARR_API_KEY else {}

def lidarr_get(path: str, params: Optional[dict] = None):
    return requests.get(f"{LIDARR_API_BASE.rstrip('/')}{path}", headers=lidarr_headers(), params=params or {}, timeout=20)

def lidarr_post(path: str, payload: dict):
    return requests.post(f"{LIDARR_API_BASE.rstrip('/')}{path}", headers={**lidarr_headers(), "Content-Type": "application/json"}, json=payload, timeout=30)

# Cache for artist fallback images
ARTIST_IMAGE_CACHE: Dict[str, str] = {}
_profile_cache: Dict[str, int] = {}

def _pick_profile_id(profile_type: str, preferred_name: str = "") -> Optional[int]:
    key = f"{profile_type}:{preferred_name}"
    if key in _profile_cache:
        return _profile_cache[key]
    try:
        if profile_type == "quality":
            r = lidarr_get("/qualityprofile")
        else:
            r = lidarr_get("/metadataprofile")
        if r.status_code == 200:
            items = r.json() or []
            if preferred_name:
                for it in items:
                    if it.get("name", "").lower() == preferred_name.lower():
                        _profile_cache[key] = it["id"]
                        return it["id"]
            if items:
                _profile_cache[key] = items[0]["id"]
                return items[0]["id"]
    except Exception as e:
        print("profile pick error:", e)
    return None

# =========================
# ROUTES
# =========================

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/discover/random")
def discover_random():
    try:
        r = lidarr_get("/artist")
        if r.status_code == 200:
            data = r.json()
            artists = []
            for x in data:
                name = x.get("artistName") or (x.get("artistMetadata") or {}).get("name")
                if not name:
                    continue
                artists.append({
                    "id": x.get("foreignArtistId") or x.get("id"),
                    "name": name,
                    "image_url": f"/artist/image?name={name.replace(' ', '+')}"
                })
            random.shuffle(artists)
            return {"artists": artists[:18]}
    except Exception as e:
        print("discover_random error:", e)
    return {"artists": []}

@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: Optional[str] = None):
    results = []
    if q:
        seen = set()
        lib_resp = lidarr_get("/artist")
        library = lib_resp.json() if lib_resp.status_code == 200 else []
        lib_names = {((a.get("artistName") or (a.get("artistMetadata") or {}).get("name") or "").lower()) for a in library}
        lib_ids = {str(a.get("id")) for a in library}
        lib_mbids = {str(a.get("foreignArtistId")) for a in library if a.get("foreignArtistId")}

        local_resp = lidarr_get("/artist/lookup", params={"term": q})
        remote_resp = lidarr_get("/search", params={"term": q, "type": "artist"})
        local = local_resp.json() if local_resp.status_code == 200 else []
        remote = remote_resp.json() if remote_resp.status_code == 200 else []

        merged = (local or []) + (remote or [])
        for a in merged:
            name = (a.get("artistName") or (a.get("artistMetadata") or {}).get("name") or "").strip()
            if not name:
                continue
            id_ = a.get("id") or a.get("foreignArtistId") or name
            if str(id_).lower() in seen:
                continue
            seen.add(str(id_).lower())
            in_lib = str(id_) in lib_ids or str(id_) in lib_mbids or name.lower() in lib_names
            results.append({
                "id": id_, "name": name,
                "image_url": f"/artist/image?name={quote(name)}",
                "in_library": in_lib
            })
    return templates.TemplateResponse("search.html", {"request": request, "query": q or "", "results": results})

# =========================
# IMAGE HANDLERS
# =========================
@app.get("/config/MediaCover/{artist_id}/{filename}")
def legacy_media_cover_proxy(artist_id: str, filename: str):
    try:
        for f in [filename, "poster-500.jpg", "poster.jpg"]:
            url = f"{LIDARR_API_BASE}/mediacover/artist/{artist_id}/{f}"
            r = requests.get(url, headers=lidarr_headers(), stream=True, timeout=8)
            if r.status_code == 200 and r.headers.get("content-type","").startswith("image/"):
                return StreamingResponse(r.raw, media_type=r.headers["content-type"])
    except Exception as e:
        print("legacy_media_cover_proxy error:", e)
    return FileResponse(os.path.join("static/icons", "icon-192.png"))

@app.get("/artist/image")
def artist_image(request: Request):
    name = request.query_params.get("name")
    artist_id = request.query_params.get("id")
    try:
        if not artist_id and name:
            look = lidarr_get("/artist/lookup", params={"term": name}).json()
            if look:
                artist_id = look[0].get("id") or look[0].get("foreignArtistId")

        if artist_id:
            aid = str(artist_id)
            if aid in ARTIST_IMAGE_CACHE:
                from fastapi.responses import RedirectResponse
                return RedirectResponse(ARTIST_IMAGE_CACHE[aid])

            for f in ["poster-500.jpg", "poster.jpg"]:
                url = f"{LIDARR_API_BASE}/mediacover/artist/{aid}/{f}"
                r = requests.get(url, headers=lidarr_headers(), stream=True, timeout=8)
                if r.status_code == 200 and r.headers.get("content-type","").startswith("image/"):
                    return StreamingResponse(r.raw, media_type=r.headers["content-type"])

            local_id = aid
            if not str(aid).isdigit():
                lr = lidarr_get("/artist/lookup", params={"term": f"mbid:{aid}"})
                if lr.status_code == 200 and lr.json():
                    cand = lr.json()[0]
                    if cand.get("id"):
                        local_id = str(cand.get("id"))

            ar = lidarr_get("/album", params={"artistId": local_id})
            if ar.status_code == 200:
                albums = ar.json() or []
                for alb in albums:
                    cover = alb.get("remoteCover")
                    if not cover and alb.get("images"):
                        for i in alb["images"]:
                            if i.get("coverType") == "cover" and i.get("remoteUrl"):
                                cover = i["remoteUrl"]
                                break
                    if cover:
                        from fastapi.responses import RedirectResponse
                        ARTIST_IMAGE_CACHE[aid] = cover
                        return RedirectResponse(cover)
    except Exception as e:
        print("artist_image error:", e)
    return FileResponse(os.path.join("static/icons", "icon-192.png"))

# =========================
# ARTIST DETAIL
# =========================
@app.get("/artist/{artist_id}", response_class=HTMLResponse)
def artist_detail(request: Request, artist_id: str):
    in_library = False
    artist = None
    albums = []

    def is_uuid(v: str): return re.match(r"^[0-9a-fA-F-]{36}$", v or "")

    try:
        r = lidarr_get(f"/artist/{artist_id}")
        if r.status_code == 200:
            artist = r.json()
            in_library = True
        elif is_uuid(artist_id):
            lr = lidarr_get("/artist/lookup", params={"term": f"mbid:{artist_id}"})
            if lr.status_code == 200 and lr.json():
                artist = lr.json()[0]
            else:
                sr = lidarr_get("/search", params={"term": artist_id, "type": "artist"})
                if sr.status_code == 200 and sr.json():
                    artist = sr.json()[0]

        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found")

        # Double-check library membership
        try:
            all_artists = lidarr_get("/artist")
            if all_artists.status_code == 200:
                for a in all_artists.json():
                    if (
                        str(a.get("id")) == str(artist_id)
                        or str(a.get("foreignArtistId")) == str(artist.get("foreignArtistId"))
                    ):
                        in_library = True
                        break
        except Exception as e:
            print("library check error:", e)

        resolved_id = artist.get("id") or artist_id
        name = artist.get("artistName") or (artist.get("artistMetadata") or {}).get("name") or "Unknown Artist"

        # Build album list
        ar = lidarr_get("/album", params={"artistId": resolved_id})
        if ar.status_code == 200:
            for alb in ar.json() or []:
                img = None
                if alb.get("images"):
                    for i in alb["images"]:
                        if i.get("coverType") == "cover" and i.get("remoteUrl"):
                            img = i["remoteUrl"]
                            break

                tracks = lidarr_get("/track", params={"albumId": alb.get("id")}).json() or []
                downloaded = all(t.get("hasFile") for t in tracks) if tracks else False

                albums.append({
                    "id": alb.get("id"),
                    "title": alb.get("title"),
                    "year": (alb.get("releaseDate") or "")[:4],
                    "image_url": img,
                    "downloaded": downloaded
                })

        ctx = {
            "request": request,
            "artist": {
                "id": resolved_id,
                "name": name,
                "image_url": f"/artist/image?id={resolved_id}",
                "in_library": in_library
            },
            "albums": albums
        }

        return templates.TemplateResponse("artist.html", ctx)

    except Exception as e:
        print("artist_detail error:", e)
        raise HTTPException(status_code=404, detail="Artist not found")

# =========================
# ALBUM DETAIL
# =========================
@app.get("/album/{album_id}", response_class=HTMLResponse)
def album_detail(request: Request, album_id: str):
    try:
        a = lidarr_get(f"/album/{album_id}")
        if a.status_code != 200:
            raise HTTPException(status_code=404, detail="Album not found")
        album = a.json()

        # Fetch tracks
        tracks = []
        t = lidarr_get("/track", params={"albumId": album_id})
        if t.status_code == 200:
            seen = set()
            for tr in t.json() or []:
                title = tr.get("title")
                tn = tr.get("trackNumber")
                dedup_key = (str(tn) if tn is not None else "", (title or "").lower())
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                tracks.append({
                    "title": title,
                    "track_number": tn,
                    "has_file": tr.get("hasFile"),
                })

        tracks_sorted = sorted(tracks, key=lambda x: (x["track_number"] or 0, x["title"] or ""))
        for i, tr in enumerate(tracks_sorted, start=1):
            tr["display_number"] = i
            tr["track_number"] = i

        img = None
        if album.get("images"):
            for i in album["images"]:
                if i.get("coverType") == "cover" and i.get("remoteUrl"):
                    img = i["remoteUrl"]
                    break

        ctx = {
            "request": request,
            "album": {
                "id": album.get("id"),
                "title": album.get("title"),
                "year": (album.get("releaseDate") or "")[:4],
                "image_url": img
            },
            "tracks": tracks_sorted
        }
        return templates.TemplateResponse("album.html", ctx)
    except Exception as e:
        print("album_detail error:", e)
        raise HTTPException(status_code=404, detail="Album not found")

# =========================
# NEW: ALBUM SEARCH COMMAND
# =========================
@app.post("/album/search/{album_id}")
def search_album(album_id: str):
    try:
        payload = {"name": "AlbumSearch", "albumIds": [int(album_id)]}
        r = lidarr_post("/command", payload)
        print("Search album response:", r.status_code, r.text)
        if r.status_code in (200, 201):
            return JSONResponse({"status": "ok", "message": "Album search triggered"})
        return JSONResponse({"status": "error", "message": "Failed to trigger search"}, status_code=500)
    except Exception as e:
        print("album_search error:", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

# =========================
# ADD ARTIST
# =========================
@app.post("/add_artist")
def add_artist(artist_id: str = Form(...), artist_name: str = Form(...)):
    try:
        artist = None
        lr = lidarr_get("/artist/lookup", params={"term": artist_id})
        if lr.status_code == 200 and lr.json():
            artist = lr.json()[0]
        else:
            lr2 = lidarr_get("/artist/lookup", params={"term": artist_name})
            if lr2.status_code == 200 and lr2.json():
                artist = lr2.json()[0]
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found")

        mbid = artist.get("foreignArtistId") or artist.get("id")
        name = artist.get("artistName") or (artist.get("artistMetadata") or {}).get("name") or artist_name
        qid = _pick_profile_id("quality", LIDARR_QUALITY_PROFILE) or 1
        mid = _pick_profile_id("metadata", LIDARR_METADATA_PROFILE) or 1

        payload = {
            "foreignArtistId": mbid,
            "artistName": name,
            "rootFolderPath": LIDARR_ROOT_FOLDER,
            "qualityProfileId": qid,
            "metadataProfileId": mid,
            "monitored": True,
            "monitorNewItems": "all",
            "addOptions": {"searchForMissingAlbums": True},
        }

        pr = lidarr_post("/artist", payload)
        print("Add artist response:", pr.status_code, pr.text)
        if pr.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail="Failed to add artist")

        return RedirectResponse(f"/artist/{artist_id}", status_code=302)
    except Exception as e:
        print("add_artist error:", e)
        raise HTTPException(status_code=500, detail="Error adding artist")

@app.post("/download_artist")
def download_artist(artist_id: str = Form(...)):
    try:
        artist = None
        r = lidarr_get(f"/artist/{artist_id}")
        if r.status_code == 200:
            artist = r.json()
        else:
            lr = lidarr_get("/artist/lookup", params={"term": artist_id})
            if lr.status_code == 200 and lr.json():
                artist = lr.json()[0]
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found")

        mbid = artist.get("foreignArtistId") or artist.get("id")
        qid = _pick_profile_id("quality", LIDARR_QUALITY_PROFILE) or 1
        mid = _pick_profile_id("metadata", LIDARR_METADATA_PROFILE) or 1
        payload = {
            "foreignArtistId": mbid,
            "rootFolderPath": LIDARR_ROOT_FOLDER,
            "qualityProfileId": qid,
            "metadataProfileId": mid,
            "monitored": True, "monitorNewItems": "all",
            "addOptions": {"searchForMissingAlbums": True}
        }
        pr = lidarr_post("/artist", payload)
        print("Add artist response:", pr.status_code, pr.text)
        return RedirectResponse(f"/artist/{artist_id}", status_code=302)
    except Exception as e:
        print("download_artist error:", e)
        raise HTTPException(status_code=500, detail="Failed to add artist")

# =========================
# 404 HANDLER
# =========================
@app.exception_handler(404)
def not_found(request: Request, exc):
    return JSONResponse({"error": "Not Found", "detail": str(exc)}, status_code=404)
