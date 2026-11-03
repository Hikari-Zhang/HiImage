"""Validate HiImage Docker and Compose configuration without building AI images."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent.parent
RUNTIME_TARGETS = {
    "/app/config",
    "/app/cache",
    "/app/tmp",
    "/app/output",
    "/app/models",
}


def require(condition: bool, message: str) -> None:
    """Raise a readable validation error when a required invariant is absent."""
    if not condition:
        raise AssertionError(message)


def read(relative_path: str) -> str:
    """Read a repository file as UTF-8 text."""
    return (ROOT / relative_path).read_text(encoding="utf-8")


def dockerfile_instructions(content: str) -> list[str]:
    """Collapse Dockerfile continuations into logical instructions."""
    instructions: list[str] = []
    pending = ""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        instructions.append(pending)
        pending = ""
    if pending:
        instructions.append(pending)
    return instructions


def strip_nginx_comment(line: str) -> str:
    """Strip an Nginx comment while preserving hash characters inside quotes."""
    quote: str | None = None
    escaped = False
    result: list[str] = []
    for character in line:
        if escaped:
            result.append(character)
            escaped = False
            continue
        if character == "\\":
            result.append(character)
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            result.append(character)
            continue
        if character == "#" and quote is None:
            break
        result.append(character)
    return "".join(result)


def normalize_directive(value: str) -> str:
    """Normalize insignificant whitespace in one configuration directive."""
    return " ".join(value.split())


def parse_nginx_directives(content: str) -> list[tuple[tuple[str, ...], str]]:
    """Parse effective Nginx directives and retain their enclosing block path."""
    effective = "\n".join(strip_nginx_comment(line) for line in content.splitlines())
    directives: list[tuple[tuple[str, ...], str]] = []
    stack: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    escaped = False

    for character in effective:
        if escaped:
            buffer.append(character)
            escaped = False
            continue
        if character == "\\":
            buffer.append(character)
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            buffer.append(character)
            continue
        if quote is not None:
            buffer.append(character)
            continue
        if character == "{":
            block = normalize_directive("".join(buffer))
            require(bool(block), "Nginx block header cannot be empty")
            stack.append(block)
            buffer = []
        elif character == ";":
            directive = normalize_directive("".join(buffer))
            require(bool(directive), "Nginx directive cannot be empty")
            directives.append((tuple(stack), directive))
            buffer = []
        elif character == "}":
            require(not normalize_directive("".join(buffer)), "Nginx block has dangling content")
            require(bool(stack), "Nginx configuration has an unmatched closing brace")
            stack.pop()
            buffer = []
        else:
            buffer.append(character)

    require(quote is None, "Nginx configuration has an unterminated quote")
    require(not stack, "Nginx configuration has an unclosed block")
    require(not normalize_directive("".join(buffer)), "Nginx configuration has dangling content")
    return directives


def validate_nginx_config(content: str) -> None:
    """Validate effective proxy, SPA, upload, timeout, and cache directives."""
    records = parse_nginx_directives(content)

    def has(directive: str, required_block: str | None = None) -> bool:
        return any(
            value == directive
            and (required_block is None or required_block in context)
            for context, value in records
        )

    require(has("client_max_body_size 80m", "http"), "Nginx upload limit must be 80m")
    require(has("listen 80", "server"), "Nginx must listen on port 80")
    require(
        has("try_files $uri $uri/ /index.html", "location /"),
        "Nginx SPA fallback is missing",
    )

    api_directives = {
        value
        for context, value in records
        if "location /api/" in context
    }
    required_api = {
        "proxy_pass http://backend:8787",
        "proxy_set_header Upgrade $http_upgrade",
        "proxy_set_header Connection $connection_upgrade",
        "proxy_set_header X-Forwarded-For $remote_addr",
        "proxy_set_header X-Forwarded-Proto $scheme",
        "proxy_set_header X-Forwarded-Host $http_host",
        "proxy_buffering off",
        "proxy_cache off",
        "add_header X-Accel-Buffering no always",
        "proxy_connect_timeout 60s",
        "proxy_send_timeout 1800s",
        "proxy_read_timeout 1800s",
    }
    for directive in required_api:
        require(directive in api_directives, f"Nginx API directive missing: {directive}")

    map_contexts = {
        context
        for context, _ in records
        if "map $http_upgrade $connection_upgrade" in context
    }
    require(bool(map_contexts), "Nginx WebSocket connection map is missing")
    require(has("default upgrade", "map $http_upgrade $connection_upgrade"), "Nginx map upgrade")
    require(has("'' close", "map $http_upgrade $connection_upgrade"), "Nginx map close")

    static_contexts = {
        context
        for context, _ in records
        if any(block.startswith("location ~* ") for block in context)
    }
    require(len(static_contexts) == 1, "Nginx must define one static asset cache location")
    static_context = next(iter(static_contexts))
    static_directives = {
        value for context, value in records if context == static_context
    }
    require("expires 1y" in static_directives, "Nginx static assets must expire after one year")
    require(
        'add_header Cache-Control "public, immutable"' in static_directives,
        "Nginx static assets need immutable cache control",
    )
    require("try_files $uri =404" in static_directives, "Nginx static assets need strict lookup")


def validate_frontend_dockerfile(content: str) -> None:
    """Validate the effective final frontend image stage and non-root runtime."""
    instructions = dockerfile_instructions(content)
    from_indexes = [
        index for index, instruction in enumerate(instructions) if instruction.startswith("FROM ")
    ]
    require(len(from_indexes) == 2, "frontend image must have build and runtime stages")
    require(
        instructions[from_indexes[0]] == "FROM node:22-alpine AS build",
        "frontend build stage must use pinned Node",
    )
    final_stage = instructions[from_indexes[-1] :]
    require(
        final_stage[0] == "FROM nginxinc/nginx-unprivileged:1.28-alpine",
        "frontend final stage must use pinned unprivileged Nginx",
    )
    require("RUN npm run build" in instructions[: from_indexes[-1]], "frontend must build Vite")
    require(
        "COPY frontend/nginx.conf /etc/nginx/nginx.conf" in final_stage,
        "frontend final stage must copy nginx.conf",
    )
    require(
        "COPY --from=build /app/dist /usr/share/nginx/html" in final_stage,
        "frontend final stage must copy Vite dist",
    )
    require("EXPOSE 80" in final_stage, "frontend final stage must expose port 80")
    require(
        any(
            instruction.startswith("HEALTHCHECK ")
            and "http://127.0.0.1/" in instruction
            for instruction in final_stage
        ),
        "frontend final stage must define an HTTP healthcheck",
    )
    require(
        any("setcap cap_net_bind_service=+ep /usr/sbin/nginx" in item for item in final_stage),
        "frontend final stage must grant low-port bind capability",
    )
    user_instructions = [
        instruction for instruction in final_stage if instruction.startswith("USER ")
    ]
    require(bool(user_instructions), "frontend final stage must set a runtime user")
    require(
        user_instructions[-1] in {"USER 101", "USER nginx"},
        "frontend final USER must be the non-root Nginx user",
    )


def validate_flux_install(run_instructions: list[str], source: str) -> None:
    """Native dependencies must use the normal resolver and be validated."""
    require(not any("--no-deps" in line for line in run_instructions),
            f"{source} must not bypass dependency resolution")
    require(any("python -m pip check" in line for line in run_instructions),
            f"{source} must run pip check")


def validate_backend_install_steps(content: str, source: str) -> list[str]:
    """Validate shared backend dependency installation and patch execution."""
    instructions = dockerfile_instructions(content)
    run_instructions = [line for line in instructions if line.startswith("RUN ")]
    require(
        any(
            "python -m pip install" in line and "-r /tmp/requirements.txt" in line
            for line in run_instructions
        ),
        f"{source} must install backend requirements",
    )
    require("backend/requirements-inference.txt" in content,
            f"{source} must copy the shared inference requirements")
    validate_flux_install(run_instructions, source)
    require(
        any(
            re.fullmatch(r"RUN python /app/scripts/post_install\.py", line)
            for line in run_instructions
        ),
        f"{source} must run post_install.py",
    )
    require("install_deps.py" not in content, f"{source} must not run install_deps.py")

    user_instructions = [
        instruction for instruction in instructions if instruction.startswith("USER ")
    ]
    require(bool(user_instructions), f"{source} must set a runtime user")
    require(
        user_instructions[-1] == "USER hiimage",
        f"{source} final USER must be hiimage",
    )

    command_instructions = [
        instruction for instruction in instructions if instruction.startswith("CMD ")
    ]
    expected_command = 'CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8787"]'
    require(
        bool(command_instructions) and command_instructions[-1] == expected_command,
        f"{source} final CMD must run python run.py on 0.0.0.0:8787",
    )

    healthchecks = [
        instruction
        for instruction in instructions
        if instruction.startswith("HEALTHCHECK ")
    ]
    require(
        len(healthchecks) == 1
        and "CMD curl -fsS http://127.0.0.1:8787/api/health || exit 1"
        in healthchecks[0],
        f"{source} healthcheck must curl -fsS /api/health",
    )
    return run_instructions


def validate_cpu_dockerfile(content: str) -> None:
    """Validate reproducible CPU-specific PyTorch and backend installation."""
    run_instructions = validate_backend_install_steps(content, "CPU Dockerfile")
    require(
        re.search(r"^ARG TORCH_VERSION=\d+\.\d+\.\d+$", content, re.MULTILINE) is not None,
        "CPU Dockerfile must declare an explicit torch version",
    )
    require(
        re.search(r"^ARG TORCHVISION_VERSION=\d+\.\d+\.\d+$", content, re.MULTILINE)
        is not None,
        "CPU Dockerfile must declare an explicit torchvision version",
    )
    torch_run = next(
        (
            line
            for line in run_instructions
            if "torch==${TORCH_VERSION}" in line
            and "torchvision==${TORCHVISION_VERSION}" in line
        ),
        "",
    )
    require(torch_run, "CPU Dockerfile must install explicit torch/torchvision versions")
    require(
        "https://download.pytorch.org/whl/cpu" in torch_run,
        "CPU Dockerfile must use the CPU PyTorch index",
    )


def validate_cuda_dockerfile(content: str) -> None:
    """Validate the CUDA runtime and reproducible backend installation."""
    validate_backend_install_steps(content, "CUDA Dockerfile")
    require(
        re.search(
            r"^FROM pytorch/pytorch:[^\s]*cuda12\.4[^\s]*-runtime$",
            content,
            re.MULTILINE,
        )
        is not None,
        "CUDA Dockerfile must use the official PyTorch CUDA 12.4 runtime",
    )
    require(
        re.search(r"\bNVIDIA_VISIBLE_DEVICES=all\b", content) is not None,
        "CUDA Dockerfile missing NVIDIA_VISIBLE_DEVICES",
    )
    require(
        re.search(r"\bNVIDIA_DRIVER_CAPABILITIES=compute,utility\b", content) is not None,
        "CUDA Dockerfile missing NVIDIA_DRIVER_CAPABILITIES",
    )
    require("torch==2.5.1" in content, "CUDA Dockerfile torch constraint must be 2.5.1")
    require(
        "torchvision==0.20.1" in content,
        "CUDA Dockerfile torchvision constraint must be 0.20.1",
    )
    require(
        "torchaudio==2.5.1" in content,
        "CUDA Dockerfile torchaudio constraint must be 2.5.1",
    )
    require(
        "-c /tmp/constraints.txt" in content,
        "CUDA requirements install must apply constraints",
    )


def parse_compose_yaml(content: str) -> dict[str, Any]:
    """Safely parse Compose YAML, including anchor expansion."""
    document = yaml.safe_load(content)
    require(isinstance(document, dict), "docker-compose.yml must contain a mapping")
    require(isinstance(document.get("services"), dict), "Compose services must be a mapping")
    return document


def validate_env_example(content: str) -> None:
    """Require documented, safe defaults for phase-one backend controls."""
    values = {
        key.strip(): value.strip()
        for raw_line in content.splitlines()
        if raw_line.strip() and not raw_line.lstrip().startswith("#") and "=" in raw_line
        for key, value in [raw_line.split("=", 1)]
    }
    require(
        values.get("HIIMAGE_MAX_PENDING_INFERENCE") == "4",
        ".env.example HIIMAGE_MAX_PENDING_INFERENCE must default to 4",
    )
    require(
        "HIIMAGE_WS_ORIGINS" in values and values["HIIMAGE_WS_ORIGINS"] == "",
        ".env.example HIIMAGE_WS_ORIGINS must default to empty same-origin mode",
    )
    require(
        values.get("HIIMAGE_TRUST_PROXY_HEADERS") == "false",
        ".env.example HIIMAGE_TRUST_PROXY_HEADERS must default to false",
    )


def compose_json(*extra_args: str) -> dict[str, Any]:
    """Render normalized Compose JSON using Docker Compose."""
    command = [
        "docker",
        "compose",
        *extra_args,
        "-f",
        str(ROOT / "docker-compose.yml"),
        "config",
        "--format",
        "json",
    ]
    environment = os.environ.copy()
    environment.pop("WEB_HOST", None)
    environment.pop("WEB_PORT", None)
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command)} failed:\n{result.stdout}\n{result.stderr}".strip()
        )
    return json.loads(result.stdout)


def dependency_closure(services: dict[str, Any], selected: set[str]) -> set[str]:
    """Return selected Compose services and their transitive dependencies."""
    closure = set(selected)
    pending = list(selected)
    while pending:
        service_name = pending.pop()
        dependencies = services[service_name].get("depends_on", {})
        for dependency in dependencies:
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return closure


def volume_targets(service: dict[str, Any]) -> set[str]:
    """Extract normalized container targets from a Compose service."""
    return {volume["target"] for volume in service.get("volumes", [])}


def volume_sources(service: dict[str, Any]) -> set[str]:
    """Extract normalized named-volume sources from a Compose service."""
    return {volume["source"] for volume in service.get("volumes", [])}


def static_networks(service: dict[str, Any]) -> set[str]:
    """Extract network names from short or long Compose YAML syntax."""
    networks = service.get("networks", {})
    return set(networks if isinstance(networks, (list, dict)) else [])


def static_volumes(service: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Extract named-volume sources and targets from Compose short syntax."""
    sources: set[str] = set()
    targets: set[str] = set()
    for volume in service.get("volumes", []):
        require(isinstance(volume, str) and ":" in volume, "volumes must use named short syntax")
        source, target = volume.split(":", 1)
        sources.add(source)
        targets.add(target)
    return sources, targets


