"""Windows prompt-edit models and the local stable-diffusion.cpp installation."""

from dataclasses import dataclass
from pathlib import Path

from core.paths import CACHE_ROOT

RUNTIME_RELEASE = "master-1133-52491bd"
RUNTIME_ASSET = "sd-master-52491bd-bin-win-vulkan-x64.zip"
RUNTIME_SHA256 = "976ed26dfc06c16b720fdf272d6445cdd7fe42056f2b1f53e4359e25d5a1d1f1"
RUNTIME_BINARIES = (
    "sd-cli.exe", "stable-diffusion.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu-x64.dll", "ggml-vulkan.dll",
    "libsharpyuv.dll", "webm.dll", "libwebp.dll", "libwebpmux.dll", "libwebpdemux.dll",
)


@dataclass(frozen=True)
class Weight:
    repo: str
    revision: str
    filename: str
    size: int
    sha256: str

    def path(self, weights: Path) -> Path:
        return weights / self.repo / self.filename

    def installed(self, weights: Path) -> bool:
        path = self.path(weights)
        try:
            return path.is_file() and path.stat().st_size == self.size
        except OSError:
            return False


@dataclass(frozen=True)
class WindowsEditModel:
    name: str
    diffusion: Weight
    encoder: Weight
    vae: Weight
    steps: int
    cfg: float = 1.0
    alignment: int = 16
    projector: Weight | None = None
    supports_mask = False

    @property
    def files(self) -> tuple[Weight, ...]:
        required = (self.diffusion, self.encoder, self.vae)
        return required + (self.projector,) if self.projector else required


FLUX_VAE = Weight(
    "Comfy-Org/Fibo-2.0", "4742d4bec410034142c8e65559e5f00c6f29eccb", "vae/flux2-vae.safetensors",
    336211292, "868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3",
)
QWEN_VL_4B = Weight(
    "Qwen/Qwen3-VL-4B-Instruct-GGUF", "1cd86afb9a95c410a6038ab3b40d8b578c892266", "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
    2497281664, "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a",
)

WINDOWS_MODELS = {
    "flux2-klein-4b": WindowsEditModel(
        "FLUX.2 Klein 4B",
        Weight(
            "leejet/FLUX.2-klein-4B-GGUF", "3b1f5a9dc3abb32238b053aeb3d823c30afdacbd", "flux-2-klein-4b-Q4_0.gguf",
            2460378560, "d1023499ef3f2f82ff7c50e6778495195c1b6cc34835741778868428111f9ff4",
        ),
        Weight(
            "unsloth/Qwen3-4B-GGUF", "22c9fc8a8c7700b76a1789366280a6a5a1ad1120", "Qwen3-4B-Q4_K_M.gguf",
            2497281312, "f6f851777709861056efcdad3af01da38b31223a3ba26e61a4f8bf3a2195813a",
        ),
        FLUX_VAE, steps=4,
    ),
    "qwen-image-2.1": WindowsEditModel(
        "Qwen-Image-2.1",
        Weight(
            "leejet/Qwen-Image-2.1-GGUF", "0bca76da6edd8a18211e9717454615c1bbaaf6ed", "qwen_image_2.1-Q4_K.gguf",
            4197494816, "29f9c83c249ff0292fb2943fceddfa2319b446601866c82a4f8be062abea72c2",
        ),
        Weight(
            "Qwen/Qwen3-VL-8B-Instruct-GGUF", "f982a07559d4a2f6c8744d840bf6fccab30eea96", "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
            5027784800, "67d1659bfe71b89d50b45a4ad1a9e5b997e5bb16ce5da66a6a6167abd569e9e2",
        ),
        Weight(
            "Comfy-Org/Qwen-Image-2.1", "aacf31a6d48e707f352ecba714cee0680da3479b", "vae/qwen_image_2.1_vae_bf16.safetensors",
            675509688, "bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9",
        ),
        steps=30, cfg=6.0, alignment=32,
        projector=Weight(
            "Qwen/Qwen3-VL-8B-Instruct-GGUF", "f982a07559d4a2f6c8744d840bf6fccab30eea96", "mmproj-Qwen3VL-8B-Instruct-F16.gguf",
            1159029824, "ca524100ebf825c9a870db1c580d03879e0da0ab2541697e2458e64891cf9d38",
        ),
    ),
    "flux3-image-klein": WindowsEditModel(
        "FLUX 3 Image Klein",
        Weight(
            "leejet/flux-3-image-klein-GGUF", "57dfa91df2284f4985a4107c399d05d12e0f9b63", "flux3-image-klein-Q4_K.gguf",
            3514023104, "19ddbfbed61da4bb16ebf169aaf48e9e5db43aba557be1ee537e9f00f586092e",
        ),
        QWEN_VL_4B, FLUX_VAE, steps=16,
    ),
    "fibo2-edit-turbo": WindowsEditModel(
        "FIBO 2.0 Edit Turbo",
        Weight(
            "Comfy-Org/Fibo-2.0", "4742d4bec410034142c8e65559e5f00c6f29eccb", "diffusion_models/fibo2_edit_turbo_int8_convrot.safetensors",
            8408562474, "1f6fa7c499191f405467123f655f11762661eaa5bd479d1da8f161398ef72c3b",
        ),
        QWEN_VL_4B, FLUX_VAE, steps=4, alignment=32,
        projector=Weight(
            "Qwen/Qwen3-VL-4B-Instruct-GGUF", "1cd86afb9a95c410a6038ab3b40d8b578c892266", "mmproj-Qwen3VL-4B-Instruct-F16.gguf",
            836180256, "256f3a43bd4205ffef48d6b92715e1e70b5b0e9aef06522584967513a9985331",
        ),
    ),
}


def install_root() -> Path:
    return (CACHE_ROOT / "hiimage" / "prompt-edit-windows").resolve()


def runtime_directory() -> Path:
    return install_root() / RUNTIME_RELEASE


def runtime_installed(directory: Path) -> bool:
    return all((directory / filename).is_file() for filename in RUNTIME_BINARIES)
