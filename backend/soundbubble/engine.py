"""Target-speaker extraction engine wrapping the REAL-TSE / WeSep BSRNN checkpoints.

Loads a model once and keeps it in memory. Accepts numpy audio at any sample
rate, resamples to the model rate (16 kHz), and returns timing measurements.
"""
from __future__ import annotations

import os
import platform
import threading
import time
from dataclasses import dataclass, asdict

import numpy as np
import torch
import torchaudio

MODEL_SR = 16000


@dataclass
class ExtractionResult:
    audio: np.ndarray          # float32 mono at MODEL_SR
    sample_rate: int
    model: str
    audio_seconds: float
    processing_seconds: float
    rtf: float                 # processing_seconds / audio_seconds
    sim_output: float | None = None
    sim_residual: float | None = None
    speaker_margin: float | None = None
    wrong_speaker_suspected: bool | None = None
    check_error: str | None = None
    separator_seconds: float | None = None
    gate_seconds: float | None = None
    gate_windows: int = 0
    gate_muted_windows: int = 0

    def meta(self) -> dict:
        d = asdict(self)
        d.pop("audio")
        return d


def hardware_summary() -> dict:
    cpu = platform.processor() or platform.machine()
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    return {
        "cpu": cpu,
        "torch_threads": torch.get_num_threads(),
        "cuda": torch.cuda.is_available(),
        "torch": torch.__version__,
    }


def to_mono_float(audio: np.ndarray) -> np.ndarray:
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 2:
        # soundfile gives (frames, channels)
        a = a.mean(axis=1)
    return a


def resample(audio: np.ndarray, sr: int, target: int = MODEL_SR) -> np.ndarray:
    if sr == target:
        return audio.astype(np.float32)
    t = torch.from_numpy(audio.astype(np.float32)).unsqueeze(0)
    return torchaudio.functional.resample(t, sr, target).squeeze(0).numpy()


