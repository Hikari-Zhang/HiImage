import base64
from dataclasses import replace
import io
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.routers import prompt_edit as router
from app.services import prompt_edit as service
from core.prompt_edit_windows import RUNTIME_BINARIES, WINDOWS_MODELS


def encode(image, file_format="PNG", **options):
    buffer = io.BytesIO()
    image.save(buffer, file_format, **options)
    return base64.b64encode(buffer.getvalue()).decode()


def png(width, height):
    return encode(Image.new("RGB", (width, height), "blue"))


@pytest.fixture
def mac_models(monkeypatch):
    monkeypatch.setattr(service, "windows_x64", lambda: False)
    monkeypatch.setattr(service, "platform_unavailable_reason", lambda: None)


@pytest.mark.parametrize(("model_id", "steps"), [(None, "4"), ("flux3-image-klein", "16"), ("fibo2-edit-turbo", "4")])
def test_prompt_edit_tracks_result_with_local_weights(monkeypatch, tmp_path, isolated_task_registry, mac_models, model_id, steps):
    executable, weights = tmp_path / "mflux", tmp_path / "weights"
    monkeypatch.setattr(service, "runtime", lambda model: (executable, weights, None))

    def run(command, env, timeout, consume, **options):
        assert command[:5] == [str(executable), "--model", str(weights), "--base-model", model_id or service.DEFAULT_MODEL]
        assert command[command.index("--steps") + 1] == steps
        prompt_file = Path(command[command.index("--prompt-file") + 1])
        assert prompt_file.read_text(encoding="utf-8") == "--把背景改成蓝色"
        assert "--把背景改成蓝色" not in command
        assert command[command.index("--seed") + 1] == "42"
        assert env["HF_HUB_OFFLINE"] == "1"
        with Image.open(command[command.index("--image-paths") + 1]) as source:
            assert source.size == (768, 1024)
        Image.new("RGB", (768, 1024), "red").save(Path(command[-1]))
        return 0

    monkeypatch.setattr(service, "_stream_cli_output", run)
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    with TestClient(app) as client:
        status = client.get("/api/prompt-edit/status").json()
        assert status["available"] is True
        assert status["default_model"] == service.DEFAULT_MODEL
        assert {model["id"] for model in status["models"] if model["available"]} == service.EDIT_MODELS.keys()
        assert [model["id"] for model in status["models"] if model["supports_mask"]] == ["fibo2-edit-turbo"]
        request = {"image": png(1200, 1600), "prompt": " --把背景改成蓝色 ", "task_id": "test-prompt-edit", "seed": 42}
        if model_id is not None:
            request["model"] = model_id
        response = client.post("/api/prompt-edit", json=request)
        assert response.status_code == 200
        result = response.json()
        assert (result["width"], result["height"]) == (768, 1024)
        with Image.open(io.BytesIO(base64.b64decode(result["image"]))) as output:
            assert output.getpixel((0, 0)) == (255, 0, 0)
        assert isolated_task_registry.get(request["task_id"])["status"] == "succeeded"


def test_selection_uses_model_mask_and_preserves_outer_pixels(monkeypatch, tmp_path, isolated_task_registry, mac_models):
    monkeypatch.setattr(service, "runtime", lambda model: (tmp_path / "mflux", tmp_path / "weights", None))
    source = Image.new("RGBA", (1073, 803), (0, 152, 255, 91))
    mask = Image.new("L", source.size)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((300, 300, 650, 600), fill=255)
    draw.rectangle((290, 300, 299, 600), fill=128)

    def run(command, env, timeout, consume, **options):
        def path_for(option):
            return Path(command[command.index(option) + 1])

        with Image.open(path_for("--image-paths")) as working_source, Image.open(path_for("--mask-path")) as working_mask:
            assert working_source.size == working_mask.size == (1024, 768)
            assert working_mask.mode == "L"
            assert working_mask.getpixel((10, 10)) == 0
            assert working_mask.getpixel((400, 400)) == 255
            Image.new("RGB", working_source.size, (255, 152, 0)).save(path_for("--output"))
        return 0

    monkeypatch.setattr(service, "_stream_cli_output", run)
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    with TestClient(app) as client:
        response = client.post("/api/prompt-edit", json={
            "image": encode(source), "mask": encode(mask), "model": "fibo2-edit-turbo",
            "prompt": "Change the selected area", "task_id": "selection-test",
        })

    assert response.status_code == 200
    result = response.json()
    assert (result["width"], result["height"]) == source.size
    with Image.open(io.BytesIO(base64.b64decode(result["image"]))) as output:
        assert output.mode == "RGBA"
        assert output.getpixel((100, 100)) == source.getpixel((100, 100))
        assert output.getpixel((400, 400)) == (255, 152, 0, 255)
        assert output.getpixel((295, 400)) == (128, 152, 127, 173)


