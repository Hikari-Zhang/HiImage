import hashlib
import json
from io import BytesIO

import numpy as np

from scripts.watermark_quality_samples import PHOTO_DIRECTORY, PHOTOS, SIZE, samples


def test_fixed_quality_samples_pair_each_input_with_ground_truth():
    corpus = list(samples())
    assert len({sample.id for sample in corpus}) == len(corpus) == 30
    assert sum(sample.id.endswith("-clean") for sample in corpus) == 5

    for sample, repeated in zip(corpus, samples()):
        assert sample.id == repeated.id
        for first, second in zip(
            (sample.original, sample.input, sample.mask),
            (repeated.original, repeated.input, repeated.mask),
        ):
            assert first.size == SIZE
            assert first.tobytes() == second.tobytes()
        mask = np.asarray(sample.mask)
        assert set(np.unique(mask)) <= {0, 255}
        changed = np.any(np.asarray(sample.original) != np.asarray(sample.input), axis=-1)
        assert not changed[mask == 0].any()
        assert bool(changed.any()) == bool(mask.any()) == (not sample.id.endswith("-clean"))


def test_photo_corpus_has_pinned_sources_and_one_target_per_overlay():
    corpus = list(samples("photo"))
    assert len({sample.id for sample in corpus}) == len(corpus) == 18
    assert len(list(samples("all"))) == 48
    for photo in PHOTOS:
        assert hashlib.sha256((PHOTO_DIRECTORY / photo.filename).read_bytes()).hexdigest() == photo.sha256
    for sample in corpus:
        assert sample.photo in PHOTOS
        assert sample.original.size == sample.input.size == sample.mask.size == SIZE
        mask = np.asarray(sample.mask) != 0
        changed = np.any(np.asarray(sample.original) != np.asarray(sample.input), axis=-1)
        assert not changed[~mask].any()
        covered = np.zeros_like(mask)
        for x1, y1, x2, y2 in sample.regions:
            assert mask[y1:y2, x1:x2].any()
            covered[y1:y2, x1:x2] = True
        assert not (mask & ~covered).any()
        repeats = 3 if sample.id.endswith("-repeated") else 1
        assert len(sample.regions) == (0 if sample.id.endswith("-clean") else repeats)


def test_detection_matching_reassigns_overlapping_boxes_without_double_counting():
    from scripts import benchmark_watermark_quality as benchmark

    targets = [(0, 0, 10, 10), (8, 0, 18, 10)]
    predictions = [(0, 0, 18, 10), (0, 0, 10, 10), (22, 0, 30, 10)]
    matches = benchmark.match_regions(targets, predictions)
    assert [(match["target"], match["prediction"]) for match in matches] == [(0, 1), (1, 0)]


def test_detection_benchmark_records_false_positives_and_false_negatives(tmp_path, monkeypatch):
    from scripts import benchmark_watermark_quality as benchmark

    samples_by_id = {sample.id: sample for sample in samples("photo")}
    repeated = samples_by_id["photo-clock-repeated"]
    clean = samples_by_id["photo-clock-clean"]
    requests = []
    bad_region = [2, 3, 8, 12]
    responses = iter([[repeated.regions[0], bad_region], [bad_region]])

    def respond(request, timeout):
        assert request.full_url == "http://localhost/api/detect"
        requests.append(json.loads(request.data))
        return BytesIO(json.dumps({"engine": "test", "regions": next(responses)}).encode())

    monkeypatch.setattr(benchmark, "urlopen", respond)
    output = tmp_path / "detect-only"
    assert benchmark.run([repeated, clean], [], output, "http://localhost", benchmark.REDRAW, sensitivity=.4) == 0
    assert [request["sensitivity"] for request in requests] == [.4, .4]
    assert [request["image"] for request in requests] == [benchmark.encode(repeated.input), benchmark.encode(clean.input)]
    report = json.loads((output / "report.json").read_text())
    assert report["samples"][0]["source"]["sha256"] == repeated.photo.sha256
    assert report["samples"][1]["results"] == {}
    assert report["detection"]["summary"] == {
        "true_positive": 1, "false_positive": 2, "false_negative": 2,
        "precision": 1 / 3, "recall": 1 / 3, "clean_images": 1, "clean_images_with_candidates": 1,
        "request_errors": 0,
    }


def test_benchmark_sends_same_mask_and_scores_both_models(tmp_path, monkeypatch):
    from scripts import benchmark_watermark_quality as benchmark

    corpus = list(samples())
    sample, clean = corpus[0], corpus[5]
    requests = []

    def respond(request, timeout):
        requests.append(json.loads(request.data))
        return BytesIO(json.dumps({"image": benchmark.encode(sample.original)}).encode())

    monkeypatch.setattr(benchmark, "urlopen", respond)
    output = tmp_path / "run"
    assert benchmark.run([sample, clean], list(benchmark.DEVICES), output,
                         "http://localhost:8787", benchmark.REDRAW) == 0
    assert len(requests) == 2
    lama, flux = requests
    assert lama["mask"] == flux["mask"] == benchmark.encode(sample.mask)
    assert lama["image"] == flux["image"]
    assert lama["device"] == "cpu" and "redraw" not in lama
    assert flux["device"] == "cuda" and flux["redraw"] == benchmark.REDRAW
    report = json.loads((output / "report.json").read_text())
    for model in benchmark.DEVICES:
        metrics = report["samples"][0]["results"][model]
        assert metrics["input_mask_mae"] > 0
        assert metrics["output_mask_mae"] == metrics["changed_pixels_outside_mask"] == 0
        assert (output / sample.id / f"{model}.png").is_file()
        assert report["samples"][1]["results"][model]["status"] == "skipped_empty_mask"


def test_benchmark_fails_when_model_changes_an_unmasked_pixel(tmp_path, monkeypatch):
    from scripts import benchmark_watermark_quality as benchmark

    sample = next(samples())
    result = sample.original.copy()
    assert sample.mask.getpixel((0, 0)) == 0
    result.putpixel((0, 0), (255, 255, 255))
    monkeypatch.setattr(benchmark, "repair", lambda *args: result)
    output = tmp_path / "run"
    assert benchmark.run([sample], ["wm_lama"], output, "http://localhost", benchmark.REDRAW) == 1
    report = json.loads((output / "report.json").read_text())
    metrics = report["samples"][0]["results"]["wm_lama"]
    assert metrics["status"] == "outside_mask_changed"
    assert metrics["changed_pixels_outside_mask"] == 1
