from app.paths import default_output_dir, resource_path


def test_resource_path_resolves_relative_to_project_root():
    path = resource_path("piano_transcription_inference_data")

    assert path.name == "piano_transcription_inference_data"
    assert path.is_absolute()


def test_default_output_dir_uses_documents_pianoconvert(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    path = default_output_dir()

    assert path == tmp_path / "Documents" / "PianoConvert" / "Output"
