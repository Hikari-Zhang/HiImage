"""用临时权重验证缓存检查、文件修复和下载等待。"""

import asyncio
import json

from core import downloaders, model_download_helper, paths
from core.constants import DownloadStatus as DS, ModelStatus as MS
from core.download_queue import DownloadTask
from core.model_checker import ModelCheckResult, ModelChecker
from core.model_registry import get_model


def test_retry_preserves_healthy_submodel_and_refreshes_only_broken_file(tmp_path, monkeypatch):
    import huggingface_hub

    monkeypatch.setattr(paths, "HF_HOME", tmp_path)
    hub = tmp_path / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    good = hub / "models--demo--good" / "config.json"
    bad = hub / "models--demo--bad" / "config.json"
    for path, content in ((good, "{}"), (bad, "broken")):
        path.parent.mkdir(parents=True)
        path.write_text(content)
    repositories = {"demo/good": good, "demo/bad": bad}
    downloaded = {}

    def fake_download(**kwargs):
        repo = kwargs["repo_id"]
        downloaded[repo] = kwargs.get("force_download", False)
        assert kwargs["cache_dir"] == str(hub)
        assert good.is_file() and bad.is_file()
        return str(repositories[repo])

    monkeypatch.setattr(huggingface_hub, "list_repo_files", lambda *_args, **_kw: ["config.json"])
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda repo, *_args, **_kw: str(repositories[repo]))
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    monkeypatch.setattr(ModelChecker, "check_model", lambda *_: ModelCheckResult("mixed", "", "", MS.CORRUPTED, "broken"))
    downloaders.download_hf_multi({"id": "mixed", "hf_models": [{"id": repo} for repo in repositories]})
    assert downloaded == {"demo/good": False, "demo/bad": True}
    assert good.read_text() == "{}"


def test_checker_uses_current_hf_snapshot_and_checks_every_shard(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    cfg = get_model("auto_segment_hsv")
    repo = tmp_path / ("models--" + cfg["hf_model_id"].replace("/", "--"))
    (repo / "refs").mkdir(parents=True)
    revision = "a" * 40
    (repo / "refs" / "main").write_text(revision)
    snapshot = repo / "snapshots" / revision
    snapshot.mkdir(parents=True)
    checker = ModelChecker()
    weights = {
        "a": "pytorch_model-00001-of-00002.bin",
        "b": "pytorch_model-00002-of-00002.bin",
    }
    (snapshot / "pytorch_model.bin.index.json").write_text(json.dumps({"weight_map": weights}))
    (snapshot / weights["a"]).write_bytes(b"weights")
    assert paths.resolve_hf_model_path(cfg["hf_model_id"]) == str(snapshot)
    assert checker.check_config(cfg).status == MS.PARTIAL
    (snapshot / "config.json").write_text("{}")
    (snapshot / "preprocessor_config.json").write_text("{}")
    assert checker.check_config(cfg).status == MS.PARTIAL
    (snapshot / weights["b"]).write_bytes(b"version https://git-lfs.github.com/spec/v1")
    assert checker.check_config(cfg).status == MS.CORRUPTED
    (snapshot / weights["b"]).write_bytes(b"weights")
    assert checker.check_config({**cfg, "size_mb": None}).status == MS.OK


def test_diffusers_missing_component_cannot_be_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "find_hf_snapshot", lambda _: tmp_path)
    (tmp_path / "model_index.json").write_text(json.dumps({"_class_name": "StableDiffusionXLImg2ImgPipeline"}))
    result = ModelChecker().check_config(get_model("sdxl_img2img"))
    assert result.status == MS.CORRUPTED
    assert "model_index.json" in result.message


def test_grounding_dino_requires_tokenizer_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "find_hf_snapshot", lambda _: tmp_path)
    cfg = {"id": "gdino", "hf_model_id": "IDEA-Research/grounding-dino-base"}
    (tmp_path / "config.json").write_text('{"model_type": "grounding-dino"}')
    (tmp_path / "preprocessor_config.json").write_text("{}")
    (tmp_path / "pytorch_model.bin").write_bytes(b"weights")
    checker = ModelChecker()
    assert checker.check_config(cfg).status == MS.PARTIAL
    (tmp_path / "vocab.txt").write_text("[UNK]")
    assert checker.check_config(cfg).status == MS.OK


def test_automatic_download_waits_for_original_attempt(monkeypatch):
    original = DownloadTask("flux_fill", "original", status=DS.CANCELLED)
    retry = DownloadTask("flux_fill", "retry", status=DS.DONE)

    class Queue:
        def submit(self, _):
            return original

        def get_task(self, _):
            return retry

    monkeypatch.setattr(model_download_helper, "get_download_queue", Queue)
    monkeypatch.setattr(ModelChecker, "check_model", lambda *_: ModelCheckResult("flux_fill", "", "", MS.MISSING, "missing"))
    assert asyncio.run(model_download_helper.ensure_model_ready("flux_fill")) == (False, "下载已取消")
