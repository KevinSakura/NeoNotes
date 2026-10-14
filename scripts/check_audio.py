"""Read-only stage-two resource diagnostics; invoke with conda run -n piano."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.render import RenderManager  # noqa: E402
from backend.app.resources import AudioPaths, SoundFontResource  # noqa: E402


def main():
    paths = AudioPaths()
    status = SoundFontResource(paths).status()
    if not status["ready"]:
        print(status["error"])
        return 1
    font = status["soundfont"]
    print(f"SoundFont: {font['name']} ({font['bytes']:,} bytes, {len(font['presets'])} presets)")
    print(f"SHA-256: {font['sha256']}")
    if not status["render_ready"]:
        print("Missing FluidSynth, FFmpeg or FFprobe in tools.")
        return 1
    for path, flag in (
        (paths.fluidsynth, "-V"),
        (paths.ffmpeg, "-version"),
        (paths.ffprobe, "-version"),
    ):
        print(
            RenderManager.command([str(path), flag], [])
            .decode("utf-8", errors="replace")
            .splitlines()[0]
        )
    print("Resource and executable checks passed. This is not an audio quality test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
