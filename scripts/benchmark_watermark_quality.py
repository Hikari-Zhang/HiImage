"""Compare watermark repair models against fixed ground truth via the local API."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
from time import perf_counter
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np
from PIL import Image

from .watermark_quality_samples import VERSION, samples


DEVICES = {"wm_lama": "cpu", "flux_fill": "cuda"}
REDRAW = {
    "prompt": "Reconstruct the surrounding background and texture naturally.",
    "num_inference_steps": 30,
    "guidance_scale": 15,
    "seed": 42,
}


def encode(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def repair(base_url, sample, model, redraw):
    payload = {
        "image": encode(sample.input), "mask": encode(sample.mask),
        "model": model, "device": DEVICES[model], "dilation": 0, "task_id": str(uuid4()),
    }
    if model == "flux_fill":
        payload["redraw"] = redraw
    request = Request(
        base_url.rstrip("/") + "/api/inpaint/with-mask",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=1900) as response:
        data = json.load(response)
    with Image.open(io.BytesIO(base64.b64decode(data["image"]))) as result:
        return result.convert("RGB")


def score(sample, result):
    if result.size != sample.original.size:
        raise ValueError(f"{sample.id}: output dimensions changed to {result.size}")
    mask = np.asarray(sample.mask) != 0
    source = np.asarray(sample.input, dtype=np.int16)
    target = np.asarray(sample.original, dtype=np.int16)
    output = np.asarray(result, dtype=np.int16)
    return {
        "input_mask_mae": float(np.abs(source - target)[mask].mean()) if mask.any() else None,
        "output_mask_mae": float(np.abs(output - target)[mask].mean()) if mask.any() else None,
        "changed_pixels_outside_mask": int(np.count_nonzero(np.any(output != source, axis=-1) & ~mask)),
    }


def run(corpus, models, output, base_url, redraw):
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "dataset_version": VERSION,
        "dilation": 0,
        "models": {model: {"device": DEVICES[model]} for model in models},
        "samples": [],
    }
    if "flux_fill" in models:
        report["models"]["flux_fill"]["redraw"] = redraw
    failed = False
    for sample in corpus:
        directory = output / sample.id
        directory.mkdir()
        files = {"original": sample.original, "input": sample.input, "mask": sample.mask}
        for name, image in files.items():
            image.save(directory / f"{name}.png")
        entry = {
            "id": sample.id,
            "pixel_sha256": {name: hashlib.sha256(image.tobytes()).hexdigest() for name, image in files.items()},
            "results": {},
        }
        report["samples"].append(entry)
        for model in models:
            if sample.mask.getbbox() is None:
                entry["results"][model] = {"status": "skipped_empty_mask"}
                continue
            started = perf_counter()
            try:
                result = repair(base_url, sample, model, redraw)
                metrics = score(sample, result)
                result.save(directory / f"{model}.png")
                outside_changed = metrics["changed_pixels_outside_mask"] > 0
                status = "outside_mask_changed" if outside_changed else "ok"
                failed |= outside_changed
                entry["results"][model] = {"status": status, **metrics, "seconds": perf_counter() - started}
            except (OSError, ValueError, KeyError) as error:
                entry["results"][model] = {"status": "error", "error": str(error)}
                failed = True
            print(f"{sample.id} {model}: {entry['results'][model]['status']}", flush=True)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return int(failed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="A new directory for this run")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--models", nargs="+", choices=DEVICES, default=list(DEVICES))
    parser.add_argument("--sample", action="append", help="Run only this sample ID; repeat to select several")
    parser.add_argument("--steps", type=int, choices=range(1, 51), default=REDRAW["num_inference_steps"])
    args = parser.parse_args()
    corpus = [sample for sample in samples() if not args.sample or sample.id in args.sample]
    unknown = set(args.sample or ()) - {sample.id for sample in corpus}
    if unknown:
        parser.error(f"unknown sample IDs: {', '.join(sorted(unknown))}")
    if args.output.exists():
        parser.error(f"output directory already exists: {args.output}")
    return run(corpus, list(dict.fromkeys(args.models)), args.output, args.base_url,
               {**REDRAW, "num_inference_steps": args.steps})


if __name__ == "__main__":
    raise SystemExit(main())
