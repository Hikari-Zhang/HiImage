"""Run the prompt-edit model for the current local inference platform."""
from dataclasses import dataclass
import os
import platform
import re
import secrets
import subprocess
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from app.logging_manager import log_manager
from app.services.image_codec import decode_image, decode_mask, decode_rgba_image, validate_matching_dimensions
from core.paths import CACHE_ROOT
from core.process_runner import _cli_log_directory, _stream_cli_output
from core.prompt_edit_windows import WINDOWS_MODELS, WindowsEditModel, install_root, runtime_directory, runtime_installed

MAX_EDGE = 1024
TIMEOUT_SECONDS = 600
DEFAULT_MODEL = "flux2-klein-4b"
WINDOWS_VRAM_BUDGET_GIB = 3


@dataclass(frozen=True)
class EditModel:
    name: str
    command: str
    directory: str
    steps: int
    files: dict[str, int]
    alignment: int = 16
    supports_mask: bool = False


FLUX2_FILES = {
    "text_encoder/0.safetensors": 2135435237,
    "text_encoder/1.safetensors": 127582075,
    "text_encoder/model.safetensors.index.json": 51332,
    "tokenizer/chat_template.jinja": 4168,
    "tokenizer/tokenizer.json": 11422650,
    "tokenizer/tokenizer_config.json": 376,
    "transformer/0.safetensors": 2145323769,
    "transformer/1.safetensors": 34727728,
    "transformer/model.safetensors.index.json": 26908,
    "vae/0.safetensors": 165107888,
    "vae/model.safetensors.index.json": 17547,
}

EDIT_MODELS = {
    DEFAULT_MODEL: EditModel("FLUX.2 Klein 4B", "mflux-generate-flux2-edit", "prompt-edit-model", 4, FLUX2_FILES),
    "flux3-image-klein": EditModel(
        "FLUX 3 Image Klein", "mflux-generate-flux3-edit", "prompt-edit-models/flux3-image-klein", 16, {
            "manifest.json": 78,
            "text_encoder/0.safetensors": 2035881396,
            "text_encoder/chat_template.jinja": 5292,
            "text_encoder/model.safetensors.index.json": 45636,
            "text_encoder/tokenizer.json": 11422650,
            "text_encoder/tokenizer_config.json": 692,
            "transformer/0.safetensors": 2144245413,
            "transformer/1.safetensors": 977011458,
            "transformer/model.safetensors.index.json": 61760,
            "vae/0.safetensors": 165107888,
            "vae/model.safetensors.index.json": 17547,
        },
    ),
    "fibo2-edit-turbo": EditModel(
        "FIBO 2.0 Edit Turbo", "mflux-generate-fibo2-edit", "prompt-edit-models/fibo2-edit-turbo", 4, {
            "fonts/Aileron-Regular.otf": 27644,
            "fonts/DejaVuSans.ttf": 757076,
            "mflux_model.json": 461,
            "processor/chat_template.jinja": 5292,
            "processor/preprocessor_config.json": 441,
            "processor/tokenizer.json": 11422650,
            "processor/tokenizer_config.json": 733,
            "text_encoder/0.safetensors": 2135447931,
            "text_encoder/1.safetensors": 307286181,
            "text_encoder/model.safetensors.index.json": 93925,
            "transformer/0.safetensors": 2147208606,
            "transformer/1.safetensors": 2141017746,
            "transformer/2.safetensors": 288231740,
            "transformer/model.safetensors.index.json": 93206,
            "vae/0.safetensors": 165107912,
            "vae/model.safetensors.index.json": 17547,
        }, alignment=32, supports_mask=True,
    ),
}


def windows_x64() -> bool:
    return platform.system() == "Windows" and platform.machine().lower() in {"amd64", "x86_64"}


def active_models() -> dict[str, EditModel] | dict[str, WindowsEditModel]:
    return WINDOWS_MODELS if windows_x64() else EDIT_MODELS


def platform_unavailable_reason() -> str | None:
    if windows_x64() or (platform.system() == "Darwin" and platform.machine() == "arm64"):
        return None
    return "提示词编辑需要 Apple Silicon macOS 或 Windows x64 后端。"


def windows_device() -> str:
    return os.environ.get("HIIMAGE_PROMPT_EDIT_VULKAN_DEVICE", "vulkan0").strip().lower()


def runtime(model: EditModel | WindowsEditModel) -> tuple[Path, Path, str | None]:
    if isinstance(model, WindowsEditModel):
        directory = runtime_directory()
        executable = directory / "sd-cli.exe"
        weights = install_root() / "weights"
        if not re.fullmatch(r"vulkan[0-9]+", windows_device()):
            return executable, weights, "提示词编辑的 Vulkan 设备配置无效，请使用 vulkan0 等设备名。"
        if not runtime_installed(directory):
            return executable, weights, "请运行 scripts/setup_prompt_edit_windows.py 安装 Windows 提示词编辑运行时。"
        if not all(file.installed(weights) for file in model.files):
            return executable, weights, f"{model.name} 权重尚未安装完整，请运行 Windows 提示词编辑安装脚本。"
        return executable, weights, None

    root = (CACHE_ROOT / "hiimage").resolve()
    executable = root / "prompt-edit/bin" / model.command
    weights = root / model.directory
    if reason := platform_unavailable_reason():
        return executable, weights, reason
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return executable, weights, "本地后端尚未安装 MFlux，请按项目说明配置。"
    try:
        complete = all((weights / name).stat().st_size == size for name, size in model.files.items())
    except OSError:
        complete = False
    if not complete:
        return executable, weights, f"{model.name} 权重尚未下载完整。"
    return executable, weights, None


