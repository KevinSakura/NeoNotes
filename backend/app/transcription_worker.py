"""Private single-job inference child. Only explicit local assets, no downloads."""

import json
import sys
from pathlib import Path


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    import soundfile as sf
    import torch

    torch.set_num_threads(4)
    assets = Path(request["assets"])
    sys.path.insert(0, str(assets / "source"))
    audio, rate = sf.read(request["input"], dtype="float32", always_2d=True)
    if request["operation"] == "separate":
        from demucs.apply import apply_model
        from demucs.states import load_model

        # Only the pinned, SHA-checked official checkpoint is accepted by the parent.
        package = torch.load(
            assets / "5c90dfd2-34c22ccb.th", map_location="cpu", weights_only=False
        )
        model = load_model(package)
        model.eval()
        if rate != model.samplerate:
            raise ValueError("Expected decoded 44100Hz")
        wave = torch.from_numpy(audio.T.copy())
        ref = wave.mean(0)
        scale = ref.std().clamp_min(1e-8)
        with torch.inference_mode():
            stems = apply_model(
                model,
                ((wave - ref.mean()) / scale)[None],
                device="cpu",
                shifts=0,
                split=True,
                overlap=0.25,
                progress=False,
                num_workers=0,
            )
        piano = (stems[0, model.sources.index("piano")] * scale + ref.mean()).T.numpy()
        sf.write(request["output"], piano, rate, subtype="PCM_16")
    else:
        import moduleconf
        import soxr
        from transkun.Data import writeMidi

        manager = moduleconf.parseFromFile(str(assets / "2.0.conf"))
        model = manager["Model"].module.TransKun(conf=manager["Model"].config)
        checkpoint = torch.load(assets / "2.0.pt", map_location="cpu", weights_only=False)
        model.load_state_dict(
            checkpoint.get("best_state_dict", checkpoint.get("state_dict")), strict=True
        )
        model.eval()
        if rate != model.fs:
            audio = soxr.resample(audio, rate, model.fs)
        with torch.inference_mode():
            notes = model.transcribe(
                torch.from_numpy(audio.copy()),
                stepInSecond=None,
                segmentSizeInSecond=None,
                discardSecondHalf=False,
            )
        writeMidi(notes).write(request["output"])


if __name__ == "__main__":
    main()
