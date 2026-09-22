"""Run by the downloaded GPU Python, not by the installed CPU app."""

from __future__ import annotations

import sys


def _piano(audio: str, midi: str, checkpoint: str) -> None:
    from piano_transcription_inference import PianoTranscription

    PianoTranscription(device="cuda", checkpoint_path=checkpoint).transcribe(audio, midi)


def _other(audio: str, midi: str) -> None:
    import basic_pitch.inference as bp_inf
    import onnxruntime as ort
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict as bp_predict

    chosen = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("没有可用的 NVIDIA GPU，或 GPU 组件未装好。")
    real = bp_inf.ort.InferenceSession

    def _session(path, providers=None, **kwargs):
        return real(path, providers=chosen, **kwargs)

    bp_inf.ort.InferenceSession = _session
    try:
        _model_out, midi_data, _notes = bp_predict(audio, ICASSP_2022_MODEL_PATH)
    finally:
        bp_inf.ort.InferenceSession = real
    midi_data.write(midi)


def main() -> None:
    kind, audio, midi = sys.argv[1], sys.argv[2], sys.argv[3]
    print("PROGRESS\t0.45\t正在用 GPU 转录", flush=True)
    if kind == "piano":
        _piano(audio, midi, sys.argv[4])
    elif kind == "other":
        _other(audio, midi)
    else:
        raise RuntimeError("未知的转换类型")
    print("PROGRESS\t0.65\tGPU 转录完成", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR\t{exc}", flush=True)
        raise SystemExit(1)
