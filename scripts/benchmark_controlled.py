"""Controlled target-speaker-extraction benchmark on Mini LibriSpeech dev-clean-2.

For each (target, interferer) speaker pair:
  * enrollment = a target utterance NOT used in the mixture
  * overlap_0dB      : full overlap, target/interferer at equal energy
  * louder_intf_-5dB : interferer 5 dB louder than target
  * target_silent    : interferer alone for the first ~3 s, then both overlap (0 dB)

Clean references are used ONLY for scoring, never passed to the model.
Writes results JSON + sample WAVs under bench/.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
import time

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from soundbubble.engine import TSEEngine, hardware_summary, MODEL_SR  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
CORPUS = os.path.join(ROOT, "data/corpus/LibriSpeech/dev-clean-2")


def si_sdr(est: np.ndarray, ref: np.ndarray) -> float:
    est = est - est.mean()
    ref = ref - ref.mean()
    alpha = np.dot(est, ref) / (np.dot(ref, ref) + 1e-8)
    proj = alpha * ref
    noise = est - proj
    return float(10 * np.log10((np.sum(proj**2) + 1e-8) / (np.sum(noise**2) + 1e-8)))


def rms(x):
    return float(np.sqrt(np.mean(x**2) + 1e-12))


def utterances(spk: str):
    files = sorted(glob.glob(os.path.join(CORPUS, spk, "*", "*.flac")))
    out = []
    for f in files:
        a, sr = sf.read(f, dtype="float32")
        assert sr == MODEL_SR
        if len(a) >= 6 * sr:
            out.append((f, a))
    return out


def build_cases(tgt, intf, seconds=8.0):
    n = int(seconds * MODEL_SR)
    t = tgt[:n]
    i = intf[:n]
    n = min(len(t), len(i))
    t, i = t[:n], i[:n]
    cases = {}
    for name, sir in (("overlap_0dB", 0.0), ("louder_intf_-5dB", -5.0)):
        g = rms(t) / rms(i) / (10 ** (sir / 20))
        ii = i * g
        cases[name] = (t + ii, t, ii, None)
    # Target silent for first `sil` seconds
    sil = int(3.0 * MODEL_SR)
    t2 = np.concatenate([np.zeros(sil, np.float32), t[: n - sil]])
    g = rms(t[: n - sil]) / rms(i[sil:])
    ii = i * g
    cases["target_silent"] = (t2 + ii, t2, ii, sil)
    # normalise peaks
    out = {}
    for k, (m, tt, iii, s) in cases.items():
        p = np.abs(m).max() / 0.9
        out[k] = (m / p, tt / p, iii / p, s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+",
                    default=["models/spk_emb_causal_100", "models/spk_emb_100"])
    ap.add_argument("--pairs", type=int, default=6)
    ap.add_argument("--out", default="bench")
    args = ap.parse_args()

    random.seed(0)
    spks = sorted(os.listdir(CORPUS))
    pairs = []
    while len(pairs) < args.pairs:
        a, b = random.sample(spks, 2)
        if (a, b) not in pairs:
            pairs.append((a, b))
    utt_cache = {}

    def get(spk):
        if spk not in utt_cache:
            utt_cache[spk] = utterances(spk)
        return utt_cache[spk]

    os.makedirs(os.path.join(ROOT, args.out, "samples"), exist_ok=True)
    report = {"hardware": hardware_summary(), "corpus": "Mini LibriSpeech dev-clean-2",
              "time": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "models": {}}

    for mdir in args.models:
        eng = TSEEngine(os.path.join(ROOT, mdir))
        rows = []
        print(f"== {eng.name} loaded in {eng.load_seconds:.1f}s", flush=True)
        for pi, (ts, isp) in enumerate(pairs):
            tu, iu = get(ts), get(isp)
            if len(tu) < 2 or len(iu) < 1:
                continue
            enroll_f, enroll = tu[0]
            tgt_f, tgt = tu[1]
            intf_f, intf = iu[0]
            for cname, (mix, ref, iref, sil) in build_cases(tgt, intf).items():
                res = eng.extract(mix, MODEL_SR, enroll[: 8 * MODEL_SR], MODEL_SR)
                y = res.audio[: len(mix)]
                row = {"pair": f"{ts}<-{isp}", "case": cname, "enroll": os.path.basename(enroll_f),
                       "target_utt": os.path.basename(tgt_f), "interferer_utt": os.path.basename(intf_f),
                       "rtf": res.rtf, "proc_s": res.processing_seconds, "audio_s": res.audio_seconds,
                       "speaker_margin": res.speaker_margin,
                       "wrong_speaker_suspected": res.wrong_speaker_suspected}
                if sil is None:
                    row["si_sdr_mix"] = si_sdr(mix, ref)
                    row["si_sdr_out"] = si_sdr(y, ref)
                    row["si_sdr_out_vs_interferer"] = si_sdr(y, iref)
                else:
                    a = slice(sil, None)
                    row["si_sdr_mix"] = si_sdr(mix[a], ref[a])
                    row["si_sdr_out"] = si_sdr(y[a], ref[a])
                    # Leakage while target is silent: output energy relative to mixture energy
                    # (scale-invariant: normalise output to match target-active segment gain)
                    alpha = np.dot(y[a], ref[a]) / (np.dot(ref[a], ref[a]) + 1e-8)
                    row["silent_region_leak_db"] = 20 * np.log10(
                        rms(y[:sil] / (alpha if abs(alpha) > 1e-4 else 1.0)) / rms(mix[:sil]))
                row["si_sdr_i"] = row["si_sdr_out"] - row["si_sdr_mix"]
                rows.append(row)
                print(json.dumps({k: (round(v, 2) if isinstance(v, float) else v)
                                  for k, v in row.items()}), flush=True)
                if pi < 2:
                    base = os.path.join(ROOT, args.out, "samples", f"{eng.name}_{pi}_{cname}")
                    sf.write(base + "_mix.wav", mix, MODEL_SR)
                    sf.write(base + "_out.wav", y, MODEL_SR)
                    if mdir == args.models[0]:
                        sf.write(base + "_ref.wav", ref, MODEL_SR)
        summ = {}
        for c in {r["case"] for r in rows}:
            rr = [r for r in rows if r["case"] == c]
            summ[c] = {
                "n": len(rr),
                "mean_si_sdr_i": float(np.mean([r["si_sdr_i"] for r in rr])),
                "min_si_sdr_i": float(np.min([r["si_sdr_i"] for r in rr])),
                "failures_si_sdr_i_lt_3dB": sum(r["si_sdr_i"] < 3 for r in rr),
                "mean_rtf": float(np.mean([r["rtf"] for r in rr])),
            }
            if c == "target_silent":
                summ[c]["mean_silent_leak_db"] = float(np.mean([r["silent_region_leak_db"] for r in rr]))
        report["models"][eng.name] = {"load_s": eng.load_seconds, "summary": summ, "rows": rows}
        print(json.dumps(summ, indent=1), flush=True)
        del eng

    with open(os.path.join(ROOT, args.out, "controlled_results.json"), "w") as f:
        json.dump(report, f, indent=1)


if __name__ == "__main__":
    main()
