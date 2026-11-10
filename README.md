# SoundBubble 🎧

**Select one person's voice in a noisy, overlapping conversation, then search everything they said.**

SoundBubble records a conversation, uses a short voice reference to extract one speaker at a time,
transcribes each separated voice, and stores timestamped transcript segments in **Elasticsearch**,
so *"find where Yuvraj talked about Kubernetes networking"* returns the exact moment and plays it
from his separated voice. The search service runs on **Kubernetes**, deployed and operated through
**Radar**.

> **What it is, precisely:** *target-speaker extraction*. A 5–10 s reference recording tells the
> model whose voice to keep. It is **not** a physical listening radius: it doesn't sense direction
> or distance, doesn't silence the room, and doesn't control headphone noise cancellation. It
> processes recorded microphone audio (**record-and-replay**, not live).

| | |
|---|---|
| 🎙️ Two-voice enrollment + capture in the browser | ✅ working |
| 🔀 Extract each voice, synchronized Original ↔ processed playback, WAV download | ✅ working (CPU) |
| 👥 Speaker view: lanes driven by each extracted track + captions from Elasticsearch | ✅ working |
| 🔎 Conversation Memory: Whisper → Elastic Cloud → keyword + semantic search → click to replay | ✅ working |
| ☸️ Kubernetes deployment, Radar MCP + AI investigation with a verified fix | ✅ done ([RADAR.md](RADAR.md)) |
| ⚡ Continuous low-latency listening | ❌ not built (measured CPU RTF ≈ 1.3–1.8) |

📹 Video script: [DEMO.md](DEMO.md) · 📊 Measurements: [BENCHMARKS.md](BENCHMARKS.md) · 🛰️ Radar evidence: [RADAR.md](RADAR.md)

---

## How it works

```
                LAPTOP (local inference)                              KUBERNETES (k3d "hackathon", Radar-connected)
 ┌──────────────────────────────────────────────────┐          ┌───────────────────────────────────────────┐
 │ React UI  ── mic → WAV, waveforms, sync player   │          │ Deployment soundbubble/app (CPU)           │
 │    │                                              │          │  FastAPI + UI, extraction disabled         │
 │ FastAPI ── BSRNN target-speaker extraction        │          │  Whisper ASR · MiniLM embeddings           │
 │            (REAL-TSE tfmap_context_100)           │          │  search · saved conversations · PVC audio  │
 │         ── two-speaker gate (ECAPA embeddings)    │          └──────────────────┬────────────────────────┘
 │         ── Whisper ASR → MiniLM embeddings        │                             │
 └────────────────────────┬─────────────────────────┘                             │
                          │        HTTPS + API key (server-side only)              │
                          └──────────────►  ELASTIC CLOUD (serverless)  ◄──────────┘
                                            soundbubble-segments · soundbubble-conversations
```

**1. Enroll.** Each person records 5–10 s of their own voice (different words from the conversation).

**2. Capture.** Record (or upload) a conversation. The browser decodes it to WAV, with browser noise
suppression / AGC **off**, so the model sees the raw mixture.

