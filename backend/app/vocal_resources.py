"""Explicit local resources; this module never loads model/.env settings."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .resources import ROOT, AudioError
from .vocal_score import PROTOCOL, fingerprint


@dataclass(frozen=True)
class VocalPaths:
    root: Path = ROOT / "tools/vocals"

    @property
    def dotnet(self):
        return self.root / "dotnet/dotnet.exe"

    @property
    def worker(self):
        return self.root / "worker/NeoNote.VocalWorker.dll"


class VocalResource:
    def __init__(self, paths: VocalPaths | None = None):
        self.paths = paths or VocalPaths()
        self._stamp = None
        self._info = None

    def info(self):
        root = self.paths.root.resolve()
        manifest_path = root / "resources.json"
        if not all(p.is_file() for p in (manifest_path, self.paths.dotnet, self.paths.worker)):
            raise AudioError(
                "演唱资源未准备好，请运行人声资源准备和构建脚本；不会用器乐代替演唱。", 503
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["protocol"] != PROTOCOL or not manifest["files"]:
                raise ValueError
            checked = []
            for relative, entry in manifest["files"].items():
                path = root / relative
                if (
                    not path.resolve().is_relative_to(root)
                    or path.is_symlink()
                    or any(p.is_symlink() for p in path.parents)
                ):
                    raise ValueError
                stat = path.stat()
                if stat.st_size != entry["bytes"]:
                    raise ValueError
                checked.append((relative, stat.st_size, stat.st_mtime_ns))
            stamp = (manifest_path.stat().st_mtime_ns, tuple(checked))
            if self._stamp == stamp and self._info:
                return self._info
            for relative, entry in manifest["files"].items():
                with (root / relative).open("rb") as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != entry["sha256"]:
                        raise ValueError
            self._info = {
                key: manifest[key]
                for key in (
                    "singer_id",
                    "singer_name",
                    "vocoder",
                    "openutau",
                    "protocol",
                    "onnx",
                    "devices",
                    "attribution",
                    "sources",
                )
            }
            self._info["resource_hash"] = fingerprint(manifest["files"])
            self._info["protocols"] = manifest.get("protocols", [PROTOCOL])
            self._info["model_hash"] = fingerprint(
                {k: v for k, v in manifest["files"].items() if v["group"] != "engine"}
            )
            self._stamp = stamp
            return self._info
        except (OSError, ValueError, KeyError, TypeError):
            raise AudioError(
                "演唱模型、词典、声码器或工具缺失/校验失败，请重新检查官方资源；已有结果保留。", 503
            ) from None

    def status(self):
        try:
            return {"ready": True, "singers": [self.info()], "error": None}
        except AudioError as error:
            return {"ready": False, "singers": [], "error": str(error)}

    def validate_readings(self, settings):
        if not settings.pronunciations:
            return
        self.info()
        dictionary = (
            self.paths.root / "singers/Qixuan_v2.7.0_DiffSinger_OpenUtau/dsdur/dsdict-zh.yaml"
        )
        try:
            allowed = set(
                re.findall(
                    r"^\s*- grapheme: ([a-z]+)\s*$",
                    dictionary.read_text(encoding="utf-8"),
                    re.MULTILINE,
                )
            )
        except OSError:
            raise AudioError("歌手普通话词典缺失，请检查官方资源。", 503) from None
        for correction in settings.pronunciations:
            if correction.pinyin not in allowed:
                raise AudioError(
                    f"歌词第{correction.line_index + 1}行第{correction.unit_index + 1}字的读音 {correction.pinyin} 不在歌手词典中，请填写不带声调的普通话拼音。",
                    422,
                )