def windows_command(
    model: WindowsEditModel, executable: Path, weights: Path, input_path: Path, output_path: Path,
    prompt_path: Path, width: int, height: int, seed: int,
) -> list[str]:
    device = windows_device()
    command = [
        str(executable), "--diffusion-model", str(model.diffusion.path(weights)),
        "--llm", str(model.encoder.path(weights)), "--vae", str(model.vae.path(weights)),
        "-r", str(input_path), "--prompt-file", str(prompt_path), "-o", str(output_path),
        "--width", str(width), "--height", str(height), "--seed", str(seed),
        "--steps", str(model.steps), "--cfg-scale", str(model.cfg), "--sampling-method", "euler",
        "--backend", f"te=cpu,vae=cpu,diffusion={device}", "--offload-to-cpu",
        "--max-vram", f"{device}={WINDOWS_VRAM_BUDGET_GIB}", "--flash-attn", "off,diffusion=on", "--threads", "10",
    ]
    if model.projector:
        command.extend(("--llm_vision", str(model.projector.path(weights))))
    return command


def edit_dimensions(width: int, height: int, alignment: int = 16) -> tuple[int, int]:
    scale = min(1.0, MAX_EDGE / max(width, height))
    return max(32, round(width * scale / alignment) * alignment), max(32, round(height * scale / alignment) * alignment)


def edit_image(
    model_id: str, model: EditModel | WindowsEditModel, executable: Path, weights: Path,
    encoded: str, prompt: str, seed: int | None,
    encoded_mask: str | None = None,
) -> Image.Image:
    mask = None
    if encoded_mask is None:
        source = Image.fromarray(decode_image(encoded))
    else:
        pixels = decode_rgba_image(encoded, orient=True)
        mask_pixels = decode_mask(encoded_mask)
        validate_matching_dimensions(pixels, mask_pixels)
        mask = Image.fromarray(mask_pixels)
        if not mask.getbbox():
            raise HTTPException(422, "选区为空，请涂抹要修改的区域。")
        source = Image.fromarray(pixels)
        if source.getextrema()[3] == (255, 255):
            source = source.convert("RGB")
    width, height = edit_dimensions(*source.size, alignment=model.alignment)
    working_source = source.resize((width, height), Image.Resampling.LANCZOS)
    with _cli_log_directory() as name:
        directory = Path(name)
        input_path, output_path = directory / "input.png", directory / "result.png"
        prompt_path = directory / "prompt.txt"
        working_source.save(input_path)
        prompt_path.write_text(prompt, encoding="utf-8")
        seed = seed if seed is not None else secrets.randbelow(2**31)
        if isinstance(model, WindowsEditModel):
            engine = "stable-diffusion.cpp"
            command = windows_command(model, executable, weights, input_path, output_path, prompt_path, width, height, seed)
        else:
            engine = "MFlux"
            command = [
                str(executable), "--model", str(weights), "--base-model", model_id,
                "--image-paths", str(input_path), "--prompt-file", str(prompt_path), "--steps", str(model.steps),
                "--width", str(width), "--height", str(height),
                "--seed", str(seed), "--no-exif", "--output", str(output_path),
            ]
        if mask is not None:
            # FIBO thresholds the conditioning mask at 128. Keep the original
            # mask for compositing so the edit never changes unselected pixels.
            working_mask = mask.resize((width, height), Image.Resampling.BOX)
            if working_mask.getextrema()[1] < 128:
                raise HTTPException(422, "选区太小，请扩大选区后重试。")
            mask_path = directory / "mask.png"
            working_mask.save(mask_path)
            command.extend(("--mask-path", str(mask_path)))
        env_keys = ("HOME", "PATH", "TMPDIR", "LANG", "SYSTEMROOT", "TEMP", "TMP")
        env = {key: os.environ[key] for key in env_keys if key in os.environ}
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        try:
            code = _stream_cli_output(command, env, TIMEOUT_SECONDS, lambda _: None, cwd=directory, confirm_group=True)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "本地图片编辑超时，请重试。") from None
        if code:
            log_manager.error(f"{model_id} 的 {engine} 进程退出码 {code}", source="prompt_edit")
            raise HTTPException(502, f"本地模型执行失败，请检查 {engine} 环境和可用内存。")
        try:
            with Image.open(output_path) as result:
                if result.format != "PNG" or result.size != (width, height):
                    raise ValueError("unexpected output")
                generated = result.convert(source.mode)
        except (OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(502, "本地模型返回的结果图像无效。") from None
    if mask is None:
        return generated
    if generated.size != source.size:
        generated = generated.resize(source.size, Image.Resampling.LANCZOS)
    return Image.composite(generated, source, mask)
