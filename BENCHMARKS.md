# Benchmarks

Measured 2026-11-09 14:33:54 MST on Intel(R) Core(TM) Ultra 9 285H, 16 torch threads, CUDA: False, PyTorch 2.7.1+cpu.

## Controlled mixtures (Mini LibriSpeech dev-clean-2)

Six random speaker pairs, 8 s mixtures at 16 kHz. Enrollment = a different utterance of the target speaker (≤ 8 s). Clean references are used **only** for scoring (SI-SDR); they are never given to the model. Script: `scripts/benchmark_controlled.py`.

* `overlap_0dB`: full overlap, equal energy.
* `louder_intf_-5dB`: interferer 5 dB louder than the target.
* `target_silent`: interferer alone for the first 3 s; SI-SDR measured on the target-active part; *silent leak* = residual output level during the first 3 s relative to the mixture (lower is better).

### Summary

| Model | Case | Mean SI-SDRi (dB) | Worst (dB) | Wrong voice (SI-SDRi < 3 dB) | Mean RTF | Silent leak (dB) |
|---|---|---|---|---|---|---|
| spk_emb_causal_100 | overlap_0dB | 8.9 | -19.8 | 1/6 | 0.93 | – |
| spk_emb_causal_100 | louder_intf_-5dB | 14.2 | -2.5 | 1/6 | 0.96 | – |
| spk_emb_causal_100 | target_silent | 11.6 | -4.0 | 1/6 | 0.96 | -29.2 |
| spk_emb_100 | overlap_0dB | 18.9 | 17.4 | 0/6 | 1.41 | – |
| spk_emb_100 | louder_intf_-5dB | 21.3 | 19.7 | 0/6 | 1.39 | – |
| spk_emb_100 | target_silent | 11.1 | -25.6 | 1/6 | 1.38 | -32.5 |

RTF = processing time ÷ audio duration for offline file processing (CPU, whole file at once). It is **not** a listening delay. A separate smoke run of the causal model measured RTF 0.67–0.78; RTF varies between runs on this laptop.

### Per-pair results