def validate_service_healthcheck(
    service: dict[str, Any],
    name: str,
    expected_test: list[str],
) -> None:
    """Require the exact executable health probe for a Compose service."""
    healthcheck = service.get("healthcheck", {})
    require(
        isinstance(healthcheck, dict) and healthcheck.get("test") == expected_test,
        f"{name} healthcheck must run {' '.join(expected_test[1:])}",
    )


def validate_web_service(
    service: dict[str, Any],
    name: str,
    backend_name: str,
    network_name: str,
    normalized: bool,
) -> None:
    """Validate web binding, health-gated dependency, and isolated network."""
    validate_service_healthcheck(
        service,
        name,
        ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/"],
    )
    ports = service.get("ports", [])
    require(len(ports) == 1, f"{name} must publish exactly one port")
    if normalized:
        port = ports[0]
        require(port.get("host_ip") == "127.0.0.1", f"{name} must bind 127.0.0.1")
        require(str(port.get("published")) == "8080", f"{name} must publish port 8080")
        require(int(port.get("target")) == 80, f"{name} must target port 80")
    else:
        require(
            ports == ["${WEB_HOST:-127.0.0.1}:${WEB_PORT:-8080}:80"],
            f"{name} default host/port must be 127.0.0.1:8080:80",
        )

    dependencies = service.get("depends_on", {})
    require(set(dependencies) == {backend_name}, f"{name} must depend only on {backend_name}")
    require(
        dependencies[backend_name].get("condition") == "service_healthy",
        f"{name} dependency must use service_healthy",
    )
    networks = (
        set(service.get("networks", {}))
        if normalized
        else static_networks(service)
    )
    require(networks == {network_name}, f"{name} must use only the {network_name} network")