class TSEEngine:
    def __init__(self, model_dir: str, device: str = "cpu"):
        # Import lazily so the API can start (and report) even if wesep is missing.
        import wesep  # noqa: WPS433

        self.model_dir = model_dir
        self.name = os.path.basename(os.path.normpath(model_dir))
        t0 = time.perf_counter()
        self._ex = wesep.load_model_local(model_dir)
        self._ex.set_resample_rate(MODEL_SR)
        self._ex.set_vad(False)
        self._ex.set_device(device)
        self._ex.set_output_norm(False)
        self.load_seconds = time.perf_counter() - t0
        self._lock = threading.Lock()

    def attach_speaker_encoder(self, spk_model_dir: str):
        """Use the ECAPA encoder from a spk_emb checkpoint for gating/checks.

        Needed when the separation model (e.g. tfmap_context) has no speaker-embedding branch.
        """
        import wesep
        if hasattr(self._ex.model.spk_ft, "spkemb"):
            self._spk = self._ex.model.spk_ft.spkemb
        else:
            ex = wesep.load_model_local(spk_model_dir)
            self._spk = ex.model.spk_ft.spkemb
            self._spk_holder = ex  # keep alive
        self._spk.eval()

    def gate(self, y: np.ndarray, target: np.ndarray, others: list[np.ndarray],
             win_s: float = 1.5, hop_s: float = 0.25, floor_db: float = -30.0) -> tuple[np.ndarray, dict]:
        """Two-speaker gate: attenuate output regions that sound more like another enrolled
        speaker than the target. Handles turn-taking, where the separator passes a lone
        voice through regardless of the reference. Overlap regions are left to the model."""
        e_t = self._embed(target)
        e_o = [self._embed(o) for o in others]
        win, hop = int(win_s * MODEL_SR), int(hop_s * MODEL_SR)
        n = len(y)
        starts = list(range(0, max(1, n - win + 1), hop))
        g_acc = np.zeros(n, np.float32)
        w_acc = np.zeros(n, np.float32)
        floor = 10 ** (floor_db / 20)
        muted = 0
        for s in starts:
            seg = y[s:s + win]
            if np.sqrt(np.mean(seg ** 2)) < 1e-4:
                g = 1.0
            else:
                e = self._embed(seg)
                st = float(torch.dot(e, e_t))
                so = max(float(torch.dot(e, x)) for x in e_o)
                # margin > +0.05: keep; < -0.05: mute; linear in between
                g = float(np.clip((st - so + 0.05) / 0.10, 0.0, 1.0))
                g = floor + (1 - floor) * g
                muted += g < 0.5
            w = np.hanning(len(seg)).astype(np.float32) + 1e-3
            g_acc[s:s + len(seg)] += g * w
            w_acc[s:s + len(seg)] += w
        gain = np.where(w_acc > 0, g_acc / np.maximum(w_acc, 1e-6), 1.0).astype(np.float32)
        return y * gain, {"gate_windows": len(starts), "gate_muted_windows": int(muted)}

    def extract(self, mixture: np.ndarray, mix_sr: int, enroll: np.ndarray,
                enroll_sr: int, others: list[tuple[np.ndarray, int]] | None = None) -> ExtractionResult:
        mix = resample(to_mono_float(mixture), mix_sr)
        enr = resample(to_mono_float(enroll), enroll_sr)
        oth = [resample(to_mono_float(a), sr) for a, sr in (others or [])]
        if len(mix) < MODEL_SR // 2:
            raise ValueError("Mixture is shorter than 0.5 s")
        if len(enr) < MODEL_SR:
            raise ValueError("Enrollment is shorter than 1 s")
        with self._lock:
            t0 = time.perf_counter()
            out = self._ex.extract_speech_from_pcm(
                torch.from_numpy(mix).unsqueeze(0), MODEL_SR,
                torch.from_numpy(enr).unsqueeze(0), MODEL_SR)
            dt = time.perf_counter() - t0
        y = out[0].numpy().astype(np.float32)[: len(mix)]
        if len(y) < len(mix):
            y = np.pad(y, (0, len(mix) - len(y)))
        gate_info: dict = {"gate_windows": 0, "gate_muted_windows": 0}
        t1 = time.perf_counter()
        if oth:
            y, gate_info = self.gate(y, enr, oth)
        gate_s = time.perf_counter() - t1
        conf = self.speaker_check(mix, y, enr)
        # Match output loudness to the mixture peak, never clip.
        peak = float(np.abs(y).max()) or 1.0
        y = y / peak * min(0.95, float(np.abs(mix).max()) or 0.95)
        secs = len(mix) / MODEL_SR
        total = dt + gate_s
        return ExtractionResult(y, MODEL_SR, self.name, secs, total, total / secs, **conf,
                                separator_seconds=dt, gate_seconds=gate_s, **gate_info)

    def _embed(self, wav: np.ndarray) -> torch.Tensor:
        spk = getattr(self, "_spk", None) or self._ex.model.spk_ft.spkemb
        with torch.no_grad():
            e = spk.compute(torch.from_numpy(wav.astype(np.float32)).unsqueeze(0))
        return torch.nn.functional.normalize(e.reshape(1, -1), dim=-1)[0]

    def speaker_check(self, mix: np.ndarray, out: np.ndarray, enroll: np.ndarray) -> dict:
        """Compare the extracted voice and the removed residual against the enrollment.

        Uses the model's own speaker encoder. If the residual is closer to the enrolled
        voice than the output is, the model probably kept the wrong speaker.
        """
        try:
            # scale-match output to the mixture before computing the residual
            alpha = float(np.dot(out, mix) / (np.dot(out, out) + 1e-8))
            resid = mix - alpha * out
            e_en = self._embed(enroll)
            s_out = float(torch.dot(self._embed(out), e_en))
            s_res = float(torch.dot(self._embed(resid), e_en))
            return {"sim_output": s_out, "sim_residual": s_res,
                    "speaker_margin": s_out - s_res,
                    "wrong_speaker_suspected": bool(s_out - s_res < 0.05)}
        except Exception as exc:  # never break extraction because of the check
            return {"sim_output": None, "sim_residual": None, "speaker_margin": None,
                    "wrong_speaker_suspected": None, "check_error": str(exc)}
