"""Explicit resource preparation; never called by application startup. No .env."""

import hashlib
import importlib.metadata
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "tools/transcription"


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    packages = DEST / "packages"
    packages.mkdir(exist_ok=True)
    metadata = json.load(urllib.request.urlopen("https://pypi.org/pypi/transkun/2.0.1/json"))
    item = next(x for x in metadata["urls"] if x["filename"] == "transkun-2.0.1-py3-none-any.whl")
    wheel = packages / item["filename"]
    if not wheel.exists():
        urllib.request.urlretrieve(item["url"], wheel)
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != item["digests"]["sha256"]:
        raise RuntimeError("Official PyPI wheel checksum mismatch")
    source = DEST / "source"
    source.mkdir(exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        for entry in archive.infolist():
            if not (source / entry.filename).resolve().is_relative_to(source.resolve()):
                raise RuntimeError("Unsafe archive member")
        archive.extractall(source)
    pretrained = source / "transkun/pretrained"
    for name in ("2.0.pt", "2.0.conf"):
        shutil.copyfile(pretrained / name, DEST / name)
    url = "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/5c90dfd2-34c22ccb.th"
    weight = DEST / "5c90dfd2-34c22ccb.th"
    if not weight.exists():
        temporary = weight.with_suffix(".download")
        urllib.request.urlretrieve(url, temporary)
        if not hashlib.sha256(temporary.read_bytes()).hexdigest().startswith("34c22ccb"):
            raise RuntimeError("Demucs official filename checksum mismatch")
        temporary.replace(weight)
    assets = []
    source_files = sorted(str(p.relative_to(DEST)).replace("\\", "/") for p in source.rglob("*.py"))
    for name in ("2.0.pt", "2.0.conf", weight.name, *source_files):
        path = DEST / name
        assets.append(
            {
                "file": name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )
    manifest = {
        "schema": "neonote-transcription-resources-v1",
        "device": "cpu",
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("torch", "torchaudio", "demucs", "soundfile")
        },
        "transkun": {
            "source": item["url"],
            "version": "2.0.1",
            "wheel_sha256": item["digests"]["sha256"],
            "license": "MIT",
            "checkpoint": "V2 No Pedal Ext (official 2.0.1 package)",
            "note_end": "key release, not pedal-extended",
            "installation": "unmodified official source, inference dependencies only; ncls training omitted",
        },
        "separator": {
            "source": url,
            "license": "MIT",
            "model": "htdemucs_6s",
            "stem": "piano",
            "warning": "Experimental piano stem; bleeding and artifacts possible",
        },
        "assets": assets,
    }
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
