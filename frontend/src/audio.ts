// Audio helpers: microphone capture, WAV encoding, waveform peaks, synchronized playback.

let ctx: AudioContext | null = null;
export function audioCtx(): AudioContext {
  if (!ctx) ctx = new AudioContext();
  if (ctx.state === "suspended") void ctx.resume();
  return ctx;
}

export type MicError = "denied" | "notfound" | "insecure" | "other";

export function micErrorKind(e: unknown): MicError {
  if (!window.isSecureContext) return "insecure";
  const name = (e as { name?: string })?.name;
  if (name === "NotAllowedError" || name === "SecurityError") return "denied";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "notfound";
  return "other";
}

export class Recorder {
  private stream: MediaStream | null = null;
  private rec: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  analyser: AnalyserNode | null = null;
  startedAt = 0;

  async start(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error("no mediaDevices"), { name: "SecurityError" });
    // Raw capture: browser noise suppression/AGC would alter the mixture the model sees.
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
    });
    const ac = audioCtx();
    const src = ac.createMediaStreamSource(this.stream);
    this.analyser = ac.createAnalyser();
    this.analyser.fftSize = 2048;
    src.connect(this.analyser);
    this.chunks = [];
    this.rec = new MediaRecorder(this.stream);
    this.rec.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.rec.start(250);
    this.startedAt = performance.now();
  }

  level(): number {
    if (!this.analyser) return 0;
    const buf = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(buf);
    let s = 0;
    for (const v of buf) s += v * v;
    return Math.sqrt(s / buf.length);
  }

  async stop(): Promise<Blob> {
    const rec = this.rec;
    if (!rec) throw new Error("not recording");
    const done = new Promise<void>((r) => (rec.onstop = () => r()));
    rec.stop();
    await done;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.analyser = null;
    const blob = new Blob(this.chunks, { type: rec.mimeType });
    return toWav(blob);
  }

  cancel() {
    try { this.rec?.stop(); } catch { /* ignore */ }
    this.stream?.getTracks().forEach((t) => t.stop());
    this.analyser = null;
  }
}

/** Decode any browser-supported audio blob and re-encode as 16-bit mono WAV at its native rate. */
export async function toWav(blob: Blob): Promise<Blob> {
  const buf = await audioCtx().decodeAudioData(await blob.arrayBuffer());
  return encodeWav(mixdown(buf), buf.sampleRate);
}

export function mixdown(buf: AudioBuffer): Float32Array {
  const out = new Float32Array(buf.length);
  for (let c = 0; c < buf.numberOfChannels; c++) {
    const d = buf.getChannelData(c);
    for (let i = 0; i < d.length; i++) out[i] += d[i] / buf.numberOfChannels;
  }
  return out;
}

export function encodeWav(pcm: Float32Array, sr: number): Blob {
  const view = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const w = (o: number, s: string) => [...s].forEach((ch, i) => view.setUint8(o + i, ch.charCodeAt(0)));
  w(0, "RIFF"); view.setUint32(4, 36 + pcm.length * 2, true); w(8, "WAVE");
  w(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sr, true); view.setUint32(28, sr * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  w(36, "data"); view.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) {
    const s = Math.max(-1, Math.min(1, pcm[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

export async function fetchBuffer(url: string): Promise<AudioBuffer> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`audio ${r.status}`);
  return audioCtx().decodeAudioData(await r.arrayBuffer());
}

export function peaks(buf: AudioBuffer, n: number): Float32Array {
  const d = buf.getChannelData(0);
  const out = new Float32Array(n);
  const step = Math.max(1, Math.floor(d.length / n));
  for (let i = 0; i < n; i++) {
    let m = 0;
    for (let j = i * step; j < Math.min(d.length, (i + 1) * step); j++) m = Math.max(m, Math.abs(d[j]));
    out[i] = m;
  }
  return out;
}

/** Plays one of several equal-length buffers; switching tracks keeps the playhead. */
export class SyncPlayer {
  buffers = new Map<string, AudioBuffer>();
  private src: AudioBufferSourceNode | null = null;
  private startCtxTime = 0;
  private startOffset = 0;
  private stopAt: number | null = null;
  playing = false;
  track = "original";
  onEnd: (() => void) | null = null;

  get duration() {
    return this.buffers.get(this.track)?.duration ?? 0;
  }

  position(): number {
    if (!this.playing) return this.startOffset;
    const p = this.startOffset + (audioCtx().currentTime - this.startCtxTime);
    if (this.stopAt !== null && p >= this.stopAt) { this.pause(); return this.startOffset; }
    return Math.min(p, this.duration);
  }

  play(offset = this.position(), stopAt: number | null = null) {
    const buf = this.buffers.get(this.track);
    if (!buf) return;
    this.halt();
    const ac = audioCtx();
    const s = ac.createBufferSource();
    s.buffer = buf;
    s.connect(ac.destination);
    const off = Math.max(0, Math.min(offset, buf.duration - 0.01));
    s.start(0, off);
    s.onended = () => {
      if (this.src === s) { this.playing = false; this.startOffset = 0; this.onEnd?.(); }
    };
    this.src = s;
    this.startCtxTime = ac.currentTime;
    this.startOffset = off;
    this.stopAt = stopAt;
    this.playing = true;
  }

  pause() {
    const p = this.position();
    this.halt();
    this.startOffset = p;
    this.playing = false;
  }

  seek(t: number) {
    if (this.playing) this.play(t, this.stopAt);
    else this.startOffset = t;
  }

  switchTo(track: string) {
    if (track === this.track || !this.buffers.has(track)) return;
    const wasPlaying = this.playing;
    const p = this.position();
    this.track = track;
    if (wasPlaying) this.play(p, this.stopAt);
    else this.startOffset = p;
  }

  private halt() {
    if (this.src) {
      this.src.onended = null;
      try { this.src.stop(); } catch { /* already stopped */ }
      this.src.disconnect();
      this.src = null;
    }
  }

  reset() {
    this.halt();
    this.buffers.clear();
    this.playing = false;
    this.startOffset = 0;
    this.track = "original";
  }
}

/** RMS envelope in dBFS, one value per `hop` seconds. */
export function envelope(buf: AudioBuffer, hop = 0.05): Float32Array {
  const d = buf.getChannelData(0);
  const n = Math.max(1, Math.floor(buf.duration / hop));
  const step = Math.floor(buf.sampleRate * hop);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let j = i * step; j < (i + 1) * step && j < d.length; j++) s += d[j] * d[j];
    out[i] = 10 * Math.log10(s / step + 1e-12);
  }
  return out;
}
