from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import check_docker_config as checker  # noqa: E402


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def normalized_documents() -> tuple[dict, dict]:
    document = checker.parse_compose_yaml(read("docker-compose.yml"))
    services = deepcopy(document["services"])
    for service in services.values():
        if "build" in service:
            service["build"]["context"] = str(ROOT)
        if "volumes" in service:
            service["volumes"] = [
                {"source": item.split(":", 1)[0], "target": item.split(":", 1)[1]}
                for item in service["volumes"]
            ]
        networks = service.get("networks", {})
        if isinstance(networks, list):
            service["networks"] = {network: None for network in networks}
    for web_name in ("web", "web-gpu"):
        services[web_name]["ports"] = [
            {"host_ip": "127.0.0.1", "published": "8080", "target": 80}
        ]
    return (
        {"services": {name: services[name] for name in ("backend", "web")}},
        {"services": services},
    )


def test_cpu_dockerfile_rejects_missing_cpu_pytorch_index() -> None:
    content = read("backend/Dockerfile")
    checker.validate_cpu_dockerfile(content)

    mutated = content.replace("https://download.pytorch.org/whl/cpu", "https://pypi.org/simple")
    with pytest.raises(AssertionError, match="CPU PyTorch index"):
        checker.validate_cpu_dockerfile(mutated)


def test_cpu_dockerfile_rejects_non_executed_post_install() -> None:
    content = read("backend/Dockerfile")
    mutated = content.replace(
        "RUN python /app/scripts/post_install.py",
        "# python /app/scripts/post_install.py",
    )

    with pytest.raises(AssertionError, match="run post_install"):
        checker.validate_cpu_dockerfile(mutated)


def test_cuda_dockerfile_rejects_missing_nvidia_runtime_environment() -> None:
    content = read("backend/Dockerfile.cuda")
    checker.validate_cuda_dockerfile(content)

    mutated = content.replace("NVIDIA_DRIVER_CAPABILITIES=compute,utility", "")
    with pytest.raises(AssertionError, match="NVIDIA_DRIVER_CAPABILITIES"):
        checker.validate_cuda_dockerfile(mutated)


@pytest.mark.parametrize(
    ("needle", "replacement", "message"),
    [
        ("torch==2.5.1", "torch==2.5.0", "torch constraint"),
        ("torchvision==0.20.1", "torchvision==0.20.0", "torchvision constraint"),
        ("-c /tmp/constraints.txt", "", "constraints"),
    ],
)
def test_cuda_dockerfile_rejects_mutated_torch_stack_constraints(
    needle: str,
    replacement: str,
    message: str,
) -> None:
    content = read("backend/Dockerfile.cuda")
    mutated = content.replace(needle, replacement)

    with pytest.raises(AssertionError, match=message):
        checker.validate_cuda_dockerfile(mutated)


@pytest.mark.parametrize(
    ("relative_path", "validator"),
    [
        ("backend/Dockerfile", checker.validate_cpu_dockerfile),
        ("backend/Dockerfile.cuda", checker.validate_cuda_dockerfile),
    ],
)
def test_backend_dockerfiles_reject_echoed_flux_packages(
    relative_path: str,
    validator,
) -> None:
    content = read(relative_path)
    mutated = content.replace("python -m pip install --no-deps", "echo --no-deps")

    with pytest.raises(AssertionError, match="pip install.*--no-deps"):
        validator(mutated)


