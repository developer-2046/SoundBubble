# SoundBubble: video script (≈3 min)

Two voices, one microphone. Pick a person, hear only them, then search what they said.

**Before recording**
- Backend + UI running (`./scripts/dev.sh`), both speaker cards enrolled, search pill says *ready*.
- Radar Cloud open in a second tab: cluster `hackathon` → Deployment `soundbubble/app`.
- Backup material: the "Both" session in the timeline, recorded earlier today through the same
  pipeline. If you use it, say so on screen: *"recorded earlier, processed by the same pipeline"*.
- Screen recorder captures system audio (the Original ↔ processed switch has to be heard).
- Close other tabs; zoom the browser to 110–125% so text reads on video.

Speaker names below: **Y** = Yuvraj, **T** = teammate. Lines in *italics* are spoken narration.

---

## 0:00–0:15 · Hook

**Screen:** the SoundBubble main screen, both speaker cards enrolled.

**Y:** *"Ever been in a conversation where two people talk at once, and later you can't
remember who said what? SoundBubble picks one voice out of overlapping speech, and then lets you
search everything that person said."*

**On-screen text:** `SoundBubble: select a voice, find what they said`

## 0:15–0:35 · Enrollment: how it knows who is who

**Screen:** point at the two speaker cards; play a few seconds of one reference.

**T:** *"Each of us recorded a 10-second reference of our own voice, different sentences from what we'll
say next. That reference is all the model needs. This is target-speaker extraction: the reference tells
the model whose voice to keep. It does not measure distance or direction, and it doesn't touch your
headphones' noise cancelling. It works on the recorded audio."*

## 0:35–1:00 · Capture a real overlapping conversation

**Screen:** press the big red record button. The live level meter moves. Both talk **at the same time**
for ~15 s; one of you louder. Include a topic for search later:

> **Y:** "…so for Kubernetes networking we expose the app through a Service on port 80…"
> **T** (over the top): "…and the Elastic index stores every transcript segment with a vector…"

Press stop.

**Y:** *"That's a fresh recording from this laptop's microphone, both of us talking over each other."*

## 1:00–1:35 · Extract one voice, then the other

**Screen:** Speaker focus on **Y** → **Extract**. While it processes, keep talking (or cut the wait):

**Y:** *"This runs a BSRNN target-speaker-extraction model from the REAL-TSE / WeSep project, on the CPU.
No GPU. We benchmarked four public checkpoints on our own voices and picked the best one. We also added a
two-speaker gate: because both of us are enrolled, stretches that sound more like the other person get
muted. That fixed the case where we take turns instead of overlapping."*

**Screen:** press play, toggle **Original → Y only → Original** with the O / P keys. Let each play
2–3 seconds; the playhead stays in sync.

**Y:** *"Original… now only my voice. Same playhead."*

**Screen:** focus **T** → Extract → play **T only**.

**T:** *"Same recording, my reference, my voice."*

**Screen:** zoom on the metrics row: processing time, real-time factor, speaker-match margin,
windows muted by the gate.

**T:** *"Every number here is measured: processing time, real-time factor, and a speaker-match check
that warns us if the model grabbed the wrong voice. It's record-and-replay, not live listening. On this
CPU it takes about one and a half times the clip length."*

## 1:35–2:15 · Conversation Memory: Elasticsearch

**Screen:** title the conversation → **Save conversation** → "Saved to Elasticsearch: N transcript segments".

**Y:** *"When we hit Save, each extracted voice is transcribed with Whisper. Because we transcribe the
separated tracks, every line already knows who said it. Each line becomes a timestamped segment in
Elastic Cloud: speaker, start and end time, the text, a 384-dimension embedding, and a reference to the
processed audio."*

**Screen:** play the conversation; show the **speaker lanes** lighting up with live captions.

**Y:** *"These captions are those Elasticsearch segments, shown at the playhead. The lanes light up from
each extracted track's real audio level."*

**Screen:** Search box → type **"Kubernetes networking"** → mode *Keyword + semantic* → Search.

**T:** *"Search runs in Elasticsearch: BM25 keyword matching on the transcript, plus kNN vector search on
the embeddings, in one query, filtered by speaker or session. Same embedding model at index and query
time. The query took about 20 to 70 milliseconds."*

**Screen:** set the speaker filter to **Y** → click the top result → it plays exactly that segment of
Y's extracted audio.

**T:** *"Find where Yuvraj talked about Kubernetes networking: click, and it plays that moment, from
his separated voice."*

**Screen (optional, 5 s):** switch mode to *Semantic* and search a paraphrase with no shared words, e.g.
"how do we expose the service" → still finds the segment.

## 2:15–2:50 · Kubernetes + Radar

**Screen:** Radar Cloud → cluster `hackathon` → Resources → Deployment `soundbubble/app` (Healthy, 1/1).

**Y:** *"The search and storage side runs on our Kubernetes cluster, connected to Radar. Our coding agent
deployed it through Radar's MCP server: discovered the cluster, dry-ran the manifests server-side, applied
them, then read the pod logs through Radar to confirm it was reaching Elastic Cloud."*

**Screen:** Investigations → run `chat_qAti0PYK4KNbEvBo`: verdict *healthy*, confidence 0.9, evidence list.

**Y:** *"Then we asked Radar's AI investigator to check the running app. It confirmed it was healthy, no
restarts, probes passing, and flagged a real security finding: the pod mounted a Kubernetes API token it
never uses."*

**Screen:** Checks / audit for namespace `soundbubble`: 2 findings remaining. Optionally Timeline showing
the Deployment generation 2 patch.

**T:** *"We turned token auto-mount off, Radar watched the rollout, and the audit dropped from three
findings to two. The other two we left on purpose: the models need a writable filesystem, and our data
volume can only attach to one pod."*

## 2:50–3:00 · Close

**Screen:** back to SoundBubble, a processed track playing.

**Y:** *"SoundBubble: a short reference picks the voice, the model separates it, Elastic remembers it,
and Radar keeps the service healthy. It's record-and-replay today; live listening is next."*

**On-screen text:** repo URL · "Built with REAL-TSE/WeSep, faster-whisper, Elastic Cloud, Radar"

---

## Honesty checklist (say or show these)
- Record-and-replay, not live. CPU file processing ≈ 1.3–1.8× clip length per speaker.
- Selects by voice reference; no direction, distance, or headphone control.
- Strongest on overlapping speech; some leakage remains when people take turns (the gate needs both
  people enrolled).
- Backup clip, if used, is labelled as recorded earlier.

## If something breaks on camera
| Problem | Do this |
|---|---|
| Fresh take sounds bad | Open "Both" from the timeline; say it was recorded earlier |
| Extraction slow | Cut the wait in editing; the metrics row shows the real time |
| Search pill "unavailable" | Elastic Cloud unreachable; extraction/playback still work, show the Radar part, retry |
| Wrong voice warning appears | Keep it in; it's the speaker-match check doing its job |