def test_selection_uses_display_orientation_for_the_source(monkeypatch, tmp_path):
    source = Image.new("RGB", (67, 42), "green")
    ImageDraw.Draw(source).rectangle((0, 0, 20, 41), fill="blue")
    exif = source.getexif()
    exif[274] = 6
    jpeg = encode(source, "JPEG", exif=exif)
    mask = Image.new("L", (42, 67))
    ImageDraw.Draw(mask).rectangle((0, 40, 30, 60), fill=255)

    def run(command, env, timeout, consume, **options):
        Image.new("RGB", (32, 64), "red").save(command[command.index("--output") + 1])
        return 0

    monkeypatch.setattr(service, "_stream_cli_output", run)
    model_id = "fibo2-edit-turbo"
    result = service.edit_image(model_id, service.EDIT_MODELS[model_id], tmp_path / "mflux", tmp_path, jpeg, "Edit", 42, encode(mask))
    assert result.size == mask.size
    assert result.getpixel((20, 10)) == tuple(service.decode_image(jpeg)[10, 20])
    assert result.getpixel((20, 50)) == (255, 0, 0)


@pytest.mark.parametrize(
    ("model", "mask", "message"),
    [
        ("flux2-klein-4b", Image.new("L", (64, 64), 255), "不支持局部编辑"),
        ("fibo2-edit-turbo", Image.new("L", (32, 64), 255), "尺寸必须与原图一致"),
        ("fibo2-edit-turbo", Image.new("L", (64, 64)), "选区为空"),
        ("fibo2-edit-turbo", Image.new("L", (64, 64), 127), "选区太小"),
    ],
)
def test_invalid_selection_is_rejected_before_inference(monkeypatch, tmp_path, isolated_task_registry, mac_models, model, mask, message):
    monkeypatch.setattr(service, "runtime", lambda model: (tmp_path, tmp_path, None))
    monkeypatch.setattr(service, "_stream_cli_output", lambda *args, **kw: pytest.fail("inference was called"))
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    with TestClient(app) as client:
        response = client.post("/api/prompt-edit", json={
            "image": png(64, 64), "mask": encode(mask), "model": model, "prompt": "Edit", "task_id": "invalid-selection",
        })
        assert response.status_code == 422
        assert message in response.json()["detail"]


def test_missing_runtime_and_blank_instruction_are_reported(monkeypatch, tmp_path, isolated_task_registry, mac_models):
    monkeypatch.setattr(service, "runtime", lambda model: (tmp_path, tmp_path, "权重尚未下载完整"))
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    with TestClient(app) as client:
        status = client.get("/api/prompt-edit/status").json()
        assert status["available"] is False and status["reason"] == "权重尚未下载完整"
        assert {model["id"] for model in status["models"]} == service.EDIT_MODELS.keys()
        request = {"image": png(32, 32), "prompt": "   ", "task_id": "empty-prompt"}
        assert client.post("/api/prompt-edit", json=request).status_code == 422
        assert client.post("/api/prompt-edit", json={
            **request, "prompt": "change the background", "task_id": "missing-runtime",
        }).status_code == 503
        assert client.post("/api/prompt-edit", json={
            **request, "model": "unknown-model", "prompt": "change the background", "task_id": "invalid-model",
        }).status_code == 422


