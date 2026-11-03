"""模型配置静态契约；不导入推理库，不检查权重或触发下载。"""
from __future__ import annotations

import math
import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any


SCHEMA_VERSION = 1

# 配置只能选择已知标识，不能指定可导入的 Python 类。
# pipeline -> (executor, 兼容 provider, architecture 集合, capabilities 集合)
PIPELINE_SPECS = {
    "lama": ("lama", "lama", {"big_lama"}, {"inpaint"}),
    "rembg": ("rembg", "rembg", {"birefnet", "rmbg2", "isnet", "u2net"}, {"matting"}),
    "flux_fill": ("diffusers", "diffusers", {"flux_fill"}, {"inpaint"}),
    "flux_img2img": ("diffusers", "diffusers", {"flux"}, {"image_edit"}),
    "sdxl_img2img": ("diffusers", "diffusers", {"sdxl"}, {"image_edit"}),
    "instruct_pix2pix": ("diffusers", "diffusers", {"instruct_pix2pix"}, {"image_edit"}),
    "gfpgan": ("gfpgan", "facexlib", {"gfpgan"}, {"face_enhance"}),
    "grounded_sam_flux": ("composite", "HiImage", {"grounding_dino_sam_flux_fill"}, {"segmentation", "inpaint"}),
    "segformer_hsv": ("composite", "HiImage", {"segformer"}, {"segmentation", "color_edit"}),
    "realesrgan": ("realesrgan", "realesrgan", {"RRDBNet", "SRVGGNetCompact"}, {"upscale"}),
}

MODE_CAPABILITIES = {
    "background_replace": {"matting"}, "cutout": {"matting"},
    "outfit_swap": {"inpaint"}, "virtual_tryon": {"inpaint"},
    "prompt_inpaint": {"inpaint"}, "watermark_removal": {"inpaint"},
    "face_swap": {"inpaint", "face_enhance"},
    "auto_segment_edit": {"segmentation"},
    "instruction_edit": {"image_edit"}, "upscale": {"upscale"},
}

_GENERATION_PARAMS = {"num_inference_steps", "guidance_scale", "seed", "prompt"}
PIPELINE_PARAMS = {
    "lama": set(),
    "rembg": set(), "flux_fill": _GENERATION_PARAMS,
    "flux_img2img": _GENERATION_PARAMS | {"strength"},
    "sdxl_img2img": _GENERATION_PARAMS | {"strength", "negative_prompt"},
    "instruct_pix2pix": _GENERATION_PARAMS | {"image_guidance_scale"},
    "gfpgan": {"upscale"}, "grounded_sam_flux": _GENERATION_PARAMS,
    "segformer_hsv": {"prompt", "feather_px", "saturation_boost"},
    "realesrgan": set(),
}
PARAMETER_TYPES = {
    "num_inference_steps": "int", "guidance_scale": "float", "seed": "int", "prompt": "str",
    "strength": "float", "negative_prompt": "str", "image_guidance_scale": "float",
    "upscale": "int", "feather_px": "int", "saturation_boost": "float",
}


class ModelConfigError(ValueError):
    """字段位置和原因可直接用于定位 YAML 错误。"""


def _require(condition: bool, location: str, message: str) -> None:
    if not condition:
        raise ModelConfigError(f"{location}: {message}")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _strings(value: Any, location: str) -> list[str]:
    _require(isinstance(value, list) and bool(value), location, "需要非空字符串列表")
    _require(all(_text(item) for item in value), location, "条目必须为非空字符串")
    _require(len(value) == len(set(value)), location, "条目不能重复")
    return value


def _parameters(value: Any, location: str) -> None:
    _require(isinstance(value, dict), location, "需要参数定义映射")
    for name, spec in value.items():
        where = f"{location}.{name}"
        _require(_text(name) and isinstance(spec, dict), where, "需要有效参数名称和定义")
        kind = spec.get("type")
        _require(kind in ("int", "float", "str", "bool"), where + ".type", "未知参数类型")
        _require("default" in spec, where + ".default", "缺少默认值")
        default = spec["default"]
        valid = {"int": type(default) is int, "float": _number(default),
                 "str": isinstance(default, str), "bool": type(default) is bool}[kind]
        _require(valid, where + ".default", "默认值类型不匹配或不是有限数值")
        if "range" in spec:
            bounds = spec["range"]
            _require(kind in ("int", "float") and isinstance(bounds, list) and len(bounds) == 2
                     and all(_number(item) for item in bounds), where + ".range", "需要两个有限数值边界")
            _require(bounds[0] <= bounds[1], where + ".range", "下限不能大于上限")
            _require(kind != "int" or all(type(item) is int for item in bounds),
                     where + ".range", "整数参数需要整数边界")
            _require(bounds[0] <= default <= bounds[1], where + ".default", "默认值超出范围")


def _hf_required_files(config: dict, location: str) -> None:
    if "hf_required_files" not in config:
        return
    files = config["hf_required_files"]
    where = location + ".hf_required_files"
    _require(isinstance(files, dict) and "model_index.json" in files, where, "需要 Diffusers 文件清单")
    for name, size in files.items():
        _require(_text(name) and "\\" not in name and ":" not in name
                 and not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts,
                 where, "文件名必须是仓库内的相对路径")
        _require(type(size) is int and size > 0, where + "." + name, "字节数必须为正整数")


