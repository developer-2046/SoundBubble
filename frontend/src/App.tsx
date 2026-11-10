import { useCallback, useEffect, useRef, useState } from "react";
import { api, audioUrl, Health, Hit, SearchResult, Session, Speaker } from "./api";
import { envelope, fetchBuffer, micErrorKind, peaks, Recorder, SyncPlayer, toWav } from "./audio";

const SLOTS = ["a", "b"] as const;
type Slot = (typeof SLOTS)[number];
const fmt = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const MIC_MSG: Record<string, string> = {
  denied: "Microphone access was blocked. Allow it in the browser's site settings, or upload a file instead.",
  notfound: "No microphone was found. Connect one, or upload a file instead.",
  insecure: "Microphone capture needs localhost or HTTPS. Open the app via http://localhost or upload a file.",
  other: "Could not start the microphone. Upload a file instead.",
};

// ---------------------------------------------------------------- recording hook
function useRecorder(maxSeconds: number) {
  const rec = useRef<Recorder | null>(null);
  const [active, setActive] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const raf = useRef(0);
  const resolveRef = useRef<((b: Blob | null) => void) | null>(null);

  const stop = useCallback(async () => {
    cancelAnimationFrame(raf.current);
    const r = rec.current;
    rec.current = null;
    setActive(false);
    setLevel(0);
    if (!r) return;
    try {
      const blob = await r.stop();
      resolveRef.current?.(blob);
    } catch (e) {
      setError(`Recording failed: ${(e as Error).message}`);
      resolveRef.current?.(null);
    }
  }, []);

  const start = useCallback(async (): Promise<Blob | null> => {
    setError(null);
    const r = new Recorder();
    try {
      await r.start();
    } catch (e) {
      setError(MIC_MSG[micErrorKind(e)]);
      return null;
    }
    rec.current = r;
    setActive(true);
    const tick = () => {
      const t = (performance.now() - r.startedAt) / 1000;
      setElapsed(t);
      setLevel(r.level());
      if (t >= maxSeconds) void stop();
      else raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return new Promise((res) => (resolveRef.current = res));
  }, [maxSeconds, stop]);

  useEffect(() => () => rec.current?.cancel(), []);
  return { active, elapsed, level, error, setError, start, stop };
}

function Meter({ level, active }: { level: number; active: boolean }) {
  const db = level > 0 ? 20 * Math.log10(level) : -90;
  const pct = Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
  return (
    <div className={`meter ${active ? "on" : ""}`} aria-label="input level">
      <div className="meter-fill" style={{ width: `${pct}%` }} />
      <span className="meter-db">{active ? `${db.toFixed(0)} dBFS` : "mic idle"}</span>
    </div>
  );
}

// ---------------------------------------------------------------- speaker card
function SpeakerCard({ slot, speaker, focused, onFocus, onEnrolled, disabled }: {
  slot: Slot; speaker?: Speaker; focused: boolean; onFocus: () => void; onEnrolled: () => void; disabled: boolean;
}) {
  const [name, setName] = useState(speaker?.name ?? "");
  const [busy, setBusy] = useState(false);
  const rec = useRecorder(12);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (speaker?.name) setName(speaker.name); }, [speaker?.name]);

  const submit = async (blob: Blob | null) => {
    if (!blob) return;
    if (!name.trim()) { rec.setError("Enter a name first."); return; }
    setBusy(true);
    try {
      await api.enroll(slot, name.trim(), blob);
      rec.setError(null);
      onEnrolled();
    } catch (e) {
      rec.setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const record = async () => {
    if (rec.active) { await rec.stop(); return; }
    if (!name.trim()) { rec.setError("Enter a name first."); return; }
    await submit(await rec.start());
  };

  const upload = async (f?: File) => {
    if (!f) return;
    try { await submit(await toWav(f)); } catch { rec.setError("Could not decode that audio file."); }
  };

  return (
    <div className={`card speaker ${focused ? "focused" : ""}`}>
      <div className="speaker-head">
        <span className="slot">{slot.toUpperCase()}</span>
        <input className="name" placeholder={slot === "a" ? "Teammate name" : "Second teammate"} value={name}
               maxLength={40} onChange={(e) => setName(e.target.value)} aria-label={`Speaker ${slot} name`} />
        <span className={`badge ${speaker ? "ok" : ""}`}>{speaker ? `Enrolled · ${speaker.seconds.toFixed(1)}s` : "Not enrolled"}</span>
      </div>
      <p className="hint">Read 5–10 s of natural speech alone. Use a different sentence from the conversation.</p>
      {rec.active && <Meter level={rec.level} active />}
      <div className="row">
        <button className={`btn ${rec.active ? "rec" : ""}`} onClick={record} disabled={busy || disabled}>
          {rec.active ? `■ Stop · ${rec.elapsed.toFixed(1)}s` : speaker ? "● Re-record reference" : "● Record reference"}
        </button>
        <button className="btn ghost" onClick={() => fileRef.current?.click()} disabled={busy || rec.active}>Upload</button>
        <input ref={fileRef} type="file" accept="audio/*" hidden onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
        {speaker && <audio className="mini" controls src={`/api/speakers/${slot}/audio?t=${speaker.updated}`} />}
      </div>
      {busy && <p className="status">Uploading reference…</p>}
      {rec.error && <p className="error">{rec.error}</p>}
      <button className={`focus-btn ${focused ? "on" : ""}`} onClick={onFocus} disabled={!speaker} aria-pressed={focused}>
        {focused ? "◉ Speaker focus" : "○ Focus on this speaker"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------- waveform
function Waveform({ data, pos, dur, accent, onSeek, segments }: {
  data: Float32Array | null; pos: number; dur: number; accent: boolean; onSeek: (t: number) => void;
  segments?: { start: number; end: number }[];
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    c.width = w * dpr; c.height = h * dpr;
    const g = c.getContext("2d")!;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);
    const css = getComputedStyle(document.documentElement);
    if (segments && dur) {
      g.fillStyle = css.getPropertyValue("--seg");
      for (const s of segments) g.fillRect((s.start / dur) * w, 0, ((s.end - s.start) / dur) * w, h);
    }
    if (!data) return;
    const px = dur ? (pos / dur) * w : 0;
    const bw = w / data.length;
    for (let i = 0; i < data.length; i++) {
      const x = i * bw;
      const a = Math.max(1, data[i] * h * 0.95);
      g.fillStyle = x < px ? css.getPropertyValue(accent ? "--accent" : "--fg") : css.getPropertyValue("--dim");
      g.fillRect(x, (h - a) / 2, Math.max(1, bw - 1), a);
    }
    g.fillStyle = css.getPropertyValue("--fg");
    g.fillRect(px, 0, 2, h);
  }, [data, pos, dur, accent, segments]);
  return (
    <canvas ref={ref} className="wave" onClick={(e) => {
      const r = e.currentTarget.getBoundingClientRect();
      onSeek(((e.clientX - r.left) / r.width) * dur);
    }} />
  );
}

// ---------------------------------------------------------------- main
export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthErr, setHealthErr] = useState<string | null>(null);
  const [speakers, setSpeakers] = useState<Record<string, Speaker>>({});
  const [focus, setFocus] = useState<Slot>("a");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [track, setTrack] = useState("original");
  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [wave, setWave] = useState<Record<string, Float32Array>>({});
  const [env, setEnv] = useState<Record<string, Float32Array>>({});
  const [segs, setSegs] = useState<Hit[]>([]);
  const player = useRef(new SyncPlayer());
  const mixRec = useRecorder(60);
  const mixFile = useRef<HTMLInputElement>(null);
  const [label, setLabel] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [h, sp, ss] = await Promise.all([api.health(), api.speakers(), api.sessions()]);
      setHealth(h); setSpeakers(sp); setSessions(ss); setHealthErr(null);
    } catch (e) {
      setHealthErr((e as Error).message);
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (health?.model.status !== "loading") return;
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [health?.model.status, refresh]);

  // animation loop for playhead
  useEffect(() => {
    let raf = 0;
    let last = -1;
    let lastPlaying = false;
    const loop = () => {
      const p = player.current;
      const t = p.position();
      // Only re-render when the playhead moved noticeably or play state changed
      if (Math.abs(t - last) > 0.03 || p.playing !== lastPlaying) {
        last = t;
        lastPlaying = p.playing;
        setPos(t);
        setPlaying(p.playing);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const loadTrack = useCallback(async (s: Session, kind: string) => {
    const buf = await fetchBuffer(`${audioUrl(s.id, kind)}?t=${s.extractions[kind]?.created ?? s.created}`);
    player.current.buffers.set(kind, buf);
    setWave((w) => ({ ...w, [kind]: peaks(buf, 600) }));
    setEnv((e) => ({ ...e, [kind]: envelope(buf) }));
  }, []);

  const openSession = useCallback(async (s: Session, preferTrack?: string) => {
    player.current.reset();
    setWave({});
    setEnv({});
    setSegs([]);
    if (s.saved) api.transcript(s.id).then((t) => setSegs(t.segments)).catch(() => undefined);
    setSession(s);
    setTrack("original");
    setErr(null);
    try {
      await loadTrack(s, "original");
      for (const k of Object.keys(s.extractions)) await loadTrack(s, k);
      if (preferTrack && player.current.buffers.has(preferTrack)) {
        player.current.switchTo(preferTrack);
        setTrack(preferTrack);
      }
    } catch (e) {
      setErr(`Could not load audio: ${(e as Error).message}`);
    }
  }, [loadTrack]);

  const uploadMixture = async (blob: Blob | null) => {
    if (!blob) return;
    setBusy("Uploading conversation…");
    try {
      const s = await api.createSession(blob, label);
      setLabel("");
      await openSession(s);
      await refresh();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const recordMixture = async () => {
    if (mixRec.active) { await mixRec.stop(); return; }
    await uploadMixture(await mixRec.start());
  };

  const runExtract = async (slots: Slot[]) => {
    if (!session) return;
    setErr(null);
    for (const slot of slots) {
      setBusy(`Extracting ${speakers[slot]?.name ?? slot.toUpperCase()}… (running ${health?.model.name} on ${health?.hardware.cuda ? "GPU" : "CPU"})`);
      try {
        await api.extract(session.id, slot);
      } catch (e) {
        setErr((e as Error).message);
        break;
      }
    }
    try {
      const s = await api.session(session.id);
      setSession(s);
      for (const slot of slots) if (s.extractions[slot]) await loadTrack(s, slot);
      const last = slots[slots.length - 1];
      if (s.extractions[last]) { player.current.switchTo(last); setTrack(last); }
      void refresh();
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(null);
  };

  const switchTrack = (t: string) => { player.current.switchTo(t); setTrack(player.current.track); };
  const togglePlay = () => (player.current.playing ? player.current.pause() : player.current.play());

  // keyboard: space = play/pause, O = original, P = processed (focused)
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") return;
      if (e.code === "Space") { e.preventDefault(); togglePlay(); }
      if (e.key === "o") switchTrack("original");
      if (e.key === "p") switchTrack(focus);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  const modelStatus = health?.model.status;
  const enrolledFocus = !!speakers[focus];
  const ex = session?.extractions[track];
  const processedTracks = session ? SLOTS.filter((s) => session.extractions[s]) : [];

  return (
    <div className="app">
      <header>
        <div className="brand"><span className="logo" />SoundBubble</div>
        <div className="pills">
          <span className={`pill ${modelStatus}`} title={health?.model.error ?? ""}>
            Model: {healthErr ? "API offline" : modelStatus === "ready" ? `${health?.model.name} · ready` : modelStatus ?? "…"}
          </span>
          <span className={`pill ${health?.search.status}`} title={health?.search.error ?? ""}>
            Search: {health?.search.status?.replace("_", " ") ?? "…"}
          </span>
          {health?.hardware.cpu && <span className="pill dim">{health.hardware.cuda ? "GPU" : "CPU"} · {health.hardware.cpu}</span>}
        </div>
      </header>

      {healthErr && <div className="banner error">{healthErr} Start it with <code>./scripts/dev.sh</code>.</div>}
      {modelStatus === "loading" && <div className="banner">Loading the extraction model… Recording and enrollment work in the meantime.</div>}
      {modelStatus === "failed" && <div className="banner error">Extraction model failed to load: {health?.model.error}</div>}
      {modelStatus === "disabled" && <div className="banner">Audio extraction is disabled on this deployment. Saved conversations and search still work here; run SoundBubble locally to extract voices.</div>}

      <section className="speakers">
        {SLOTS.map((s) => (
          <SpeakerCard key={s} slot={s} speaker={speakers[s]} focused={focus === s} onFocus={() => setFocus(s)}
                       onEnrolled={refresh} disabled={false} />
        ))}
      </section>

      <section className="card capture">
        <div className="capture-main">
          <button className={`big-rec ${mixRec.active ? "on" : ""}`} onClick={recordMixture} disabled={!!busy}
                  aria-label={mixRec.active ? "Stop recording" : "Record conversation"}>
            <span>{mixRec.active ? "■" : "●"}</span>
          </button>
          <div className="capture-info">
            <h2>{mixRec.active ? `Recording · ${fmt(mixRec.elapsed)}` : "Capture a conversation"}</h2>
            <p className="hint">Both people talk at the same time for 10–20 s. The recording is processed after you stop (record-and-replay, not live).</p>
            <Meter level={mixRec.level} active={mixRec.active} />
            <div className="row">
              <input className="label-in" placeholder="Label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} />
              <button className="btn ghost" onClick={() => mixFile.current?.click()} disabled={!!busy || mixRec.active}>Upload mixture</button>
              <input ref={mixFile} type="file" accept="audio/*" hidden onChange={async (e) => {
                const f = e.target.files?.[0]; e.target.value = "";
                if (f) { try { await uploadMixture(await toWav(f)); } catch { setErr("Could not decode that audio file."); } }
              }} />
            </div>
            {mixRec.error && <p className="error">{mixRec.error}</p>}
          </div>
        </div>
      </section>

      {session ? (
        <section className="card studio">
          <div className="studio-head">
            <div>
              <h2>{session.label || session.id}</h2>
              <p className="hint">{fmt(session.seconds)} · {session.sample_rate / 1000} kHz{session.source === "demo" ? " · labelled demonstration material" : ""}</p>
            </div>
            <div className="row">
              <button className="btn primary" disabled={!!busy || modelStatus !== "ready" || !enrolledFocus} onClick={() => runExtract([focus])}>
                Extract {speakers[focus]?.name ?? focus.toUpperCase()}
              </button>
              <button className="btn" disabled={!!busy || modelStatus !== "ready" || !speakers.a || !speakers.b} onClick={() => runExtract(["a", "b"])}>
                Extract both
              </button>
            </div>
          </div>
          {!enrolledFocus && <p className="hint">Enroll the focused speaker to extract their voice.</p>}
          {busy && <div className="status working"><span className="spinner" />{busy}</div>}
          {err && <p className="error">{err}</p>}

          <div className="switch" role="tablist" aria-label="Playback source">
            <button role="tab" aria-selected={track === "original"} className={track === "original" ? "sel" : ""} onClick={() => switchTrack("original")}>Original</button>
            {processedTracks.map((s) => (
              <button key={s} role="tab" aria-selected={track === s} className={track === s ? "sel accent" : ""} onClick={() => switchTrack(s)}>
                {session.extractions[s].speaker_name} only
              </button>
            ))}
          </div>

          <div className="transport">
            <button className="play" onClick={togglePlay} disabled={!wave.original} aria-label={playing ? "Pause" : "Play"}>{playing ? "❚❚" : "▶"}</button>
            <div className="waves">
              <Waveform data={wave[track] ?? null} pos={pos} dur={player.current.duration} accent={track !== "original"} onSeek={(t) => player.current.seek(t)} />
              <div className="time">{fmt(pos)} / {fmt(player.current.duration)} · <kbd>Space</kbd> play · <kbd>O</kbd>/<kbd>P</kbd> switch</div>
            </div>
          </div>

          {ex ? (
            <div className="metrics">
              <div><b>{ex.processing_seconds.toFixed(2)} s</b><span>processing time ({ex.audio_seconds.toFixed(1)} s audio)</span></div>
              <div><b>{ex.rtf.toFixed(2)}×</b><span>real-time factor (offline file processing, not live delay)</span></div>
              <div><b>{ex.speaker_margin !== null ? ex.speaker_margin.toFixed(2) : "–"}</b><span>speaker-match margin (output vs. removed audio)</span></div>
              <div><b>{ex.model}</b><span>{health?.hardware.cuda ? "GPU" : "CPU"} · {health?.hardware.torch_threads} threads</span></div>
              {!!ex.gate_windows && (
                <div><b>{ex.gate_muted_windows}/{ex.gate_windows}</b><span>windows muted by two-speaker gate (sounded like the other enrolled voice)</span></div>
              )}
              {ex.wrong_speaker_suspected && (
                <p className="warn">Low confidence: the removed audio sounds more like {ex.speaker_name} than the output does, so the model may have kept the wrong voice. Try a longer, cleaner reference.</p>
              )}
              <a className="btn ghost" href={audioUrl(session.id, track)} download={`${session.id}_${ex.speaker_name}.wav`}>Download processed WAV</a>
            </div>
          ) : (
            <p className="hint">Playing the unprocessed recording. Extract a speaker, then switch between Original and processed. Playback position is kept.</p>
          )}

          {processedTracks.length > 0 && (
            <SpeakerView tracks={processedTracks.map((k) => ({ key: k, name: session.extractions[k].speaker_name }))}
                         env={env} pos={pos} segs={segs} focused={track} saved={!!session.saved} />
          )}

          <Memory session={session} onSaved={async () => {
            const s = await api.session(session.id);
            setSession(s);
            api.transcript(s.id).then((t) => setSegs(t.segments)).catch(() => undefined);
          }} searchStatus={health?.search.status} />
        </section>
      ) : (
        <section className="card empty">
          <p>No conversation selected. Record one above, or pick a previous recording from the timeline.</p>
        </section>
      )}

      <section className="card timeline">
        <h3>Timeline</h3>
        {sessions.length === 0 && <p className="hint">Recordings you capture appear here.</p>}
        <ol>
          {sessions.map((s) => (
            <li key={s.id} className={session?.id === s.id ? "sel" : ""}>
              <button onClick={() => openSession(s)}>
                <span className="t">{new Date(s.created * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                <span className="l">{s.label || s.id}</span>
                <span className="d">{fmt(s.seconds)}</span>
                <span className="x">{Object.values(s.extractions).map((e) => e.speaker_name).join(" · ") || "unprocessed"}</span>
                {s.saved && <span className="saved">saved</span>}
              </button>
            </li>
          ))}
        </ol>
      </section>

      <Search onPlay={async (h) => {
        const s = await api.session(h.conversation_id).catch(() => null);
        if (!s) { setErr("That conversation's audio is not stored on this server."); return; }
        if (session?.id !== s.id) await openSession(s, h.speaker_slot);
        else { player.current.switchTo(h.speaker_slot); setTrack(h.speaker_slot); }
        player.current.play(Math.max(0, h.start - 0.3), h.end + 0.3);
        window.scrollTo({ top: 0, behavior: "smooth" });
      }} searchStatus={health?.search.status} searchError={health?.search.error} />

      <footer>
        Target-speaker extraction: a short reference recording tells the model whose voice to keep. SoundBubble processes microphone audio; it does not
        silence the room, measure physical distance, or control headphone noise cancellation.
      </footer>
    </div>
  );
}

// ---------------------------------------------------------------- speaker view (for demo video)
function SpeakerView({ tracks, env, pos, segs, focused, saved }: {
  tracks: { key: string; name: string }[]; env: Record<string, Float32Array>; pos: number; segs: Hit[];
  focused: string; saved: boolean;
}) {
  const hop = 0.05;
  return (
    <div className="speaker-view" aria-label="Who is speaking">
      {tracks.map(({ key, name }) => {
        const e = env[key];
        // peak over the last 150 ms of the extracted track at the playhead
        let level = -90;
        if (e) for (let i = Math.max(0, Math.floor(pos / hop) - 2); i <= Math.floor(pos / hop) && i < e.length; i++) level = Math.max(level, e[i]);
        const active = level > -42;
        const pct = Math.max(0, Math.min(100, ((level + 60) / 50) * 100));
        const cap = segs.find((g) => g.speaker_slot === key && pos >= g.start - 0.1 && pos <= g.end + 0.1);
        return (
          <div key={key} className={`lane ${active ? "active" : ""} ${focused === key ? "focused" : ""}`}>
            <div className="lane-head">
              <span className="dot" />
              <b>{name}</b>
              <span className="lane-state">{active ? "speaking" : "quiet"}</span>
            </div>
            <div className="lane-meter"><div style={{ width: `${pct}%` }} /></div>
            <p className="caption">{cap ? cap.text : saved ? "…" : "Save the conversation to show captions"}</p>
          </div>
        );
      })}
      <p className="hint">Levels come from each extracted track; captions are the saved Elasticsearch transcript segments at the playhead.
        This shows who is speaking, not where the sound comes from (single microphone).</p>
    </div>
  );
}

// ---------------------------------------------------------------- memory: save
function Memory({ session, onSaved, searchStatus }: { session: Session; onSaved: () => void; searchStatus?: string }) {
  const [title, setTitle] = useState(session.label);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setTitle(session.label); setErr(null); }, [session.id, session.label]);
  const n = Object.keys(session.extractions).length;
  const save = async () => {
    setBusy(true); setErr(null);
    try { await api.save(session.id, title); onSaved(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="memory">
      <h3>Conversation Memory</h3>
      {session.saved ? (
        <p className="ok-text">Saved to Elasticsearch as “{session.saved.title}”: {session.saved.segments} transcript segments (speech-to-text took {session.saved.asr_seconds}s).
          Saving again replaces it.</p>
      ) : (
        <p className="hint">Saving transcribes each extracted voice and indexes timestamped segments in Elasticsearch. Nothing is saved automatically.</p>
      )}
      <div className="row">
        <input className="label-in" value={title} placeholder="Conversation title" onChange={(e) => setTitle(e.target.value)} maxLength={120} />
        <button className="btn" onClick={save} disabled={busy || n === 0 || searchStatus !== "ready"}>
          {busy ? "Transcribing & indexing…" : session.saved ? "Re-save" : "Save conversation"}
        </button>
      </div>
      {n === 0 && <p className="hint">Extract at least one speaker first.</p>}
      {searchStatus && searchStatus !== "ready" && <p className="hint">Search is {searchStatus.replace("_", " ")}; extraction and playback still work.</p>}
      {err && <p className="error">{err}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- memory: search
function Search({ onPlay, searchStatus, searchError }: { onPlay: (h: Hit) => void; searchStatus?: string; searchError?: string }) {
  const [q, setQ] = useState("");
  const [speaker, setSpeaker] = useState("");
  const [conv, setConv] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [res, setRes] = useState<SearchResult | null>(null);
  const [facets, setFacets] = useState<{ speakers: string[]; conversations: { id: string; title: string }[] }>({ speakers: [], conversations: [] });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (searchStatus === "ready") api.facets().then(setFacets).catch(() => undefined); }, [searchStatus, res]);
  const run = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setBusy(true); setErr(null);
    try { setRes(await api.search(q, speaker, conv, mode)); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  if (searchStatus !== "ready")
    return (
      <section className="card search">
        <h3>Search conversations</h3>
        <p className="hint">Search is unavailable ({searchStatus?.replace("_", " ") ?? "checking"}){searchError ? `: ${searchError}` : ""}. Extraction and playback are unaffected.</p>
      </section>
    );
  return (
    <section className="card search">
      <h3>Search conversations</h3>
      <form className="row" onSubmit={run}>
        <input className="q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Kubernetes networking" aria-label="Search query" />
        <select value={speaker} onChange={(e) => setSpeaker(e.target.value)} aria-label="Speaker filter">
          <option value="">All speakers</option>
          {facets.speakers.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={conv} onChange={(e) => setConv(e.target.value)} aria-label="Session filter">
          <option value="">All sessions</option>
          {facets.conversations.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
        </select>
        <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Retrieval mode">
          <option value="hybrid">Keyword + semantic</option>
          <option value="keyword">Keyword (BM25)</option>
          <option value="semantic">Semantic (kNN)</option>
        </select>
        <button className="btn primary" disabled={busy}>{busy ? "Searching…" : "Search"}</button>
      </form>
      {err && <p className="error">{err}</p>}
      {res && (
        <>
          <p className="hint">{res.total} matching segments in Elasticsearch · {res.es_took_ms} ms query · embeddings: {res.embedding_model.split("/").pop()}</p>
          <ul className="hits">
            {res.hits.map((h) => (
              <li key={h.id}>
                <button onClick={() => onPlay(h)}>
                  <span className="who">{h.speaker_name}</span>
                  <span className="when">▶ {fmt(h.start)}–{fmt(h.end)}</span>
                  <span className="conv">{h.conversation_title}{h.source === "demo" ? " · demo material" : ""}</span>
                  <span className="txt" dangerouslySetInnerHTML={{ __html: h.highlight ?? escapeHtml(h.text) }} />
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
