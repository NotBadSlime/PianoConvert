from __future__ import annotations

torch = None  # patched in tests


def resolve_device() -> str:
    global torch
    if torch is None:
        import torch as _torch
        torch = _torch
    return "cuda" if torch.cuda.is_available() else "cpu"
