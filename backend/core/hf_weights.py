"""轻量校验推理所用的 Hugging Face 快照，避免加载模型权重。"""

import json
from pathlib import Path


def check_file(path: Path, expected_size: int | None = None) -> int:
    size = path.stat().st_size
    if not path.is_file() or size == 0 or (expected_size is not None and size != expected_size):
        raise ValueError(f"文件大小异常: {path.name}")
    if expected_size is not None:
        return size
    if path.suffix == ".safetensors" or (size < 1024 and path.suffix == ".bin"):
        with path.open("rb") as file:
            prefix = file.read(200)
        if prefix.startswith(b"version https://git-lfs.github.com/spec/"):
            raise ValueError(f"权重仍是 Git LFS 指针: {path.name}")
        if path.suffix == ".safetensors":
            header_size = int.from_bytes(prefix[:8], "little")
            if not 0 < header_size <= min(size - 8, 100_000_000):
                raise ValueError(f"Safetensors 文件头异常: {path.name}")
    return size


def read_config(path: Path) -> dict:
    check_file(path)
    try:
        data = json.loads(path.read_bytes())
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"JSON 配置损坏: {path.name}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON 配置格式异常: {path.name}")
    return data


def check_weights(directory: Path, *, diffusers: bool, safetensors_only: bool = False) -> int:
    names = ["diffusion_pytorch_model.safetensors" if diffusers else "model.safetensors"]
    if not safetensors_only:
        names.append("diffusion_pytorch_model.bin" if diffusers else "pytorch_model.bin")
    for name in names:
        # 优先检查加载器默认使用的单文件，然后检查同格式的分片。
        weight = directory / name
        index = directory / (name + ".index.json")
        if weight.is_file():
            return check_file(weight)
        if index.is_file():
            mapping = read_config(index).get("weight_map")
            if not isinstance(mapping, dict) or not mapping or any(
                not isinstance(shard, str) or Path(shard).name != shard or "\\" in shard
                or not shard.endswith(weight.suffix) for shard in mapping.values()
            ):
                raise ValueError(f"无效的权重分片清单: {index.name}")
            return sum(check_file(directory / shard) for shard in set(mapping.values()))
    raise FileNotFoundError(f"{directory.name}/{names[0]}")


def check_snapshot(directory: Path, cfg: dict) -> int:
    if required := cfg.get("hf_required_files"):
        return sum(check_file(directory / name, size) for name, size in required.items())

    if cfg.get("provider") != "diffusers" and cfg.get("executor") != "diffusers":
        config = read_config(directory / "config.json")
        read_config(directory / "preprocessor_config.json")
        if config.get("model_type") == "grounding-dino":
            # GroundingDinoProcessor 同时加载图像预处理器和 BERT 分词器。
            tokenizer = directory / "tokenizer.json"
            if tokenizer.is_file():
                read_config(tokenizer)
            else:
                check_file(directory / "vocab.txt")
        return check_weights(directory, diffusers=False)

    components = read_config(directory / "model_index.json")
    total = 0
    model_components = 0
    for name, component in components.items():
        if name.startswith("_") or not isinstance(component, list) or len(component) != 2:
            continue
        library, class_name = component
        if library is None and class_name is None:
            continue
        if not name.isidentifier() or not isinstance(class_name, str):
            raise ValueError(f"无效的模型组件: {name}")
        if name == "safety_checker" and cfg.get("pipeline") == "instruct_pix2pix":
            continue
        folder = directory / name
        if name == "scheduler":
            read_config(folder / "scheduler_config.json")
        elif name.startswith("tokenizer"):
            read_config(folder / "tokenizer_config.json")
            if class_name.endswith("Fast") and (folder / "tokenizer.json").is_file():
                read_config(folder / "tokenizer.json")
            elif class_name.startswith("CLIP"):
                read_config(folder / "vocab.json")
                check_file(folder / "merges.txt")
            else:
                check_file(folder / "spiece.model")
        elif name in {"feature_extractor", "image_processor"}:
            read_config(folder / "preprocessor_config.json")
        else:
            read_config(folder / "config.json")
            total += check_weights(folder, diffusers=library != "transformers" and name != "safety_checker",
                                   safetensors_only=cfg.get("pipeline") == "sdxl_img2img")
            model_components += 1
    required_components = {"scheduler", "tokenizer", "text_encoder", "vae"}
    required_components.add("transformer" if cfg.get("pipeline") == "flux_img2img" else "unet")
    if cfg.get("pipeline") in {"flux_img2img", "sdxl_img2img"}:
        required_components.update({"tokenizer_2", "text_encoder_2"})
    for name in sorted(required_components):
        component = components.get(name)
        if not isinstance(component, list) or len(component) != 2 or not all(component):
            raise ValueError(f"model_index.json 缺少组件: {name}")
    if not model_components:
        raise ValueError("model_index.json 未声明任何权重组件")
    return total
