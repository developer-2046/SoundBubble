"""Conversation Memory: ASR on extracted voices -> Elasticsearch -> hybrid retrieval.

Elasticsearch is the authoritative store for conversation records and transcript
segments. Audio stays on disk; segments index a reference to the processed audio.

Semantic search: segment text is embedded server-side with one sentence-transformers
model (SB_EMBED_MODEL) at index time AND query time, stored in a dense_vector field,
and queried with Elasticsearch kNN. Keyword search is BM25 on the same index.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

ES_URL = os.environ.get("ES_URL", "")
ES_API_KEY = os.environ.get("ES_API_KEY", "")
ES_CLOUD_ID = os.environ.get("ES_CLOUD_ID", "")
ES_USER = os.environ.get("ES_USER", "")
ES_PASSWORD = os.environ.get("ES_PASSWORD", "")
SEG_INDEX = os.environ.get("ES_SEGMENT_INDEX", "soundbubble-segments")
CONV_INDEX = os.environ.get("ES_CONVERSATION_INDEX", "soundbubble-conversations")
EMBED_MODEL = os.environ.get("SB_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBED_DIMS = int(os.environ.get("SB_EMBED_DIMS", "384"))
ASR_MODEL = os.environ.get("SB_ASR_MODEL", "base.en")
MIN_COSINE = float(os.environ.get("SB_MIN_COSINE", "0.2"))


class SearchUnavailable(RuntimeError):
    pass


_lock = threading.Lock()
_es = None
_embedder = None
_asr = None
_last_error: str | None = None


def _client():
    global _es, _last_error
    if _es is not None:
        return _es
    if not (ES_URL or ES_CLOUD_ID):
        raise SearchUnavailable("Elasticsearch is not configured (set ES_URL or ES_CLOUD_ID)")
    from elasticsearch import Elasticsearch
    kw: dict = {"request_timeout": 20}
    if ES_API_KEY:
        kw["api_key"] = ES_API_KEY
    elif ES_USER:
        kw["basic_auth"] = (ES_USER, ES_PASSWORD)
    es = Elasticsearch(cloud_id=ES_CLOUD_ID, **kw) if ES_CLOUD_ID else Elasticsearch(ES_URL, **kw)
    try:
        es.info()
        _ensure_indices(es)
    except Exception as exc:
        _last_error = f"{type(exc).__name__}: {exc}"[:300]
        raise SearchUnavailable(f"Elasticsearch unreachable: {_last_error}")
    _es, _last_error = es, None
    return es


def _ensure_indices(es):
    if not es.indices.exists(index=SEG_INDEX):
        es.indices.create(index=SEG_INDEX, mappings={"properties": {
            "conversation_id": {"type": "keyword"},
            "conversation_title": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
            "speaker_slot": {"type": "keyword"},
            "speaker_name": {"type": "keyword"},
            "start": {"type": "float"},
            "end": {"type": "float"},
            "text": {"type": "text", "analyzer": "english"},
            "audio_ref": {"type": "keyword", "index": False},
            "source": {"type": "keyword"},
            "embedding_model": {"type": "keyword"},
            "embedding": {"type": "dense_vector", "dims": EMBED_DIMS, "index": True,
                          "similarity": "cosine"},
            "created_at": {"type": "date", "format": "epoch_second"},
        }})
    if not es.indices.exists(index=CONV_INDEX):
        es.indices.create(index=CONV_INDEX, mappings={"properties": {
            "conversation_id": {"type": "keyword"},
            "title": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
            "speakers": {"type": "keyword"},
            "duration_s": {"type": "float"},
            "segment_count": {"type": "integer"},
            "audio_refs": {"type": "object", "enabled": False},
            "model": {"type": "keyword"},
            "asr_model": {"type": "keyword"},
            "created_at": {"type": "date", "format": "epoch_second"},
        }})


def _embed(texts: list[str]):
    global _embedder
    with _lock:
        if _embedder is None:
            from sentence_transformers import SentenceTransformer
            _embedder = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _embedder.encode(texts, normalize_embeddings=True).tolist()


def _transcribe(path: Path) -> list[dict]:
    global _asr
    with _lock:
        if _asr is None:
            from faster_whisper import WhisperModel
            _asr = WhisperModel(ASR_MODEL, device="cpu", compute_type="int8")
    segs, _info = _asr.transcribe(str(path), beam_size=1, vad_filter=True,
                                  condition_on_previous_text=False)
    return [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segs if s.text.strip()]


def status() -> dict:
    configured = bool(ES_URL or ES_CLOUD_ID)
    if not configured:
        return {"status": "not_configured"}
    try:
        _client()
        return {"status": "ready", "embedding_model": EMBED_MODEL, "asr_model": ASR_MODEL}
    except SearchUnavailable as exc:
        return {"status": "unavailable", "error": str(exc)}


def save_session(session_dir: Path, meta: dict, title: str) -> dict:
    es = _client()
    sid = meta["id"]
    title = title or meta.get("label") or f"Conversation {sid}"
    t0 = time.perf_counter()
    docs = []
    for slot, ex in meta["extractions"].items():
        wav = session_dir / f"extract_{slot}.wav"
        for s in _transcribe(wav):
            docs.append({"conversation_id": sid, "conversation_title": title,
                         "speaker_slot": slot, "speaker_name": ex["speaker_name"],
                         "start": s["start"], "end": s["end"], "text": s["text"],
                         "audio_ref": f"/api/sessions/{sid}/audio/{slot}",
                         "source": meta.get("source", "recorded"),
                         "embedding_model": EMBED_MODEL, "created_at": int(time.time())})
    asr_s = time.perf_counter() - t0
    if docs:
        vecs = _embed([d["text"] for d in docs])
        for d, v in zip(docs, vecs):
            d["embedding"] = v
    # Replace any earlier save of this conversation
    es.delete_by_query(index=SEG_INDEX, query={"term": {"conversation_id": sid}},
                       refresh=True, conflicts="proceed")
    from elasticsearch.helpers import bulk
    bulk(es, ({"_index": SEG_INDEX, "_id": f"{sid}-{d['speaker_slot']}-{i}", "_source": d}
              for i, d in enumerate(docs)), refresh="wait_for")
    es.index(index=CONV_INDEX, id=sid, refresh="wait_for", document={
        "conversation_id": sid, "title": title,
        "speakers": sorted({e["speaker_name"] for e in meta["extractions"].values()}),
        "duration_s": meta["seconds"], "segment_count": len(docs),
        "audio_refs": {k: f"/api/sessions/{sid}/audio/{k}" for k in meta["extractions"]},
        "model": next(iter(meta["extractions"].values()))["model"],
        "asr_model": ASR_MODEL, "created_at": int(time.time())})
    return {"conversation_id": sid, "title": title, "segments": len(docs),
            "asr_seconds": round(asr_s, 2), "saved_at": time.time()}


def search(q: str, speaker: str | None, session: str | None, mode: str = "hybrid") -> dict:
    es = _client()
    filters = []
    if speaker:
        filters.append({"term": {"speaker_name": speaker}})
    if session:
        filters.append({"term": {"conversation_id": session}})
    body: dict = {"size": 10, "_source": {"excludes": ["embedding"]},
                  "highlight": {"encoder": "html", "fields": {"text": {"number_of_fragments": 0}},
                                "pre_tags": ["<mark>"],
                                "post_tags": ["</mark>"]}}
    q = q.strip()
    if not q:
        body["query"] = {"bool": {"filter": filters}}
        body["sort"] = [{"created_at": "desc"}, {"start": "asc"}]
    else:
        if mode in ("hybrid", "keyword"):
            body["query"] = {"bool": {"must": [{"match": {"text": {"query": q, "boost": 1.0}}}],
                                      "filter": filters}}
        if mode in ("hybrid", "semantic"):
            body["knn"] = {"field": "embedding", "query_vector": _embed([q])[0], "k": 10,
                           "num_candidates": 100, "filter": filters,
                           # raw cosine floor: drop unrelated nearest neighbours
                           "similarity": MIN_COSINE,
                           "boost": 5.0 if mode == "hybrid" else 1.0}
        if mode == "hybrid":
            # Union of BM25 matches and kNN matches (ES sums scores of docs found by both)
            body["query"]["bool"]["should"] = body["query"]["bool"].pop("must")
            body["query"]["bool"]["minimum_should_match"] = 1
    t0 = time.perf_counter()
    r = es.search(index=SEG_INDEX, **body)
    took = time.perf_counter() - t0
    hits = []
    for h in r["hits"]["hits"]:
        s = h["_source"]
        hits.append(s | {"score": h["_score"], "id": h["_id"],
                         "highlight": (h.get("highlight", {}).get("text") or [None])[0]})
    return {"query": q, "mode": mode, "es_took_ms": r.get("took"),
            "roundtrip_ms": round(took * 1000, 1), "total": r["hits"]["total"]["value"],
            "hits": hits, "embedding_model": EMBED_MODEL}


def facets() -> dict:
    es = _client()
    r = es.search(index=SEG_INDEX, size=0, aggs={
        "speakers": {"terms": {"field": "speaker_name", "size": 20}},
        "conversations": {"terms": {"field": "conversation_id", "size": 50},
                          "aggs": {"title": {"terms": {"field": "conversation_title.raw",
                                                       "size": 1}}}}})
    return {"speakers": [b["key"] for b in r["aggregations"]["speakers"]["buckets"]],
            "conversations": [{"id": b["key"], "title": (b["title"]["buckets"] or [{"key": b["key"]}])[0]["key"],
                               "segments": b["doc_count"]}
                              for b in r["aggregations"]["conversations"]["buckets"]]}


def transcript(conversation_id: str) -> dict:
    """All saved segments of one conversation, in time order (from Elasticsearch)."""
    es = _client()
    r = es.search(index=SEG_INDEX, size=500, _source={"excludes": ["embedding"]},
                  query={"term": {"conversation_id": conversation_id}},
                  sort=[{"start": "asc"}])
    return {"conversation_id": conversation_id,
            "segments": [h["_source"] for h in r["hits"]["hits"]]}