def validate_backend_service(
    service: dict[str, Any],
    name: str,
    network_name: str,
    normalized: bool,
) -> set[str]:
    """Validate backend exposure, alias, paths, mounts, and build context."""
    validate_service_healthcheck(
        service,
        name,
        ["CMD", "curl", "-fsS", "http://127.0.0.1:8787/api/health"],
    )
    require("ports" not in service, f"{name} must not publish host ports")
    require([str(value) for value in service.get("expose", [])] == ["8787"], f"{name} expose")
    networks = service.get("networks", {})
    require(set(networks) == {network_name}, f"{name} must use only {network_name} network")
    network_config = networks[network_name]
    require(
        isinstance(network_config, dict)
        and "backend" in network_config.get("aliases", []),
        f"{name} requires the backend alias",
    )

    environment = service.get("environment", {})
    expected_paths = {
        "HIIMAGE_PROJECT_ROOT": "/app",
        "HIIMAGE_CONFIG_DIR": "/app/config",
        "HIIMAGE_CACHE": "/app/cache",
        "HIIMAGE_TMP_DIR": "/app/tmp",
        "HIIMAGE_OUTPUT_DIR": "/app/output",
        "HIIMAGE_MODELS_DIR": "/app/models",
    }
    require(
        all(environment.get(variable) == path for variable, path in expected_paths.items()),
        f"{name} path environment mismatch",
    )
    require(environment.get("HIIMAGE_CORS_ORIGINS") == "", f"{name} CORS must be same-origin")
    require(
        environment.get("HIIMAGE_WS_ORIGINS") == "",
        f"{name} HIIMAGE_WS_ORIGINS must use same-origin mode",
    )
    require(
        str(environment.get("HIIMAGE_TRUST_PROXY_HEADERS")).lower() == "true",
        f"{name} HIIMAGE_TRUST_PROXY_HEADERS must be true behind Nginx",
    )
    require(
        str(environment.get("HIIMAGE_MAX_PENDING_INFERENCE")) == "4",
        f"{name} HIIMAGE_MAX_PENDING_INFERENCE must default to 4",
    )
    expected_device = "cuda" if name == "backend-gpu" else "cpu"
    require(
        environment.get("HIIMAGE_DEFAULT_DEVICE") == expected_device,
        f"{name} default device must be {expected_device}",
    )

    if normalized:
        require(volume_targets(service) == RUNTIME_TARGETS, f"{name} mount mismatch")
        sources = volume_sources(service)
    else:
        sources, targets = static_volumes(service)
        require(targets == RUNTIME_TARGETS, f"{name} mount mismatch")
    return sources


