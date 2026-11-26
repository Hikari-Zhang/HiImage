"""Install the local Windows prompt-edit runtime and selected model weights."""

import argparse
from hashlib import file_digest
from pathlib import Path
import platform
import shutil
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile

from huggingface_hub import hf_hub_download

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.paths import get_hf_hub_dir
from core.prompt_edit_windows import (
    RUNTIME_ASSET, RUNTIME_RELEASE, RUNTIME_SHA256, WINDOWS_MODELS, Weight,
    install_root, runtime_directory, runtime_installed,
)

RUNTIME_URL = f"https://github.com/leejet/stable-diffusion.cpp/releases/download/{RUNTIME_RELEASE}/{RUNTIME_ASSET}"
DEFAULT_MODELS = ["flux2-klein-4b"]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def install_runtime(archive: Path | None) -> None:
    destination = runtime_directory()
    if runtime_installed(destination):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="runtime-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        if archive is None:
            archive = stage / RUNTIME_ASSET
            print(f"Downloading {RUNTIME_ASSET}", flush=True)
            with urlopen(RUNTIME_URL, timeout=60) as response, archive.open("wb") as stream:
                shutil.copyfileobj(response, stream)
        if sha256(archive) != RUNTIME_SHA256:
            raise ValueError(f"SHA-256 mismatch: {archive}")
        release = stage / "release"
        with ZipFile(archive) as package:
            package.extractall(release)
        if not runtime_installed(release):
            raise ValueError("Incomplete stable-diffusion.cpp release")
        try:
            release.rename(destination)
        except FileExistsError:
            for file in release.iterdir():
                installed = destination / file.name
                if not installed.is_file():
                    file.replace(installed)
        print(f"Installed runtime at {destination}", flush=True)


def weight_source(weight: Weight, local_dirs: list[Path]) -> Path:
    for directory in local_dirs:
        for path in (weight.path(directory), directory / weight.filename, directory / Path(weight.filename).name):
            if path.is_file() and path.stat().st_size == weight.size and sha256(path) == weight.sha256:
                return path
    print(f"Downloading {weight.repo}/{weight.filename}", flush=True)
    return Path(hf_hub_download(
        weight.repo, weight.filename, revision=weight.revision, cache_dir=get_hf_hub_dir(),
    ))


def install_weight(weight: Weight, local_dirs: list[Path]) -> None:
    weights = install_root() / "weights"
    destination = weight.path(weights)
    if weight.installed(weights) and sha256(destination) == weight.sha256:
        return
    source = weight_source(weight, local_dirs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="weight-", dir=destination.parent) as temporary:
        stage = Path(temporary) / destination.name
        try:
            stage.hardlink_to(source.resolve())
        except OSError:
            shutil.copyfile(source, stage)
        if stage.stat().st_size != weight.size or sha256(stage) != weight.sha256:
            raise ValueError(f"SHA-256 or size mismatch: {source}")
        stage.replace(destination)
    print(f"Installed {weight.filename}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selected = parser.add_mutually_exclusive_group()
    selected.add_argument("--models", nargs="+", choices=WINDOWS_MODELS, help="Install only the listed models")
    selected.add_argument("--all", action="store_true", help="Install all Windows prompt-edit models")
    parser.add_argument("--runtime-zip", type=Path, help="Use a previously downloaded official release ZIP")
    parser.add_argument("--weights-from", type=Path, action="append", default=[], help="Reuse a local weight directory")
    args = parser.parse_args()
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        parser.error("the bundled runtime requires Windows x64")
    models = list(WINDOWS_MODELS) if args.all else args.models or DEFAULT_MODELS
    files = dict.fromkeys(weight for model in models for weight in WINDOWS_MODELS[model].files)
    try:
        install_runtime(args.runtime_zip)
        for weight in files:
            install_weight(weight, args.weights_from)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"Setup failed: {error}\n")
    print(f"Ready: {', '.join(models)}", flush=True)


if __name__ == "__main__":
    main()