@pytest.mark.parametrize(
    ("relative_path", "validator"),
    [
        ("backend/Dockerfile", checker.validate_cpu_dockerfile),
        ("backend/Dockerfile.cuda", checker.validate_cuda_dockerfile),
    ],
)
def test_backend_dockerfiles_reject_root_as_final_user(
    relative_path: str,
    validator,
) -> None:
    content = read(relative_path)

    with pytest.raises(AssertionError, match="final USER"):
        validator(f"{content}\nUSER root\n")


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        (
            'CMD ["python", "run.py"]',
            "final CMD",
        ),
        (
            "HEALTHCHECK CMD true",
            "healthcheck",
        ),
    ],
)
@pytest.mark.parametrize(
    ("relative_path", "validator"),
    [
        ("backend/Dockerfile", checker.validate_cpu_dockerfile),
        ("backend/Dockerfile.cuda", checker.validate_cuda_dockerfile),
    ],
)
def test_backend_dockerfile_rejects_invalid_runtime_instruction(
    relative_path: str,
    validator,
    replacement: str,
    message: str,
) -> None:
    content = read(relative_path)
    if replacement.startswith("CMD "):
        mutated = content.replace(
            'CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8787"]',
            replacement,
        )
    else:
        start = content.index("HEALTHCHECK ")
        end = content.index("\n\nCMD ", start)
        mutated = f"{content[:start]}{replacement}{content[end:]}"

    with pytest.raises(AssertionError, match=message):
        validator(mutated)


@pytest.mark.parametrize(
    "directive",
    [
        "listen 80;",
        "try_files $uri $uri/ /index.html;",
        "proxy_pass http://backend:8787;",
        "proxy_set_header Upgrade $http_upgrade;",
        "proxy_set_header Connection $connection_upgrade;",
        "proxy_set_header X-Forwarded-For $remote_addr;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
        "proxy_set_header X-Forwarded-Host $http_host;",
        "proxy_buffering off;",
        "proxy_cache off;",
        "add_header X-Accel-Buffering no always;",
        "proxy_send_timeout 1800s;",
        "proxy_read_timeout 1800s;",
        "client_max_body_size 80m;",
        'add_header Cache-Control "public, immutable";',
    ],
)
def test_nginx_validator_rejects_commented_core_directive(directive: str) -> None:
    content = read("frontend/nginx.conf")
    checker.validate_nginx_config(content)
    mutated = content.replace(directive, f"# {directive}", 1)

    with pytest.raises(AssertionError):
        checker.validate_nginx_config(mutated)


def test_frontend_dockerfile_rejects_root_as_final_user() -> None:
    content = read("frontend/Dockerfile")
    checker.validate_frontend_dockerfile(content)

    with pytest.raises(AssertionError, match="final USER"):
        checker.validate_frontend_dockerfile(f"{content}\nUSER root\n")


def test_static_compose_rejects_wrong_web_defaults() -> None:
    document = checker.parse_compose_yaml(read("docker-compose.yml"))
    checker.validate_static_compose(document)

    mutated = deepcopy(document)
    mutated["services"]["web"]["ports"] = ["0.0.0.0:9090:80"]
    with pytest.raises(AssertionError, match="default host/port"):
        checker.validate_static_compose(mutated)


def test_static_compose_rejects_unhealthy_dependency_or_wrong_network() -> None:
    document = checker.parse_compose_yaml(read("docker-compose.yml"))

    dependency_mutation = deepcopy(document)
    dependency_mutation["services"]["web"]["depends_on"]["backend"]["condition"] = (
        "service_started"
    )
    with pytest.raises(AssertionError, match="service_healthy"):
        checker.validate_static_compose(dependency_mutation)

    network_mutation = deepcopy(document)
    network_mutation["services"]["web-gpu"]["networks"] = ["cpu"]
    with pytest.raises(AssertionError, match="gpu network"):
        checker.validate_static_compose(network_mutation)


def test_static_compose_rejects_missing_backend_alias_and_gpu_device() -> None:
    document = checker.parse_compose_yaml(read("docker-compose.yml"))

    alias_mutation = deepcopy(document)
    alias_mutation["services"]["backend"]["networks"]["cpu"]["aliases"] = []
    with pytest.raises(AssertionError, match="backend alias"):
        checker.validate_static_compose(alias_mutation)

    device_mutation = deepcopy(document)
    device_mutation["services"]["backend-gpu"]["deploy"]["resources"]["reservations"][
        "devices"
    ] = []
    with pytest.raises(AssertionError, match="NVIDIA device"):
        checker.validate_static_compose(device_mutation)