def validate_gpu_device(service: dict[str, Any]) -> None:
    """Require one NVIDIA GPU reservation."""
    devices = (
        service.get("deploy", {})
        .get("resources", {})
        .get("reservations", {})
        .get("devices", [])
    )
    require(len(devices) == 1, "backend-gpu must reserve one NVIDIA device")
    device = devices[0]
    require(device.get("driver") == "nvidia", "backend-gpu NVIDIA device driver missing")
    require(device.get("count") == 1, "backend-gpu NVIDIA device count must be one")
    require("gpu" in device.get("capabilities", []), "backend-gpu NVIDIA device needs gpu capability")


def validate_static_compose(document: dict[str, Any]) -> None:
    """Validate source Compose structure after safe YAML anchor expansion."""
    services = document["services"]
    require(
        set(services) == {"backend", "web", "backend-gpu", "web-gpu"},
        "Compose must define exactly the CPU and GPU stacks",
    )
    expected_builds = {
        "backend": "backend/Dockerfile",
        "web": "frontend/Dockerfile",
        "backend-gpu": "backend/Dockerfile.cuda",
        "web-gpu": "frontend/Dockerfile",
    }
    for name, dockerfile in expected_builds.items():
        build = services[name].get("build", {})
        require(build.get("context") == ".", f"{name} build context must be repository root")
        require(build.get("dockerfile") == dockerfile, f"{name} Dockerfile mismatch")

    require("profiles" not in services["backend"], "CPU backend must be enabled by default")
    require("profiles" not in services["web"], "CPU web must be enabled by default")
    require(services["backend-gpu"].get("profiles") == ["gpu"], "GPU backend profile missing")
    require(services["web-gpu"].get("profiles") == ["gpu"], "GPU web profile missing")

    selected = dependency_closure(services, {"backend-gpu", "web-gpu"})
    require(
        not selected.intersection({"backend", "web"}),
        "GPU explicit service closure must not include CPU services",
    )

    cpu_sources = validate_backend_service(services["backend"], "backend", "cpu", False)
    gpu_sources = validate_backend_service(services["backend-gpu"], "backend-gpu", "gpu", False)
    validate_web_service(services["web"], "web", "backend", "cpu", False)
    validate_web_service(
        services["web-gpu"], "web-gpu", "backend-gpu", "gpu", False
    )
    validate_gpu_device(services["backend-gpu"])
    require(cpu_sources.isdisjoint(gpu_sources), "CPU and GPU volumes must be distinct")


