from pathlib import Path

from app.device import resolve_device
from app.paths import history_path, make_output_dir, model_checkpoint, output_root


def test_output_root_ends_with_pianoconvert_output(tmp_path, monkeypatch):
    monkeypatch.setattr("app.paths.Path.home", classmethod(lambda cls: tmp_path))
    root = output_root()
    assert root == tmp_path / "Documents" / "PianoConvert" / "Output"


def test_history_path_under_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert history_path() == tmp_path / "PianoConvert" / "history.json"


def test_make_output_dir_uses_stem_and_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr("app.paths.output_root", lambda: tmp_path)
    monkeypatch.setattr("app.paths._now_stamp", lambda: "20260102-030405")
    folder = make_output_dir(Path("song name.mp3"))
    assert folder.name == "song name_20260102-030405"
    assert folder.is_dir()


def test_model_checkpoint_prefers_repo_models(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    pth = models / "note_F1=0.9677_pedal_F1=0.9186.pth"
    pth.write_bytes(b"x")
    monkeypatch.setattr("app.paths.repo_root", lambda: tmp_path)
    assert model_checkpoint() == pth


def test_resolve_device_cpu_when_cuda_false(monkeypatch):
    import app.device as device_mod

    class _Cuda:
        @staticmethod
        def is_available():
            return False

    class _Torch:
        cuda = _Cuda()

    monkeypatch.setattr(device_mod, "torch", _Torch())
    assert device_mod.resolve_device() == "cpu"
