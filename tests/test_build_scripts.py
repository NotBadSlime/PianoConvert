import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_build_exe_fails_fast_when_required_runtime_dependency_is_missing(tmp_path):
    fake_python = tmp_path / "fake_python.cmd"
    fake_python.write_text(
        "\n".join(
            [
                "@echo off",
                "echo %* | findstr /C:\"PySide6\" >nul",
                "if %ERRORLEVEL%==0 exit /b 7",
                "exit /b 0",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/build_exe.ps1",
            "-Python",
            str(fake_python),
        ],
        cwd=ROOT,
        capture_output=True,
    )

    combined_output = (result.stdout + result.stderr).decode("utf-8", errors="replace")

    assert result.returncode != 0
    assert "Build dependencies are not installed" in combined_output