def validate_static_files() -> None:
    """Check configuration details not represented in normalized Compose data."""
    nginx = read("frontend/nginx.conf")
    validate_nginx_config(nginx)

    frontend_dockerfile = read("frontend/Dockerfile")
    validate_frontend_dockerfile(frontend_dockerfile)

    cpu_dockerfile = read("backend/Dockerfile")
    cuda_dockerfile = read("backend/Dockerfile.cuda")
    validate_cpu_dockerfile(cpu_dockerfile)
    validate_cuda_dockerfile(cuda_dockerfile)
    for dockerfile_name, dockerfile in (
        ("backend/Dockerfile", cpu_dockerfile),
        ("backend/Dockerfile.cuda", cuda_dockerfile),
    ):
        require("backend/requirements.txt" in dockerfile, f"{dockerfile_name} needs root context")
        require("USER hiimage" in dockerfile, f"{dockerfile_name} should run non-root")

    dockerignore = read(".dockerignore")
    for ignored in ("node_modules", "dist", "venv", "models", "cache", "output"):
        require(ignored in dockerignore, f".dockerignore missing {ignored}")
    require("scripts" not in dockerignore, ".dockerignore must preserve build scripts")
    validate_env_example(read(".env.example"))


def validate_normalized_compose(
    cpu: dict[str, Any],
    gpu: dict[str, Any],
    root: Path = ROOT,
) -> None:
    """Validate Docker Compose normalized CPU/GPU render output."""
    cpu_services = cpu["services"]
    require(set(cpu_services) == {"backend", "web"}, "default stack must be CPU web+backend")
    services = gpu["services"]
    require(
        set(services) == {"backend", "web", "backend-gpu", "web-gpu"},
        "gpu profile must render exactly CPU and GPU definitions",
    )

    validate_backend_service(cpu_services["backend"], "backend", "cpu", True)
    validate_web_service(cpu_services["web"], "web", "backend", "cpu", True)

    cpu_sources = validate_backend_service(services["backend"], "backend", "cpu", True)
    gpu_sources = validate_backend_service(
        services["backend-gpu"], "backend-gpu", "gpu", True
    )
    validate_web_service(services["web"], "web", "backend", "cpu", True)
    validate_web_service(
        services["web-gpu"], "web-gpu", "backend-gpu", "gpu", True
    )
    validate_gpu_device(services["backend-gpu"])

    for backend_name in ("backend", "backend-gpu"):
        backend = services[backend_name]
        context = Path(backend["build"]["context"]).resolve()
        require(context == root.resolve(), "backend context must be repo root")

    require(cpu_sources.isdisjoint(gpu_sources), "CPU and GPU stacks must use distinct volumes")
    require(
        set(services["backend"]["networks"]).isdisjoint(
            set(services["backend-gpu"]["networks"])
        ),
        "CPU and GPU stacks must use distinct networks",
    )

    selected = dependency_closure(services, {"backend-gpu", "web-gpu"})
    require(
        selected == {"backend-gpu", "web-gpu"},
        f"explicit GPU command would include unexpected services: {sorted(selected)}",
    )


def validate_compose() -> None:
    """Render and validate normalized CPU/GPU Compose configurations."""
    validate_normalized_compose(compose_json(), compose_json("--profile", "gpu"))


def main() -> int:
    """Run all checks and return a shell-friendly status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="skip Docker Compose rendering when Docker is unavailable",
    )
    args = parser.parse_args()
    try:
        validate_static_files()
        validate_static_compose(parse_compose_yaml(read("docker-compose.yml")))
        if not args.static_only:
            require(shutil.which("docker") is not None, "docker executable not found")
            validate_compose()
    except (AssertionError, RuntimeError, json.JSONDecodeError, yaml.YAMLError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    if args.static_only:
        print("[OK] Docker static configuration checks passed")
    else:
        print("[OK] Docker static checks, CPU config, GPU config, and GPU isolation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