| Model | Pair (target ← interferer) | Case | Mixture SI-SDR | Output SI-SDR | SI-SDRi |
|---|---|---|---|---|---|
| spk_emb_causal_100 | 3576<-84 | overlap_0dB | -0.0 | -19.8 | -19.8 |
| spk_emb_causal_100 | 3576<-84 | louder_intf_-5dB | -5.1 | -7.6 | -2.5 |
| spk_emb_causal_100 | 3576<-84 | target_silent | -0.1 | -4.1 | -4.0 |
| spk_emb_causal_100 | 3752<-1462 | overlap_0dB | 0.1 | 15.2 | 15.1 |
| spk_emb_causal_100 | 3752<-1462 | louder_intf_-5dB | -4.9 | 13.7 | 18.6 |
| spk_emb_causal_100 | 3752<-1462 | target_silent | 0.1 | 18.6 | 18.5 |
| spk_emb_causal_100 | 251<-5895 | overlap_0dB | -0.0 | 12.2 | 12.2 |
| spk_emb_causal_100 | 251<-5895 | louder_intf_-5dB | -5.0 | 10.6 | 15.7 |
| spk_emb_causal_100 | 251<-5895 | target_silent | -0.0 | 14.7 | 14.7 |
| spk_emb_causal_100 | 5694<-3576 | overlap_0dB | -0.0 | 14.2 | 14.3 |
| spk_emb_causal_100 | 5694<-3576 | louder_intf_-5dB | -5.1 | 12.0 | 17.1 |
| spk_emb_causal_100 | 5694<-3576 | target_silent | 0.1 | 13.5 | 13.5 |
| spk_emb_causal_100 | 8842<-2803 | overlap_0dB | -0.0 | 16.9 | 16.9 |
| spk_emb_causal_100 | 8842<-2803 | louder_intf_-5dB | -5.1 | 14.2 | 19.3 |
| spk_emb_causal_100 | 8842<-2803 | target_silent | 0.2 | 14.8 | 14.7 |
| spk_emb_causal_100 | 5694<-3536 | overlap_0dB | -0.0 | 14.5 | 14.5 |
| spk_emb_causal_100 | 5694<-3536 | louder_intf_-5dB | -5.0 | 12.1 | 17.1 |
| spk_emb_causal_100 | 5694<-3536 | target_silent | 0.0 | 12.0 | 12.0 |
| spk_emb_100 | 3576<-84 | overlap_0dB | -0.0 | 20.0 | 20.1 |
| spk_emb_100 | 3576<-84 | louder_intf_-5dB | -5.1 | 17.7 | 22.7 |
| spk_emb_100 | 3576<-84 | target_silent | -0.1 | -25.7 | -25.6 |
| spk_emb_100 | 3752<-1462 | overlap_0dB | 0.1 | 20.7 | 20.6 |
| spk_emb_100 | 3752<-1462 | louder_intf_-5dB | -4.9 | 18.0 | 22.9 |
| spk_emb_100 | 3752<-1462 | target_silent | 0.1 | 21.8 | 21.7 |
| spk_emb_100 | 251<-5895 | overlap_0dB | -0.0 | 18.3 | 18.4 |
| spk_emb_100 | 251<-5895 | louder_intf_-5dB | -5.0 | 15.8 | 20.9 |
| spk_emb_100 | 251<-5895 | target_silent | -0.0 | 17.2 | 17.3 |
| spk_emb_100 | 5694<-3576 | overlap_0dB | -0.0 | 17.4 | 17.4 |
| spk_emb_100 | 5694<-3576 | louder_intf_-5dB | -5.1 | 14.7 | 19.7 |
| spk_emb_100 | 5694<-3576 | target_silent | 0.1 | 17.3 | 17.3 |
| spk_emb_100 | 8842<-2803 | overlap_0dB | -0.0 | 19.5 | 19.6 |
| spk_emb_100 | 8842<-2803 | louder_intf_-5dB | -5.1 | 16.9 | 22.0 |
| spk_emb_100 | 8842<-2803 | target_silent | 0.2 | 18.0 | 17.8 |
| spk_emb_100 | 5694<-3536 | overlap_0dB | -0.0 | 17.3 | 17.4 |
| spk_emb_100 | 5694<-3536 | louder_intf_-5dB | -5.0 | 14.8 | 19.8 |
| spk_emb_100 | 5694<-3536 | target_silent | 0.0 | 18.3 | 18.3 |

### Wrong-voice failures

All failures involve the same pair (3576 ← 84). The model separates the two voices cleanly but returns the interferer: in the causal 0 dB case the output scores −19.8 dB against the target and +11.0 dB against the interferer. The non-causal model gets this pair right under overlap (+20 dB) but keeps the wrong voice when the target starts silent (−25.6 dB SI-SDRi).

### Speaker-match check (non-causal model, re-run)

Margin = cos(output, enrollment) − cos(removed audio, enrollment) using the model's own ECAPA encoder; warning when margin < 0.05.

| Pair | Case | SI-SDRi | Margin | Warning |
|---|---|---|---|---|
| 3576<-84 | overlap_0dB | 20.1 | 0.41 | False |
| 3576<-84 | louder_intf_-5dB | 22.7 | 0.38 | False |
| 3576<-84 | target_silent | -26.7 | -0.23 | True |
| 3752<-1462 | overlap_0dB | 20.6 | 0.93 | False |
| 3752<-1462 | louder_intf_-5dB | 22.9 | 0.93 | False |
| 3752<-1462 | target_silent | 21.7 | 0.86 | False |
| 251<-5895 | overlap_0dB | 18.4 | 0.55 | False |
| 251<-5895 | louder_intf_-5dB | 20.9 | 0.52 | False |
| 251<-5895 | target_silent | 17.3 | 0.48 | False |
| 5694<-3576 | overlap_0dB | 17.4 | 0.89 | False |
| 5694<-3576 | louder_intf_-5dB | 19.7 | 0.87 | False |
| 5694<-3576 | target_silent | 17.3 | 0.88 | False |
| 8842<-2803 | overlap_0dB | 19.6 | 0.75 | False |
| 8842<-2803 | louder_intf_-5dB | 22.0 | 0.7 | False |
| 8842<-2803 | target_silent | 17.8 | 0.68 | False |
| 5694<-3536 | overlap_0dB | 17.4 | 0.85 | False |
| 5694<-3536 | louder_intf_-5dB | 19.8 | 0.84 | False |
| 5694<-3536 | target_silent | 18.3 | 0.89 | False |