@pytest.mark.parametrize(("system", "machine"), [("Windows", "ARM64"), ("Linux", "aarch64"), ("Darwin", "x86_64")])
def test_prompt_edit_hides_mac_models_on_unsupported_backends(monkeypatch, system, machine):
    monkeypatch.setattr(service.platform, "system", lambda: system)
    monkeypatch.setattr(service.platform, "machine", lambda: machine)
    monkeypatch.setattr(service, "runtime", lambda model: pytest.fail("model readiness was checked"))

    assert router.get_prompt_edit_status() == {
        "available": False, "reason": "提示词编辑需要 Apple Silicon macOS 或 Windows x64 后端。",
        "model": None, "default_model": None, "models": [],
    }


@pytest.mark.parametrize(("source", "expected"), [
    ((4096, 2304), (1024, 576)), ((33, 65), (32, 64)), ((1, 5000), (32, 1024)),
])
def test_edit_dimensions_align_to_the_model(source, expected):
    assert service.edit_dimensions(*source) == expected


def test_windows_uses_ready_model_and_its_own_command(monkeypatch, tmp_path, isolated_task_registry):
    monkeypatch.setattr(service, "windows_x64", lambda: True)
    monkeypatch.setattr(service, "weight_locations", lambda: (tmp_path / "weights",))
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(service, "runtime_directory", lambda: runtime)
    monkeypatch.setenv("HIIMAGE_PROMPT_EDIT_VULKAN_DEVICE", "vulkan1")
    model_id = "qwen-image-2.1"
    profile = WINDOWS_MODELS[model_id]
    profile = replace(
        profile, diffusion=replace(profile.diffusion, size=2), encoder=replace(profile.encoder, size=2),
        vae=replace(profile.vae, size=2), projector=replace(profile.projector, size=2),
    )
    monkeypatch.setattr(service, "WINDOWS_MODELS", {**WINDOWS_MODELS, model_id: profile})
    assert "安装" in service.runtime(profile)[2]
    runtime.mkdir()
    for file in RUNTIME_BINARIES:
        (runtime / file).write_bytes(b"exe")
    assert "权重" in service.runtime(profile)[2]
    for weight in profile.files:
        path = weight.path(tmp_path / "weights")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ok")

    def run(command, env, timeout, consume, **options):
        def arg(flag):
            return command[command.index(flag) + 1]

        assert Path(command[0]) == runtime / "sd-cli.exe"
        assert Path(arg("--diffusion-model")) == profile.diffusion.path(tmp_path / "weights")
        assert Path(arg("--llm_vision")) == profile.projector.path(tmp_path / "weights")
        assert (arg("--steps"), arg("--cfg-scale"), arg("--seed")) == ("30", "6.0", "42")
        assert arg("--backend") == "te=cpu,vae=cpu,diffusion=vulkan1"
        assert arg("--max-vram") == "vulkan1=3"
        assert Path(arg("--prompt-file")).read_text(encoding="utf-8") == "把杯子改成蓝色"
        assert env["HF_HUB_OFFLINE"] == "1"
        with Image.open(arg("-r")) as source:
            assert source.size == (64, 96)
        Image.new("RGB", (64, 96), "blue").save(arg("-o"))
        return 0

    monkeypatch.setattr(service, "_stream_cli_output", run)
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    with TestClient(app) as client:
        status = client.get("/api/prompt-edit/status").json()
        assert status["available"] and status["default_model"] == model_id
        assert [model["id"] for model in status["models"]] == list(WINDOWS_MODELS)
        assert not any(model["supports_mask"] for model in status["models"])
        request = {"image": png(65, 97), "prompt": "把杯子改成蓝色", "task_id": "windows-edit", "seed": 42}
        response = client.post("/api/prompt-edit", json=request)
        assert response.status_code == 200
        assert isolated_task_registry.get(request["task_id"])["status"] == "succeeded"
        response = client.post("/api/prompt-edit", json={
            **request, "mask": png(65, 97), "model": "fibo2-edit-turbo", "task_id": "windows-selection",
        })
        assert response.status_code == 422
