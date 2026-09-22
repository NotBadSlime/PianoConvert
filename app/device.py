from __future__ import annotations

torch = None  # patched in tests


class DeviceError(RuntimeError):
    pass


def _torch():
    global torch
    if torch is None:
        import torch as _torch

        torch = _torch
    return torch


def cuda_available() -> bool:
    try:
        return bool(_torch().cuda.is_available())
    except Exception:
        return False


def resolve_device(preference: str = "auto") -> str:
    available = cuda_available()
    if preference == "cpu":
        return "cpu"
    if preference == "cuda":
        if not available:
            raise DeviceError("没有可用的 NVIDIA GPU，请改用 CPU。")
        return "cuda"
    if preference != "auto":
        raise DeviceError("未知的计算设备。")
    return "cuda" if available else "cpu"