**3. Extract.** For the focused speaker:
- `tfmap_context_100`, a BSRNN band-split separator conditioned on the reference
  ([REAL-TSE / WeSep](https://github.com/REAL-TSE/wesep-real-tse)), runs on CPU with the model kept loaded.
- **Two-speaker gate (our addition):** when both people are enrolled, we slide a 1.5 s window over the
  output, embed it with the ECAPA-TDNN speaker encoder, and attenuate (−30 dB floor) windows that sound
  more like the *other* enrolled person. This fixed turn-taking, where the separator passed a lone voice
  through regardless of the reference (+20.8 dB selectivity on our real recording).
- **Speaker-match check:** compares the output and the *removed* audio with the reference and warns if
  the model likely kept the wrong voice.

**4. Compare.** Original and each extracted voice share one playhead; switch with **O / P**,
play/pause with **Space**, download the processed WAV.

**5. Save → search → replay.** See *Elasticsearch* below.

---

## 🔎 How we use Elasticsearch

Elasticsearch (**Elastic Cloud Serverless, 9.7**) is the **authoritative store** for saved
conversations and the engine behind every search result; audio files stay on disk and are referenced.

**On Save** (only when the user clicks it): each *extracted* voice is transcribed with
`faster-whisper base.en`, so every segment already has a speaker label. Each segment becomes a document:

| Field | Type | Purpose |
|---|---|---|
| `text` | `text` (english analyzer) | BM25 keyword search + highlighting |
| `embedding` | `dense_vector` 384-d, cosine, indexed (HNSW) | semantic kNN search |
| `speaker_name`, `speaker_slot`, `conversation_id` | `keyword` | filters + facets |
| `start`, `end` | `float` | timestamps → replay that exact segment |
| `audio_ref` | `keyword` (not indexed) | pointer to the processed audio |
| `embedding_model`, `source`, `created_at` | `keyword` / `date` | provenance; demo vs. recorded |

A second index, `soundbubble-conversations`, holds one record per conversation (title, speakers,
duration, segment count, models used). Re-saving replaces that conversation's segments
(`delete_by_query` + bulk).

**On Search**, one request does both:
- `bool.should: match(text)` (BM25), plus
- `knn` on `embedding` (`k=10`, `num_candidates=100`, cosine `similarity ≥ 0.2` so unrelated
  neighbours are dropped), with the same speaker/session `filter` on both,
- `highlight` (HTML-encoded `<mark>`), and `terms` aggregations for the speaker / session dropdowns.

Modes: *Keyword + semantic* (default), *Keyword (BM25)*, *Semantic (kNN)*. Query and document vectors
come from the **same** model (`sentence-transformers/all-MiniLM-L6-v2`, computed server-side; the API
key never reaches the browser). Measured query time: **16–71 ms**.

**Captions.** The speaker view reads the conversation's segments back from Elasticsearch
(`GET /api/sessions/{id}/transcript`) and shows the one at the playhead.

If Elasticsearch is unreachable, recording, extraction and playback keep working; the UI says search is
unavailable rather than faking results.

---

## ☸️ How we use Kubernetes + Radar

The search/storage service runs on our own k3d cluster `hackathon` (1 arm64 node), connected to
**Radar Cloud**. Our coding agent (Claude Code) worked through **Radar's MCP server** for discovery, validation, verification, the investigation and the fix:

1. **Discover:** `list_clusters`, `list_namespaces`, node details (10 CPU / 8 GiB, local-path, no Prometheus).
2. **Validate before shipping:** server-side **dry-run** `apply_resource` of the PVC, Deployment and Service.
3. **Deploy:** a teammate built the arm64 image on the Mac, ran `k3d image import`, created the Secret and
   applied the manifests with `kubectl`; the agent then applied the same manifests through Radar
   (`apply_resource`) and confirmed rollout complete, 1/1 ready. It checked only the Secret's key names.
4. **Verify:** `get_workload_logs` showed probes passing **and** the pod reaching Elastic Cloud (`GET /` 200,
   index checks 200, `_search` 200); `top_resources` for live CPU/memory.
5. **AI investigation:** Radar's investigator on `soundbubble/app` → **healthy (confidence 0.9)**, plus a
   real finding: the pod auto-mounts a Kubernetes API token it never uses.
6. **Fix + verify:** `patch_resource` → `automountServiceAccountToken: false`, `wait_for` rollout, audit
   re-run: **3 → 2 findings**. The remaining two (read-only root FS, single replica) are documented
   trade-offs.

Full evidence trail, including a Radar run that errored: [RADAR.md](RADAR.md).

Pod security: non-root UID 1000, all capabilities dropped, `RuntimeDefault` seccomp, no privilege
escalation, no API token. Probes: `/healthz` (liveness), `/readyz` (readiness).

---

## 📊 Results (all measured, see [BENCHMARKS.md](BENCHMARKS.md))

**Our real voices, controlled overlap** (our solo laptop-mic clips summed, with genuine clean references, SI-SDR improvement):

| Model | Alice 0 dB | Bob 0 dB | Alice, other +5 dB | Bob, other +5 dB |
|---|---|---|---|---|
| **tfmap_context_100** (used) | **+16.3** | **+16.4** | **+19.0** | **+18.5** |
| spk_emb_100 | +15.6 | +15.4 | +18.1 | +17.5 |
| spk_emb_causal_100 | −13.7 ❌ wrong voice | +8.8 | +2.8 | +7.0 |

**Our real two-person recording** (acoustic mix, mostly turn-taking; selectivity = own voice kept vs. other voice removed):

| | Target Alice | Target Bob |
|---|---|---|
| spk_emb_100 (our first pick) | ~0 dB, passed everything through | ~0 dB |
| **tfmap_context_100 + two-speaker gate** | **9.9 dB** | **20.8 dB** |

LibriSpeech controlled benchmark (6 pairs × 3 conditions, both causal and non-causal), the
speaker-match check (flagged the 1 wrong-voice output of 18, no false alarms), and timing are in
[BENCHMARKS.md](BENCHMARKS.md).

**Speed:** CPU (Intel Core Ultra 9 285H, 16 threads), about 1.3–1.8× the clip length per speaker
(45.9 s → 80 s). This is offline file processing, not a listening delay.

---

## 🚀 Run it

**Needs:** Linux/macOS, Python 3.10, Node 20, ~3 GB free RAM. No GPU.

```bash
# 1. Python env + upstream model code
uv venv -p 3.10 .venv && source .venv/bin/activate
uv pip install --index-strategy unsafe-best-match -r backend/requirements.lock.txt
uv pip install --no-deps "git+https://github.com/wenet-e2e/wespeaker.git@8f53b6485d9f88a207bd17e7f8dba899495ec794"
git clone https://github.com/REAL-TSE/wesep-real-tse third_party/wesep-real-tse
uv pip install --no-deps -e third_party/wesep-real-tse

# 2. Checkpoints (REAL-TSE, Apache-2.0, ~1 GB zip with 4 models)
mkdir -p models && cd models
uvx gdown 'https://drive.google.com/uc?id=1M4UqK2A2EeHmQ0pCevYqBgaYn3RvklgC' -O realtse.zip
python -c "import zipfile; zipfile.ZipFile('realtse.zip').extractall('.')" && rm realtse.zip && cd ..
#    uses models/tfmap_context_100 (separator) + models/spk_emb_100 (ECAPA encoder for gate/check)

# 3. UI
(cd frontend && npm ci)

# 4. Config: Elastic Cloud (or any Elasticsearch 8.x/9.x)
cp .env.example .env   # set ES_URL + ES_API_KEY (never commit .env)

# 5. Go
./scripts/dev.sh       # API :8000, UI http://localhost:5173
```

Microphone capture needs `localhost` or HTTPS. Whisper and MiniLM download on first save/search.
No Elastic account? A local single-node Elasticsearch works too (see `.env.example`; `ES_URL=http://127.0.0.1:9200`).

### Deploy the search service

**Own k3d cluster** (what we used): [deploy/k3d/README.md](deploy/k3d/README.md), which covers build,
`k3d image import`, the Secret via kubectl, and apply via Radar MCP or kubectl.

**Hackathon vcluster / GitOps** (cnh-starter layout): `deploy/` (with an optional in-cluster
Elasticsearch StatefulSet), `argocd/application.yaml`, `.github/workflows/deploy.yaml` (GHCR image),
`./scripts/setup.sh <team>`.

### Reproduce the benchmarks

```bash
mkdir -p data/corpus && curl -sSL https://www.openslr.org/resources/31/dev-clean-2.tar.gz | tar xz -C data/corpus
python scripts/benchmark_controlled.py --pairs 6          # → bench/controlled_results.json + samples
```

---

## 🗂️ Repository map

```
backend/soundbubble/engine.py   model wrapper: resampling, timing, two-speaker gate, speaker-match check
backend/soundbubble/app.py      FastAPI: speakers, sessions, extract, save, search, transcript, archive, probes
backend/soundbubble/memory.py   Whisper ASR, embeddings, Elasticsearch mappings, hybrid search, facets
frontend/src/App.tsx            UI: speaker cards, capture, studio, speaker view, memory, search
frontend/src/audio.ts           mic capture → WAV, waveform peaks, envelopes, synchronized player
scripts/benchmark_controlled.py controlled SI-SDR benchmark (LibriSpeech)
deploy/k3d/                     manifests for our Radar-connected k3d cluster
deploy/, argocd/, .github/      cnh-starter GitOps path (vcluster)
bench/                          benchmark JSON + LibriSpeech audio samples (CC BY 4.0)
```

API: `GET /api/health` · `POST /api/speakers/{a|b}` · `POST /api/sessions` ·
`POST /api/sessions/{id}/extract` · `GET /api/sessions/{id}/audio/{original|a|b}` ·
`POST /api/sessions/{id}/save` · `GET /api/search?q=&speaker=&session=&mode=` ·
`GET /api/search/facets` · `GET /api/sessions/{id}/transcript` · `/healthz` · `/readyz`

---

## ⚠️ Known limitations

- **Record-and-replay only.** No streaming path; CPU processing is slower than real time on long clips.
- **Leakage on turn-taking.** With the gate, Alice's track still carries Bob at about −10 dB; one short
  Bob phrase appeared in her transcript. The gate needs both people enrolled.
- **Wrong-voice risk.** On LibriSpeech, 1 of 18 (non-causal) and 3 of 18 (causal) cases kept the
  interferer. The speaker-match warning catches whole-output mistakes, not partial leakage.
- **Domain.** Models are trained on read English speech; reverberant rooms, distant mics and more than
  two talkers are harder. ASR is English-only `base.en` and inherits extraction errors (names can be garbled).
- **No accounts.** Anyone who can reach a deployment can search its saved conversations.
- Uploads ≤ 120 s / 25 MB. Synthetic test mixtures are labelled as such; private recordings are not in this repo.

---

## 🙏 Attribution

- **Target-speaker extraction:** [REAL-TSE/wesep-real-tse](https://github.com/REAL-TSE/wesep-real-tse)
  (WeSep, BSRNN), checkpoints by Dr. Ke Zhang, Apache-2.0. Wang et al., *WeSep: A Scalable and Flexible
  Toolkit Towards Generalizable Target Speaker Extraction*, Interspeech 2024.
- **Speaker encoder:** [WeSpeaker](https://github.com/wenet-e2e/wespeaker) ECAPA-TDNN (weights inside the checkpoint; upstream license).
- **ASR:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) + OpenAI Whisper `base.en` (MIT).
- **Embeddings:** [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) (Apache-2.0).
- **Search:** Elastic Cloud / Elasticsearch. **Ops:** [Radar](https://radarhq.io) by Skyhook;
  GitOps scaffolding from [skyhook-io/cnh-starter](https://github.com/skyhook-io/cnh-starter).
- **Benchmark audio:** Mini LibriSpeech (OpenSLR 31, CC BY 4.0).

**Our original work:** the app (backend, UI, synchronized player, speaker view), the two-speaker gate and
speaker-match check, the Elasticsearch schema and hybrid retrieval, the benchmarks on LibriSpeech and on
our own voices, and the Kubernetes/Radar deployment.

**AI assistance disclosure.** Built with substantial help from Claude Code (Anthropic): code, manifests,
benchmark scripts and docs were drafted by the AI under our direction and through Radar's MCP server; we
recorded the audio, ran and listened to the results, and made the decisions. All numbers come from runs
recorded in `bench/` and in this session.
