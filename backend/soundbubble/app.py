"""SoundBubble API: target-speaker extraction + conversation memory.

Run:  uvicorn soundbubble.app:app --app-dir backend --port 8000
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("soundbubble")
logging.basicConfig(level=logging.INFO)

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("SB_DATA_DIR", ROOT / "data" / "store"))
MODEL_DIR = os.environ.get("SB_MODEL_DIR", str(ROOT / "models" / "tfmap_context_100"))
SPK_MODEL_DIR = os.environ.get("SB_SPK_MODEL_DIR", str(ROOT / "models" / "spk_emb_100"))
USE_GATE = os.environ.get("SB_SPEAKER_GATE", "1") == "1"
ENABLE_TSE = os.environ.get("SB_ENABLE_TSE", "1") == "1"
MAX_UPLOAD = 25 * 1024 * 1024
MAX_SECONDS = 120
SLOTS = ("a", "b")

(DATA / "speakers").mkdir(parents=True, exist_ok=True)
(DATA / "sessions").mkdir(parents=True, exist_ok=True)

app = FastAPI(title="SoundBubble")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get(
    "SB_CORS", "http://localhost:5173").split(","), allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------- model state
class ModelState:
    def __init__(self):
        self.status = "disabled" if not ENABLE_TSE else "loading"
        self.error: str | None = None
        self.engine = None
        self.hardware: dict = {}

    def load(self):
        try:
            from soundbubble.engine import TSEEngine, hardware_summary
            self.hardware = hardware_summary()
            if not os.path.exists(os.path.join(MODEL_DIR, "avg_model.pt")):
                raise FileNotFoundError(
                    f"Checkpoint not found at {MODEL_DIR}. See README 'Model download'.")
            self.engine = TSEEngine(MODEL_DIR)
            self.engine.attach_speaker_encoder(SPK_MODEL_DIR)
            self.status = "ready"
            log.info("Model %s loaded in %.1fs", self.engine.name, self.engine.load_seconds)
        except Exception as exc:  # report, don't crash the API
            log.exception("model load failed")
            self.status, self.error = "failed", f"{type(exc).__name__}: {exc}"


MODEL = ModelState()


@app.on_event("startup")
def _startup():
    if ENABLE_TSE:
        threading.Thread(target=MODEL.load, daemon=True).start()


# ---------------------------------------------------------------- helpers
def _safe_id(s: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", s):
        raise HTTPException(400, "invalid id")
    return s


async def _read_audio(f: UploadFile) -> tuple[np.ndarray, int]:
    raw = await f.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 25 MB)")
    if not raw:
        raise HTTPException(400, "Empty file")
    try:
        audio, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
    except Exception:
        raise HTTPException(415, "Unsupported audio. Upload WAV/FLAC/OGG; the web UI converts "
                                 "browser recordings to WAV automatically.")
    audio = audio.mean(axis=1)
    if sr < 8000:
        raise HTTPException(400, f"Sample rate {sr} Hz too low")
    dur = len(audio) / sr
    if dur > MAX_SECONDS:
        raise HTTPException(400, f"Audio is {dur:.0f}s; max {MAX_SECONDS}s")
    if not np.isfinite(audio).all() or np.abs(audio).max() < 1e-4:
        raise HTTPException(400, "Audio is silent or invalid")
    return audio, sr


def _meta(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _write_meta(path: Path, d: dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(path)


def _speakers() -> dict:
    return _meta(DATA / "speakers" / "speakers.json")


def _require_model():
    if MODEL.status != "ready":
        raise HTTPException(503, {"loading": "Model is still loading",
                                  "failed": f"Model failed to load: {MODEL.error}",
                                  "disabled": "Extraction is disabled on this deployment "
                                              "(run the local backend for audio inference)"}
                            .get(MODEL.status, MODEL.status))


# ---------------------------------------------------------------- routes
@app.get("/api/health")
def health():
    from soundbubble import memory
    if ARCHIVE_URL:
        try:
            remote = _remote("GET", "/api/health", timeout=5)
            search_state = remote["search"] | {"remote": ARCHIVE_URL}
        except HTTPException as exc:
            search_state = {"status": "unavailable", "error": str(exc.detail), "remote": ARCHIVE_URL}
    else:
        search_state = memory.status()
    return {"ok": True, "deployment": os.environ.get("SB_DEPLOYMENT", "local"), "model": {"status": MODEL.status, "error": MODEL.error,
                                  "name": MODEL.engine.name if MODEL.engine else os.path.basename(MODEL_DIR),
                                  "load_seconds": MODEL.engine.load_seconds if MODEL.engine else None},
            "hardware": MODEL.hardware, "search": search_state}


@app.get("/api/speakers")
def list_speakers():
    return _speakers()


@app.post("/api/speakers/{slot}")
async def enroll(slot: str, name: str = Form(...), file: UploadFile = File(...)):
    if slot not in SLOTS:
        raise HTTPException(400, "slot must be a or b")
    name = name.strip()[:40] or f"Speaker {slot.upper()}"
    audio, sr = await _read_audio(file)
    dur = len(audio) / sr
    if dur < 3:
        raise HTTPException(400, f"Reference is {dur:.1f}s; record at least 5 s of speech")
    sf.write(DATA / "speakers" / f"{slot}.wav", audio, sr)
    sp = _speakers()
    sp[slot] = {"name": name, "seconds": round(dur, 2), "sample_rate": sr,
                "updated": time.time()}
    _write_meta(DATA / "speakers" / "speakers.json", sp)
    return sp[slot]


@app.get("/api/speakers/{slot}/audio")
def speaker_audio(slot: str):
    p = DATA / "speakers" / f"{_safe_id(slot)}.wav"
    if not p.exists():
        raise HTTPException(404, "not enrolled")
    return FileResponse(p, media_type="audio/wav")


@app.post("/api/sessions")
async def create_session(file: UploadFile = File(...), label: str = Form("")):
    audio, sr = await _read_audio(file)
    sid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = DATA / "sessions" / sid
    d.mkdir()
    sf.write(d / "original.wav", audio, sr)
    meta = {"id": sid, "label": label.strip()[:80], "created": time.time(),
            "seconds": round(len(audio) / sr, 2), "sample_rate": sr, "extractions": {}}
    _write_meta(d / "meta.json", meta)
    return meta


@app.get("/api/sessions")
def list_sessions():
    out = []
    for d in sorted((DATA / "sessions").iterdir(), reverse=True):
        m = _meta(d / "meta.json")
        if m:
            out.append(m)
    return out


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    m = _meta(DATA / "sessions" / _safe_id(sid) / "meta.json")
    if not m:
        raise HTTPException(404, "session not found")
    return m


@app.post("/api/sessions/{sid}/extract")
async def extract(sid: str, speaker: str = Form(...)):
    _require_model()
    d = DATA / "sessions" / _safe_id(sid)
    meta = _meta(d / "meta.json")
    if not meta:
        raise HTTPException(404, "session not found")
    sp = _speakers()
    if speaker not in sp:
        raise HTTPException(400, f"Speaker {speaker} is not enrolled")
    mix, msr = sf.read(d / "original.wav", dtype="float32")
    enr, esr = sf.read(DATA / "speakers" / f"{speaker}.wav", dtype="float32")
    others = [sf.read(DATA / "speakers" / f"{k}.wav", dtype="float32")
              for k in sp if k != speaker and (DATA / "speakers" / f"{k}.wav").exists()] if USE_GATE else []
    try:
        res = await run_in_threadpool(MODEL.engine.extract, mix, msr, enr, esr, others)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        log.exception("extraction failed")
        raise HTTPException(500, f"Extraction failed: {type(exc).__name__}: {exc}")
    sf.write(d / f"extract_{speaker}.wav", res.audio, res.sample_rate)
    info = res.meta() | {"speaker": speaker, "speaker_name": sp[speaker]["name"],
                         "created": time.time()}
    meta = _meta(d / "meta.json")
    meta["extractions"][speaker] = info
    _write_meta(d / "meta.json", meta)
    return info


@app.get("/api/sessions/{sid}/audio/{kind}")
def session_audio(sid: str, kind: str):
    d = DATA / "sessions" / _safe_id(sid)
    name = "original.wav" if kind == "original" else f"extract_{_safe_id(kind)}.wav"
    if not (d / name).exists():
        raise HTTPException(404, "audio not found")
    return FileResponse(d / name, media_type="audio/wav")


# ------------------------------------------------- Kubernetes probes
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    # Ready once the process serves requests; model/search state is reported by /api/health
    # (search outages must not take the pod out of rotation, audio demo stays usable).
    if MODEL.status == "loading":
        raise HTTPException(503, "model loading")
    return {"ok": True, "model": MODEL.status}


# ------------------------------------------------- remote memory (local laptop -> cluster)
ARCHIVE_URL = os.environ.get("SB_ARCHIVE_URL", "").rstrip("/")
ARCHIVE_TOKEN = os.environ.get("SB_ARCHIVE_TOKEN", "")


def _remote(method: str, path: str, **kw):
    import requests
    try:
        r = requests.request(method, ARCHIVE_URL + path, timeout=kw.pop("timeout", 30),
                             headers={"X-Archive-Token": ARCHIVE_TOKEN}, **kw)
    except requests.RequestException as exc:
        raise HTTPException(503, f"Remote memory service unreachable: {type(exc).__name__}")
    try:
        body = r.json()
    except ValueError:
        body = {"detail": r.text[:200]}
    if r.status_code >= 400:
        raise HTTPException(r.status_code, body.get("detail", f"remote error {r.status_code}"))
    return body


@app.post("/api/archive/{sid}")
async def archive(sid: str, meta: str = Form(...), files: list[UploadFile] = File(...),
                  x_archive_token: str = Header("")):
    """Receive a processed session from a local SoundBubble (token-protected)."""
    import hmac
    if not ARCHIVE_TOKEN or not hmac.compare_digest(x_archive_token, ARCHIVE_TOKEN):
        raise HTTPException(401, "invalid archive token")
    sid = _safe_id(sid)
    m = json.loads(meta)
    allowed = {"original.wav"} | {f"extract_{k}.wav" for k in m.get("extractions", {}) if k in SLOTS}
    d = DATA / "sessions" / sid
    d.mkdir(exist_ok=True)
    for f in files:
        if f.filename not in allowed:
            raise HTTPException(400, f"unexpected file {f.filename}")
        raw = await f.read()
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, "file too large")
        sf.info(io.BytesIO(raw))  # validate audio
        (d / f.filename).write_bytes(raw)
    m.pop("saved", None)
    m["id"] = sid
    _write_meta(d / "meta.json", m)
    return {"ok": True, "id": sid}


@app.post("/api/sessions/{sid}/save")
async def save_conversation(sid: str, title: str = Form("")):
    from soundbubble import memory
    d = DATA / "sessions" / _safe_id(sid)
    meta = _meta(d / "meta.json")
    if not meta:
        raise HTTPException(404, "session not found")
    if not meta["extractions"]:
        raise HTTPException(400, "Extract at least one speaker before saving")
    if ARCHIVE_URL:
        files = [("files", (p.name, p.read_bytes(), "audio/wav"))
                 for p in [d / "original.wav", *(d / f"extract_{k}.wav" for k in meta["extractions"])]]
        await run_in_threadpool(_remote, "POST", f"/api/archive/{sid}",
                                data={"meta": json.dumps(meta)}, files=files, timeout=60)
        result = await run_in_threadpool(_remote, "POST", f"/api/sessions/{sid}/save",
                                         data={"title": title}, timeout=300)
        result["stored_on"] = ARCHIVE_URL
    else:
        try:
            result = await run_in_threadpool(memory.save_session, d, meta, title.strip()[:120])
        except memory.SearchUnavailable as exc:
            raise HTTPException(503, str(exc))
    meta = _meta(d / "meta.json")
    meta["saved"] = result
    _write_meta(d / "meta.json", meta)
    return result


@app.get("/api/search")
async def search(q: str = "", speaker: str = "", session: str = "", mode: str = "hybrid"):
    from soundbubble import memory
    if ARCHIVE_URL:
        return await run_in_threadpool(_remote, "GET", "/api/search", params=dict(
            q=q, speaker=speaker, session=session, mode=mode))
    try:
        return await run_in_threadpool(memory.search, q, speaker or None, session or None, mode)
    except memory.SearchUnavailable as exc:
        raise HTTPException(503, str(exc))


@app.get("/api/sessions/{sid}/transcript")
async def session_transcript(sid: str):
    from soundbubble import memory
    sid = _safe_id(sid)
    if ARCHIVE_URL:
        return await run_in_threadpool(_remote, "GET", f"/api/sessions/{sid}/transcript")
    try:
        return await run_in_threadpool(memory.transcript, sid)
    except memory.SearchUnavailable as exc:
        raise HTTPException(503, str(exc))


@app.get("/api/search/facets")
async def facets():
    from soundbubble import memory
    if ARCHIVE_URL:
        return await run_in_threadpool(_remote, "GET", "/api/search/facets")
    try:
        return await run_in_threadpool(memory.facets)
    except memory.SearchUnavailable as exc:
        raise HTTPException(503, str(exc))


@app.exception_handler(Exception)
async def _unhandled(_req, exc):
    log.exception("unhandled")
    return JSONResponse({"detail": f"Internal error: {type(exc).__name__}"}, status_code=500)


_static = ROOT / "frontend" / "dist"
if _static.exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="ui")
