"""模型注册契约回归：拒绝配置错误，保持懒加载、重载和目录接口兼容。"""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from core import model_registry
from core.model_schema import ModelConfigError, validate_registry


@pytest.fixture
def document():
    return yaml.safe_load((Path(__file__).resolve().parents[1] / "core/models.yaml").read_text(encoding="utf-8"))


def model(document, mid):
    return next(item for item in document["models"] if item["id"] == mid)


def test_current_registry_validates_without_mutation(document):
    original = deepcopy(document)
    models, modes = validate_registry(document)
    assert document == original
    assert len(models) == 23 and len(modes) == 10
    assert {m["pipeline"] for m in models} >= {"flux_fill", "rembg", "realesrgan"}
    assert next(mode for mode in modes if mode["id"] == "cutout")["models"] == [
        m["id"] for m in models if "cutout" in m["tags"]]
    models[0]["capabilities"].append("changed")
    assert document == original


@pytest.mark.parametrize("value", [None, [], "bad", {}])
def test_invalid_document_root_or_version_is_readable(value):
    with pytest.raises(ModelConfigError, match="registry|schema_version"):
        validate_registry(value)


@pytest.mark.parametrize("value", [None, True, "1", 2])
def test_invalid_schema_version(document, value):
    document["schema_version"] = value
    with pytest.raises(ModelConfigError, match="schema_version"):
        validate_registry(document)


@pytest.mark.parametrize("section", ["models", "mode_groups"])
@pytest.mark.parametrize("value", [None, {}, [], [None], [{"id": ""}]])
def test_invalid_lists_and_entries(document, section, value):
    document[section] = value
    with pytest.raises(ModelConfigError, match=section):
        validate_registry(document)


@pytest.mark.parametrize("section", ["models", "mode_groups"])
def test_duplicate_ids_fail_instead_of_overwriting(document, section):
    document[section].append(deepcopy(document[section][0]))
    with pytest.raises(ModelConfigError, match="ID 重复"):
        validate_registry(document)


@pytest.mark.parametrize("field,value", [
    ("executor", "arbitrary.module.Class"), ("executor", "rembg"),
    ("pipeline", "unknown"), ("pipeline", None), ("pipeline", {}),
    ("pipeline", "flux_img2img"), ("architecture", "sdxl"),
    ("provider", "HiImage"), ("capabilities", ["matting"]),
    ("capabilities", ["inpaint", "inpaint"]), ("capabilities", []),
    ("tags", ["missing-mode"]), ("tags", ["cutout"]),
    ("hf_model_id", ""), ("name", ""), ("supported_params", []),
])
def test_model_semantics_are_validated(document, field, value):
    model(document, "flux_fill")[field] = value
    with pytest.raises(ModelConfigError, match=r"models\[flux_fill\]"):
        validate_registry(document)


@pytest.mark.parametrize("patch", [
    {"type": "unknown"}, {"default": "50"}, {"default": True},
    {"default": 0}, {"default": float("nan")}, {"range": [50, 1]},
    {"range": [1]}, {"range": [1, float("inf")]}, {"range": [1.5, 50]},
])
def test_bad_parameter_types_ranges_and_defaults(document, patch):
    model(document, "flux_fill")["supported_params"]["num_inference_steps"].update(patch)
    with pytest.raises(ModelConfigError, match="supported_params.num_inference_steps"):
        validate_registry(document)


def test_unknown_parameter_cannot_be_advertised(document):
    model(document, "flux_fill")["supported_params"]["negative_prompt"] = {"type": "str", "default": ""}
    with pytest.raises(ModelConfigError, match="管线不支持的参数"):
        validate_registry(document)


def test_parameter_cannot_change_to_an_incompatible_but_self_consistent_type(document):
    model(document, "flux_fill")["supported_params"]["num_inference_steps"] = {"type": "str", "default": "50"}
    with pytest.raises(ModelConfigError, match="参数类型与管线契约不匹配"):
        validate_registry(document)


@pytest.mark.parametrize("mid,field", [
    ("birefnet", "rembg_session_name"), ("birefnet", "onnx_filename"),
    ("birefnet", "github_download_url"), ("flux", "hf_model_id"),
    ("grounded_sam_flux", "hf_models"), ("gfpgan", "local_path"),
    ("RealESRGAN_x4plus", "download_url"), ("RealESRGAN_x4plus", "num_block"),
])
def test_missing_loading_metadata_is_rejected(document, mid, field):
    del model(document, mid)[field]
    with pytest.raises(ModelConfigError, match=field):
        validate_registry(document)


@pytest.mark.parametrize("field,value", [
    ("arch", "SRVGGNetCompact"), ("scale", True), ("scale", 3), ("num_block", 0),
])
def test_upscale_architecture_and_scale_contract(document, field, value):
    model(document, "RealESRGAN_x4plus")[field] = value
    with pytest.raises(ModelConfigError, match=field):
        validate_registry(document)


