"""Compare watermark repair models against fixed ground truth via the local API."""
import argparse
import base64
import hashlib
import io
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np
from PIL import Image

from .watermark_quality_samples import CORPORA, VERSION, samples


DEVICES = {"wm_lama": "cpu", "flux_fill": "cuda"}
DETECTION_IOU = 0.30
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
    data = post(base_url, "/api/inpaint/with-mask", payload, timeout=1900)
    with Image.open(io.BytesIO(base64.b64decode(data["image"]))) as result:
        return result.convert("RGB")


def post(base_url, endpoint, payload, *, timeout):
    request = Request(
        base_url.rstrip("/") + endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def box_iou(first, second):
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    overlap = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(0, min(ay2, by2) - max(ay1, by1))
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - overlap
    return overlap / union if union > 0 else 0


def match_regions(expected, predicted):
    overlaps = [[box_iou(region, target) for target in expected] for region in predicted]
    owner = {}

    def assign(prediction, seen_targets):
        for target, overlap in sorted(enumerate(overlaps[prediction]), key=lambda item: -item[1]):
            if overlap < DETECTION_IOU or target in seen_targets:
                continue
            seen_targets.add(target)
            if target not in owner or assign(owner[target], seen_targets):
                owner[target] = prediction
                return True
        return False

    # Find a maximum one-to-one matching so duplicate boxes cannot inflate recall.
    for prediction in range(len(predicted)):
        assign(prediction, set())
    return [
        {"target": target, "prediction": pred, "iou": round(overlaps[pred][target], 4)}
        for target, pred in sorted(owner.items())
    ]


def detect(base_url, sample, sensitivity):
    response = post(base_url, "/api/detect", {
        "image": encode(sample.input), "sensitivity": sensitivity, "task_id": str(uuid4()),
    }, timeout=120)
    predicted = response["regions"]
    matches = match_regions(sample.regions, predicted)
    true_positive = len(matches)
    return {
        "engine": response.get("engine"),
        "warning": response.get("warning"),
        "regions": predicted,
        "matches": matches,
        "true_positive": true_positive,
        "false_positive": len(predicted) - true_positive,
        "false_negative": len(sample.regions) - true_positive,
    }


def detection_summary(entries):
    completed = [entry for entry in entries if entry["detection"]["status"] == "ok"]
    clean = [entry["detection"] for entry in completed if not entry["target_regions"]]
    totals = {
        key: sum(entry["detection"][key] for entry in completed)
        for key in ("true_positive", "false_positive", "false_negative")
    }
    tp, fp, fn = (totals[key] for key in ("true_positive", "false_positive", "false_negative"))
    return {
        **totals,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "clean_images": len(clean),
        "clean_images_with_candidates": sum(bool(item["regions"]) for item in clean),
        "request_errors": len(entries) - len(completed),
    }


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


def run(corpus, models, output, base_url, redraw, *, sensitivity=None):
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "dataset_version": VERSION,
        "dilation": 0,
        "pillow_version": Image.__version__,
        "numpy_version": np.__version__,
        "models": {model: {"device": DEVICES[model]} for model in models},
        "samples": [],
    }
    if sensitivity is not None:
        report["detection"] = {"sensitivity": sensitivity, "match_iou": DETECTION_IOU}
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
            "corpus": "photo" if sample.photo else "synthetic",
            "target_regions": sample.regions,
            "pixel_sha256": {name: hashlib.sha256(image.tobytes()).hexdigest() for name, image in files.items()},
            "results": {},
        }
        if sample.photo:
            entry["source"] = {**asdict(sample.photo), "url": sample.photo.url}
        report["samples"].append(entry)
        if sensitivity is not None:
            started = perf_counter()
            try:
                entry["detection"] = {"status": "ok", **detect(base_url, sample, sensitivity),
                                      "seconds": perf_counter() - started}
            except (OSError, ValueError, KeyError, TypeError) as error:
                entry["detection"] = {"status": "error", "error": str(error)}
                failed = True
            report["detection"]["summary"] = detection_summary(report["samples"])
            print(f"{sample.id} detect: {entry['detection']['status']}", flush=True)
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
    parser.add_argument("--corpus", choices=CORPORA, default="synthetic")
    parser.add_argument("--sample", action="append", help="Run only this sample ID; repeat to select several")
    parser.add_argument("--steps", type=int, choices=range(1, 51), default=REDRAW["num_inference_steps"])
    parser.add_argument("--detect", action="store_true", help="Also score automatic candidate detection")
    parser.add_argument("--detect-only", action="store_true", help="Score automatic detection without repairing")
    parser.add_argument("--sensitivity", type=float, default=0.5)
    args = parser.parse_args()
    if not 0 <= args.sensitivity <= 1:
        parser.error("--sensitivity must be between 0 and 1")
    corpus = [sample for sample in samples(args.corpus) if not args.sample or sample.id in args.sample]
    unknown = set(args.sample or ()) - {sample.id for sample in corpus}
    if unknown:
        parser.error(f"unknown sample IDs: {', '.join(sorted(unknown))}")
    if args.output.exists():
        parser.error(f"output directory already exists: {args.output}")
    return run(corpus, [] if args.detect_only else list(dict.fromkeys(args.models)), args.output, args.base_url,
               {**REDRAW, "num_inference_steps": args.steps},
               sensitivity=args.sensitivity if args.detect or args.detect_only else None)


if __name__ == "__main__":
    raise SystemExit(main())
