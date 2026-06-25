from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


VERSION_RE = re.compile(r"^v?\d+(?:\.\d+){1,3}(?:[-.][A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?$")


@dataclass(frozen=True)
class ReleaseVersion:
    tag: str
    plain: str


def resolve_release_version(event_name: str, ref_name: str, input_version: str | None) -> ReleaseVersion:
    raw_version = input_version if event_name == "workflow_dispatch" else ref_name
    version = (raw_version or "").strip()
    if not version:
        raise ValueError("Release version is required.")
    if not VERSION_RE.match(version):
        raise ValueError(f"Invalid release version: {version}")

    tag = version if version.startswith("v") else f"v{version}"
    return ReleaseVersion(tag=tag, plain=tag[1:])


def write_github_output(path: Path, version: ReleaseVersion) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"tag={version.tag}\n")
        handle.write(f"plain={version.plain}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--ref-name", required=True)
    parser.add_argument("--input-version", default="")
    parser.add_argument("--github-output", default="")
    args = parser.parse_args()

    version = resolve_release_version(args.event_name, args.ref_name, args.input_version)
    if args.github_output:
        write_github_output(Path(args.github_output), version)
    else:
        print(f"tag={version.tag}")
        print(f"plain={version.plain}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
