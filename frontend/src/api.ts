export type Speaker = { name: string; seconds: number; sample_rate: number; updated: number };
export type Extraction = {
  model: string; audio_seconds: number; processing_seconds: number; rtf: number;
  speaker: string; speaker_name: string; created: number;
  sim_output: number | null; sim_residual: number | null; speaker_margin: number | null;
  wrong_speaker_suspected: boolean | null;
  separator_seconds?: number | null; gate_seconds?: number | null; gate_windows?: number; gate_muted_windows?: number;
};
export type Session = {
  id: string; label: string; created: number; seconds: number; sample_rate: number;
  source?: string;
  extractions: Record<string, Extraction>;
  saved?: { conversation_id: string; title: string; segments: number; asr_seconds: number; saved_at: number };
};
export type Health = {
  ok: boolean;
  model: { status: "loading" | "ready" | "failed" | "disabled"; error: string | null; name: string; load_seconds: number | null };
  hardware: { cpu?: string; torch_threads?: number; cuda?: boolean; torch?: string };
  search: { status: "ready" | "not_configured" | "unavailable"; error?: string; embedding_model?: string; asr_model?: string };
};
export type Hit = {
  id: string; conversation_id: string; conversation_title: string; speaker_slot: string; speaker_name: string;
  start: number; end: number; text: string; audio_ref: string; score: number; highlight: string | null; source?: string;
};
export type SearchResult = { query: string; mode: string; es_took_ms: number; roundtrip_ms: number; total: number; hits: Hit[]; embedding_model: string };

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, init);
  } catch {
    throw new Error("Backend unreachable. Is the API running on port 8000?");
  }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = (body as { detail?: unknown }).detail;
    throw new Error(typeof d === "string" ? d : `Request failed (${r.status})`);
  }
  return body as T;
}

const form = (o: Record<string, string | Blob>, filename = "audio.wav") => {
  const f = new FormData();
  for (const [k, v] of Object.entries(o)) v instanceof Blob ? f.append(k, v, filename) : f.append(k, v);
  return f;
};

export const api = {
  health: () => req<Health>("/api/health"),
  speakers: () => req<Record<string, Speaker>>("/api/speakers"),
  enroll: (slot: string, name: string, file: Blob) =>
    req<Speaker>(`/api/speakers/${slot}`, { method: "POST", body: form({ name, file }) }),
  sessions: () => req<Session[]>("/api/sessions"),
  session: (id: string) => req<Session>(`/api/sessions/${id}`),
  createSession: (file: Blob, label: string) =>
    req<Session>("/api/sessions", { method: "POST", body: form({ file, label }) }),
  extract: (id: string, speaker: string) =>
    req<Extraction>(`/api/sessions/${id}/extract`, { method: "POST", body: form({ speaker }) }),
  save: (id: string, title: string) =>
    req<Session["saved"]>(`/api/sessions/${id}/save`, { method: "POST", body: form({ title }) }),
  search: (q: string, speaker: string, session: string, mode: string) =>
    req<SearchResult>(`/api/search?${new URLSearchParams({ q, speaker, session, mode })}`),
  transcript: (id: string) => req<{ conversation_id: string; segments: Hit[] }>(`/api/sessions/${id}/transcript`),
  facets: () => req<{ speakers: string[]; conversations: { id: string; title: string; segments: number }[] }>("/api/search/facets"),
};

export const audioUrl = (id: string, kind: string) => `/api/sessions/${id}/audio/${kind}`;