def validate_registry(document: Any) -> tuple[list[dict], list[dict]]:
    """校验完整文档并返回独立副本，展开模式关联；失败不产生半成品。"""
    _require(isinstance(document, dict), "registry", "顶层需要映射")
    _require(type(document.get("schema_version")) is int
             and document["schema_version"] == SCHEMA_VERSION, "schema_version", "需要版本 1")
    data = deepcopy(document)
    models, modes = data.get("models"), data.get("mode_groups")
    _require(isinstance(models, list) and bool(models), "models", "需要非空模型列表")
    _require(isinstance(modes, list) and bool(modes), "mode_groups", "需要非空模式列表")

    def index(records, section):
        result = {}
        for position, record in enumerate(records):
            where = f"{section}[{position}]"
            _require(isinstance(record, dict), where, "需要映射")
            identifier = record.get("id")
            _require(_text(identifier), where + ".id", "缺少非空 ID")
            where = f"{section}[{identifier}]"
            _require(identifier not in result, where + ".id", "ID 重复")
            _require(_text(record.get("name")), where + ".name", "缺少名称")
            result[identifier] = record
        return result

    by_id = index(models, "models")
    mode_by_id = index(modes, "mode_groups")
    for mid, model in by_id.items():
        where = f"models[{mid}]"
        _hf_required_files(model, where)
        pipeline = model.get("pipeline")
        _require(isinstance(pipeline, str) and pipeline in PIPELINE_SPECS,
                 where + ".pipeline", "未知或缺失管线标识")
        executor, provider, architectures, capabilities = PIPELINE_SPECS[pipeline]
        _require(model.get("executor") == executor, where + ".executor", "执行器与管线不匹配")
        _require(model.get("provider") == provider, where + ".provider", "兼容 provider 与执行器不匹配")
        architecture = model.get("architecture")
        _require(isinstance(architecture, str) and architecture in architectures,
                 where + ".architecture", "架构与管线不匹配")
        declared = set(_strings(model.get("capabilities"), where + ".capabilities"))
        _require(declared == capabilities, where + ".capabilities", "能力与管线契约不匹配")
        for tag in _strings(model.get("tags"), where + ".tags"):
            _require(tag in mode_by_id, where + ".tags", f"模式不存在: {tag}")
            _require(tag in MODE_CAPABILITIES and bool(declared & MODE_CAPABILITIES[tag]),
                     where + ".capabilities", f"不支持所属模式: {tag}")
        _parameters(model.get("supported_params"), where + ".supported_params")
        _require(set(model["supported_params"]) <= PIPELINE_PARAMS[pipeline],
                 where + ".supported_params", "存在管线不支持的参数")
        for name, parameter in model["supported_params"].items():
            _require(parameter["type"] == PARAMETER_TYPES[name],
                     where + ".supported_params." + name + ".type", "参数类型与管线契约不匹配")
        _require(not ("hf_model_id" in model and "hf_models" in model),
                 where, "hf_model_id 与 hf_models 不能同时声明")
        if "dependencies" in model:
            if model["dependencies"] != []:
                _strings(model["dependencies"], where + ".dependencies")
        if executor == "rembg":
            for field in ("rembg_session_name", "onnx_filename", "github_download_url"):
                _require(_text(model.get(field)), where + "." + field, "缺少抠图模型加载或下载信息")
        elif executor in ("diffusers", "composite"):
            if pipeline == "grounded_sam_flux":
                repos = model.get("hf_models")
                _require(isinstance(repos, list) and bool(repos), where + ".hf_models", "缺少组合模型仓库")
                _require(all(isinstance(repo, dict) and _text(repo.get("id")) for repo in repos),
                         where + ".hf_models", "每个子模型需要仓库 ID")
                _require(len({repo["id"] for repo in repos}) == len(repos),
                         where + ".hf_models", "组合仓库不能重复")
                for repo in repos:
                    _hf_required_files(repo, where + ".hf_models[" + repo["id"] + "]")
            else:
                _require(_text(model.get("hf_model_id")), where + ".hf_model_id", "缺少模型仓库 ID")
        else:
            for field in ("local_path", "download_url"):
                _require(_text(model.get(field)), where + "." + field, "缺少权重或下载位置")
        if executor == "lama":
            digest = model.get("sha256")
            _require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                     where + ".sha256", "需要权重的 SHA-256")
            _require(model.get("device_override") == "cpu", where + ".device_override", "LaMa 当前使用 CPU")
        if executor == "realesrgan":
            _require(model.get("arch") == architecture, where + ".arch", "旧架构字段与 architecture 不一致")
            _require(type(model.get("scale")) is int and model["scale"] in (2, 4), where + ".scale", "倍率必须为 2 或 4")
            field = "num_block" if architecture == "RRDBNet" else "num_conv"
            _require(type(model.get(field)) is int and model[field] > 0, where + "." + field, "需要正整数网络层数")
        if "device_override" in model and model["device_override"] is not None:
            _require(model["device_override"] in ("cpu", "cuda", "mps"), where + ".device_override", "未知设备")

    for mode_id, mode in mode_by_id.items():
        where = f"mode_groups[{mode_id}]"
        _require(type(mode.get("available")) is bool, where + ".available", "需要布尔值")
        default = mode.get("default_model")
        if mode["available"]:
            _require(_text(default) and default in by_id, where + ".default_model", "可用模式需要有效默认模型")
            _require(mode_id in by_id[default]["tags"], where + ".default_model", "默认模型不属于此模式")
        else:
            _require(default is None, where + ".default_model", "暂停模式必须使用 null 默认值")
            _require(_text(mode.get("unavailable_reason")), where + ".unavailable_reason", "缺少暂停原因")
        mode["models"] = [model["id"] for model in models if mode_id in model["tags"]]
    return models, modes
