"""Fingerprint installed official assets, dictionaries and the pinned worker, without .env."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "tools/vocals"


def file_hash(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main():
    groups = {
        "singer": ASSETS / "singers/Qixuan_v2.7.0_DiffSinger_OpenUtau",
        "vocoder": ASSETS / "Dependencies/pc_nsf_hifigan_44.1k_hop512_128bin_2025.02",
        "engine": ASSETS / "worker",
    }
    files = {}
    for group, directory in groups.items():
        if not directory.is_dir():
            raise SystemExit(f"Missing official asset: {group}")
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                files[path.relative_to(ASSETS).as_posix()] = {
                    "group": group,
                    "sha256": file_hash(path),
                    "bytes": path.stat().st_size,
                }
    probe = json.loads((ASSETS / "gpu-probe.json").read_text(encoding="utf-8-sig"))
    manifest = {
        "singer_id": "qixuan-2.7.0",
        "singer_name": "绮萱 Qixuan v2.7.0",
        "vocoder": "PC-NSF-HiFiGAN 2025.02",
        "openutau": "0.1.573-beta",
        "protocol": "neonote-vocal-v1",
        "protocols": ["neonote-vocal-v1", "neonote-vocal-v2"],
        "onnx": probe["onnx"],
        "devices": probe["devices"],
        "files": files,
        "attribution": "AI合成演唱：绮萱 / DiffSinger；社区声码器 OpenVPI；非商业演示",
        "sources": [
            "https://github.com/yqzhishen/qixuan-diffsinger/releases/tag/v2.7.0",
            "https://github.com/openvpi/vocoders/releases/tag/pc-nsf-hifigan-44.1k-hop512-128bin-2025.02",
            "https://github.com/openutau/OpenUtau/releases/tag/0.1.573-beta",
        ],
    }
    (ASSETS / "resources.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Pinned {len(files)} vocal resource files; no Python packages or model API requests.")


if __name__ == "__main__":
    main()