Result: 1 wrong-voice output among 18, flagged; 0 false warnings. (Small sample; treat as indicative.)

## Fresh recordings (two speakers, laptop microphone, 48 kHz)

Recorded 15:24–15:26 MT: a 25 s solo clip of each speaker ("Alice", "Bob") and a 45.9 s "Both" take.
References = first 10 s of speech of each solo clip (separate takes from the mixture).
Speaker-encoder check: Alice vs Bob cosine 0.21 (distinct); each clip self-consistent at 0.85.

### A. Real voices, controlled overlap (genuine clean references)

The unused 13 s of each solo clip (11–24 s) summed digitally. These are real laptop-mic voices
with true references, scored by SI-SDR; references are never given to the model.
(`bench/realmic_synthetic_results.json`)

| Model | Alice, 0 dB | Bob, 0 dB | Alice, other +5 dB | Bob, other +5 dB |
|---|---|---|---|---|
| tfmap_context_100 | +16.3 | +16.4 | +19.0 | +18.5 |
| spk_emb_100 | +15.6 | +15.4 | +18.1 | +17.5 |
| tfmap_context_causal_100 | +14.1 | +11.0 | +16.5 | +12.5 |
| spk_emb_causal_100 | **−13.7** (wrong voice) | +8.8 | +2.8 | +7.0 |

An 80 Hz high-pass on input cut every model to +3–11 dB, so we do not apply one.
The two-speaker gate (below) leaves these numbers unchanged.

### B. The real "Both" recording (acoustic mixing in the room, mostly turn-taking)

There is no clean reference for real overlapping speech, so we report **selectivity**: level
change of the target's own-voice windows minus that of the other speaker's windows, over 2 s
windows labelled by speaker-encoder similarity on the mixture (first 26 s: 5 Alice-dominant,
4 Bob-dominant windows). Approximate labels; indicative only.

| Configuration | Target Alice | Target Bob |
|---|---|---|
| spk_emb_100 (first default) | ~0 dB, the output equals the mixture | ~0 dB |
| spk_emb_causal_100 | 5.2 dB | 8.1 dB (but Bob's own voice −16.6 dB) |
| tfmap_context_100 | 9.9 dB | −0.3 dB |
| **tfmap_context_100 + two-speaker gate (current default)** | **9.9 dB** | **20.8 dB** |

**Finding:** the separators work on overlapping speech but often pass a lone voice through
during turn-taking regardless of the reference (the same weakness as LibriSpeech
`target_silent`). The two-speaker gate uses both enrollments: 1.5 s windows, 0.25 s hop, mute
(−30 dB floor) where the output sounds more like the other enrolled voice than the target.
It only works when the other speaker is enrolled too.
Residual: Alice's output still carries Bob at about −10 dB; ASR on Alice's track picked up one
short Bob phrase ("I'm sleeping", 4.3 s).

Timing on the full 45.9 s take (app path, CPU): Alice 80.0 s (separator 76.2 s + gate 3.8 s,
RTF 1.74), Bob 81.3 s (RTF 1.77). On 26 s excerpts: RTF 1.25–1.55. File processing only; not a
listening delay.

## Search latency

Local Elasticsearch 8.15.3 (single node, 512 MB heap): query `took` 16–71 ms over test segments. Save (ASR `base.en` int8 on ~4 s × 2 voices + indexing): 2.5 s warm.
