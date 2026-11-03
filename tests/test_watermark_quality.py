import json
from io import BytesIO

import numpy as np

from scripts.watermark_quality_samples import SIZE, samples


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
