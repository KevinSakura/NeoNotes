"""Local stage-two assets. Never load .env or expose arbitrary filesystem paths."""

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

from .models import Score

ROOT = Path(__file__).resolve().parents[2]


class AudioError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AudioPaths:
    soundfont: Path = ROOT / "tools/soundfonts/GeneralUser-GS-main/GeneralUser-GS.sf2"
    fluidsynth: Path = ROOT / "tools/fluidsynth-v2.6.1-win10-x64-cpp11/bin/fluidsynth.exe"
    ffmpeg: Path = ROOT / "tools/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe"
    ffprobe: Path = ROOT / "tools/ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe"


def chunks(data: bytes, start: int, end: int):
    while start + 8 <= end:
        kind, size = struct.unpack_from("<4sI", data, start)
        first, last = start + 8, start + 8 + size
        if last > end:
            raise AudioError("SoundFont 文件不完整，请检查 tools 中的音色文件。", 503)
        yield kind, first, last
        start = last + (size % 2)


class SoundFontResource:
    def __init__(self, paths: AudioPaths):
        self.paths = paths
        self._stamp = None
        self._info = None

    def info(self):
        try:
            stat = self.paths.soundfont.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if stamp == self._stamp and self._info:
                return self._info
            data = self.paths.soundfont.read_bytes()
        except OSError:
            raise AudioError("找不到或无法读取 SoundFont；请检查 tools/soundfonts。", 503) from None
        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"sfbk":
            raise AudioError("SoundFont 不是有效的 SF2 音色库。", 503)
        if struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
            raise AudioError("SoundFont 文件长度不正确。", 503)
        presets, name = [], "GeneralUser GS"
        for kind, first, last in chunks(data, 12, len(data)):
            if kind != b"LIST":
                continue
            group = data[first : first + 4]
            for child, begin, end in chunks(data, first + 4, last):
                if group == b"INFO" and child == b"INAM":
                    name = data[begin:end].rstrip(b"\x00").decode("latin1")
                if group == b"pdta" and child == b"phdr":
                    if (end - begin) % 38 or end - begin < 76:
                        raise AudioError("SoundFont 音色目录损坏。", 503)
                    for offset in range(begin, end - 38, 38):
                        program, bank = struct.unpack_from("<HH", data, offset + 20)
                        presets.append(
                            {
                                "bank": bank,
                                "program": program,
                                "name": data[offset : offset + 20]
                                .split(b"\x00")[0]
                                .decode("latin1"),
                            }
                        )
        if not presets:
            raise AudioError("SoundFont 中没有可用的乐器预设。", 503)
        self._stamp, self._info = (
            stamp,
            {
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "presets": presets,
            },
        )
        return self._info

    def status(self):
        try:
            info = self.info()
            return {
                "ready": True,
                "soundfont": info,
                "error": None,
                "render_ready": all(
                    path.is_file()
                    for path in (self.paths.fluidsynth, self.paths.ffmpeg, self.paths.ffprobe)
                ),
            }
        except AudioError as error:
            return {"ready": False, "soundfont": None, "render_ready": False, "error": str(error)}

    def validate_score(self, score: Score, mode: str):
        solo = any(track.solo for track in score.tracks)
        audible = [
            track
            for track in score.tracks
            if track.notes
            and track.volume > 0
            and (mode == "full" or (not track.mute and (not solo or track.solo)))
        ]
        if not audible:
            raise AudioError("没有可发声的音符；请检查音符、音量、静音和独奏。")
        presets = {(item["bank"], item["program"]) for item in self.info()["presets"]}
        for track in audible:
            bank = 128 if track.is_drum else track.bank_msb
            if track.bank_lsb != 0 or (track.is_drum and track.bank_msb != 0):
                raise AudioError(
                    f"声部“{track.name}”的 Bank 设置不在当前 GS 渲染范围内；MIDI 原信息保留。"
                )
            if (bank, track.program) not in presets:
                raise AudioError(
                    f"音色库中没有声部“{track.name}”的 Bank {bank} / Program {track.program}；不会替换音色。"
                )
        return audible