def test_static_compose_rejects_gpu_dependency_on_cpu() -> None:
    document = checker.parse_compose_yaml(read("docker-compose.yml"))
    mutated = deepcopy(document)
    mutated["services"]["web-gpu"]["depends_on"]["backend"] = {
        "condition": "service_healthy"
    }

    with pytest.raises(AssertionError, match="CPU services"):
        checker.validate_static_compose(mutated)


def test_static_compose_requires_profile_specific_default_devices() -> None:
    document = checker.parse_compose_yaml(read("docker-compose.yml"))
    checker.validate_static_compose(document)

    cpu_mutation = deepcopy(document)
    cpu_mutation["services"]["backend"]["environment"]["HIIMAGE_DEFAULT_DEVICE"] = "cuda"
    with pytest.raises(AssertionError, match="default device"):
        checker.validate_static_compose(cpu_mutation)

    gpu_mutation = deepcopy(document)
    gpu_mutation["services"]["backend-gpu"]["environment"]["HIIMAGE_DEFAULT_DEVICE"] = "cpu"
    with pytest.raises(AssertionError, match="default device"):
        checker.validate_static_compose(gpu_mutation)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("HIIMAGE_MAX_PENDING_INFERENCE", "4"),
        ("HIIMAGE_WS_ORIGINS", ""),
        ("HIIMAGE_TRUST_PROXY_HEADERS", "true"),
    ],
)
def test_static_compose_requires_phase1_backend_environment(
    variable: str,
    value: str,
) -> None:
    document = checker.parse_compose_yaml(read("docker-compose.yml"))
    checker.validate_static_compose(document)

    mutated = deepcopy(document)
    mutated["services"]["backend"]["environment"][variable] = "wrong"
    with pytest.raises(AssertionError, match=variable):
        checker.validate_static_compose(mutated)

    assert document["services"]["backend"]["environment"][variable] == value
    assert document["services"]["backend-gpu"]["environment"][variable] == value


def test_env_example_requires_phase1_backend_defaults() -> None:
    content = read(".env.example")
    checker.validate_env_example(content)

    with pytest.raises(AssertionError, match="HIIMAGE_MAX_PENDING_INFERENCE"):
        checker.validate_env_example(
            content.replace("HIIMAGE_MAX_PENDING_INFERENCE=4", "")
        )
    with pytest.raises(AssertionError, match="HIIMAGE_WS_ORIGINS"):
        checker.validate_env_example(content.replace("HIIMAGE_WS_ORIGINS=", ""))
    with pytest.raises(AssertionError, match="HIIMAGE_TRUST_PROXY_HEADERS"):
        checker.validate_env_example(
            content.replace("HIIMAGE_TRUST_PROXY_HEADERS=false", "")
        )


@pytest.mark.parametrize(
    "service_name",
    ["backend", "backend-gpu", "web", "web-gpu"],
)
def test_static_compose_rejects_noop_healthcheck(service_name: str) -> None:
    document = checker.parse_compose_yaml(read("docker-compose.yml"))
    mutated = deepcopy(document)
    mutated["services"][service_name]["healthcheck"] = {"test": ["CMD", "true"]}

    with pytest.raises(AssertionError, match="healthcheck"):
        checker.validate_static_compose(mutated)


def test_normalized_compose_rejects_wrong_host_binding() -> None:
    cpu, gpu = normalized_documents()
    checker.validate_normalized_compose(cpu, gpu, ROOT)

    mutated = deepcopy(cpu)
    mutated["services"]["web"]["ports"][0]["host_ip"] = "0.0.0.0"
    with pytest.raises(AssertionError, match="127.0.0.1"):
        checker.validate_normalized_compose(mutated, gpu, ROOT)


@pytest.mark.parametrize(
    "service_name",
    ["backend", "backend-gpu", "web", "web-gpu"],
)
def test_normalized_compose_rejects_noop_healthcheck(service_name: str) -> None:
    cpu, gpu = normalized_documents()
    mutated = deepcopy(gpu)
    mutated["services"][service_name]["healthcheck"] = {"test": ["CMD", "true"]}

    with pytest.raises(AssertionError, match="healthcheck"):
        checker.validate_normalized_compose(cpu, mutated, ROOT)