@pytest.mark.parametrize("value", ["absent", "flux_fill", None])
def test_available_mode_requires_default_belonging_to_mode(document, value):
    next(m for m in document["mode_groups"] if m["id"] == "cutout")["default_model"] = value
    with pytest.raises(ModelConfigError, match=r"mode_groups\[cutout\].default_model"):
        validate_registry(document)


@pytest.mark.parametrize("patch", [{"default_model": "flux_fill"}, {"unavailable_reason": ""}, {"available": "false"}])
def test_disabled_mode_has_explicit_contract(document, patch):
    next(m for m in document["mode_groups"] if m["id"] == "prompt_inpaint").update(patch)
    with pytest.raises(ModelConfigError, match=r"mode_groups\[prompt_inpaint\]"):
        validate_registry(document)


def test_conflicting_weight_sources_and_duplicate_repos(document):
    model(document, "flux")["hf_models"] = [{"id": "other/model"}]
    with pytest.raises(ModelConfigError, match="不能同时声明"):
        validate_registry(document)
    del model(document, "flux")["hf_models"]
    repos = model(document, "grounded_sam_flux")["hf_models"]
    repos.append(deepcopy(repos[0]))
    with pytest.raises(ModelConfigError, match="组合仓库不能重复"):
        validate_registry(document)


def test_invalid_reload_keeps_previous_snapshot(document, monkeypatch, tmp_path):
    before = model_registry.get_model("birefnet")
    document["models"].append(deepcopy(document["models"][0]))
    file = tmp_path / "models.yaml"
    file.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(model_registry, "_YAML_PATH", file)
    with pytest.raises(ModelConfigError, match="models.yaml.*ID 重复"):
        model_registry.reload()
    assert model_registry.get_model("birefnet") is before


def test_first_load_failure_does_not_publish_partial_registry(document, monkeypatch, tmp_path):
    file = tmp_path / "models.yaml"
    file.write_text("models: []", encoding="utf-8")
    monkeypatch.setattr(model_registry, "_YAML_PATH", file)
    monkeypatch.setattr(model_registry, "_loaded", False)
    previous = model_registry._MODEL_BY_ID
    with pytest.raises(ModelConfigError, match="schema_version"):
        model_registry._ensure_loaded()
    assert model_registry._loaded is False
    assert model_registry._MODEL_BY_ID is previous


def test_loading_registry_does_not_import_inference_libraries(monkeypatch):
    import builtins
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        assert name.split(".")[0] not in {"torch", "diffusers", "rembg", "onnxruntime"}
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert len(model_registry._load()[0]) == 23


def test_catalog_api_keeps_existing_fields_and_exposes_new_metadata(monkeypatch):
    import asyncio
    import httpx
    from fastapi import FastAPI
    from app.routers import models, synthesis
    monkeypatch.setattr(models.ModelChecker, "check_all", lambda _: [])
    app = FastAPI()
    app.include_router(models.router, prefix="/api")
    app.include_router(synthesis.router, prefix="/api")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/models/list")
            assert response.status_code == 200
            rows = response.json()["models"]
            assert len(rows) == 23
            flux = next(row for row in rows if row["id"] == "flux_fill")
            assert flux["provider"] == "diffusers"
            assert flux["pipeline"] == "flux_fill"
            assert flux["capabilities"] == ["inpaint"]
            assert {"name", "features", "tags", "status", "message"} <= flux.keys()
            defaults = (await client.get("/api/models/defaults")).json()
            assert defaults["upscale"] == "RealESRGAN_x4plus"
            assert defaults["cutout"] == "birefnet"
            assert defaults["watermark_removal"] == "wm_lama"
            groups = (await client.get("/api/models/groups")).json()
            assert {m["id"] for group in groups["inpaint"]["groups"] for m in group["models"]} == {"wm_lama", "flux_fill"}
            assert groups["cutout"]["default_model"] == "birefnet"
            composite = (await client.get("/api/synthesis/models")).json()["models"]
            assert {row["id"] for row in rows} == {row["id"] for row in composite}
    asyncio.run(scenario())


@pytest.mark.parametrize("valid", [False, True])
def test_standalone_validator_exit_status(document, tmp_path, valid):
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    if not valid:
        document["models"][0]["pipeline"] = "arbitrary.module.Class"
    file = tmp_path / "registry.yaml"
    file.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    result = subprocess.run([sys.executable, "-X", "utf8", str(root / "scripts/check_model_registry.py"),
                             "--path", str(file)], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == (0 if valid else 1)
    assert ("23 models, 10 modes" in result.stdout) if valid else ("models[birefnet].pipeline" in result.stderr)
