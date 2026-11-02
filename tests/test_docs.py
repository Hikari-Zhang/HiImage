from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts import check_docs


ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DOCS = (
    "README.md",
    "docs/SYSTEM_ARCHITECTURE.md",
    "docs/OPTIMIZATION_BACKLOG.md",
    "docs/system-design.md",
    "docs/PRD.md",
    "docs/SCRIPTS_REFERENCE.md",
)


def test_active_documentation_links_commands_and_web_positioning() -> None:
    check_docs.validate_active_docs(ROOT, ACTIVE_DOCS)
    check_docs.validate_phase1_contract(ROOT, ACTIVE_DOCS)


def test_architecture_documents_every_declared_business_endpoint() -> None:
    """API 新增或改名时，完整架构文档也必须同步更新。"""
    document = (ROOT / 'docs/SYSTEM_ARCHITECTURE.md').read_text(encoding='utf-8')
    router_files = list((ROOT / 'backend/app/routers').glob('*.py'))
    router_files.append(ROOT / 'backend/app/websocket/progress.py')
    endpoints = []
    for path in router_files:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        prefix = ''
        for node in tree.body:
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name) and node.value.func.id == 'APIRouter'):
                prefix = next((ast.literal_eval(item.value) for item in node.value.keywords
                               if item.arg == 'prefix'), '')
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr in {'get', 'post', 'put', 'delete', 'websocket'}
                        and decorator.args):
                    endpoint = f'{decorator.func.attr.upper()} /api{prefix}{ast.literal_eval(decorator.args[0])}'
                    endpoints.append(endpoint)
    assert endpoints, 'No API endpoints were discovered; verify the documentation test parser'
    missing = [endpoint for endpoint in endpoints if f'`{endpoint}`' not in document]
    assert not missing, f'Architecture API inventory missing: {missing}'


def test_document_validator_rejects_unknown_npm_command(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"dev":"vite"}}',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Web\n\n```bash\nnpm run package\n```\n",
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="unknown npm script.*package"):
        check_docs.validate_active_docs(tmp_path, ("README.md",))


def test_document_validator_rejects_broken_local_link(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{}}', encoding="utf-8")
    (tmp_path / "README.md").write_text("[缺失文档](docs/missing.md)", encoding="utf-8")

    with pytest.raises(AssertionError, match="broken local link"):
        check_docs.validate_active_docs(tmp_path, ("README.md",))


@pytest.mark.parametrize(
    ("outdated", "message"),
    [
        ("单文件最多 50 MB", "20 MB"),
        ("Python 3.9+", "Python 3.10"),
    ],
)
def test_document_validator_rejects_outdated_runtime_limits(
    tmp_path: Path,
    outdated: str,
    message: str,
) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{}}', encoding="utf-8")
    (tmp_path / "README.md").write_text(outdated, encoding="utf-8")

    with pytest.raises(AssertionError, match=message):
        check_docs.validate_active_docs(tmp_path, ("README.md",))


@pytest.mark.parametrize(
    ("forbidden", "message"),
    [
        ("output_path", "server path"),
        ("全局匿名 progress", "global progress"),
        ("GET settings returns hf_token", "plaintext token"),
        ("unbounded executor", "unbounded executor"),
    ],
)
def test_phase1_validator_rejects_legacy_security_claims(
    tmp_path: Path,
    forbidden: str,
    message: str,
) -> None:
    (tmp_path / "README.md").write_text(forbidden, encoding="utf-8")

    with pytest.raises(AssertionError, match=message):
        check_docs.validate_phase1_contract(tmp_path, ("README.md",))


def test_phase1_validator_requires_documented_environment_defaults(
    tmp_path: Path,
) -> None:
    corpus = "\n".join(
        (ROOT / relative_path).read_text(encoding="utf-8")
        for relative_path in ACTIVE_DOCS
    )
    (tmp_path / "README.md").write_text(
        corpus.replace(
            "`HIIMAGE_MAX_PENDING_INFERENCE` 设为默认 `4`",
            "`HIIMAGE_MAX_PENDING_INFERENCE` 设为默认 `5`",
        ).replace(
            "`HIIMAGE_MAX_PENDING_INFERENCE` 控制单个后端进程的推理等待数，默认 `4`",
            "`HIIMAGE_MAX_PENDING_INFERENCE` 控制单个后端进程的推理等待数，默认 `5`",
        ),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="default 4"):
        check_docs.validate_phase1_contract(tmp_path, ("README.md",))


def test_phase1_validator_requires_safe_proxy_trust_default(
    tmp_path: Path,
) -> None:
    corpus = "\n".join(
        (ROOT / relative_path).read_text(encoding="utf-8")
        for relative_path in ACTIVE_DOCS
    )
    (tmp_path / "README.md").write_text(
        corpus.replace(
            "`HIIMAGE_TRUST_PROXY_HEADERS` 默认 `false`",
            "`HIIMAGE_TRUST_PROXY_HEADERS` 默认 `true`",
        ),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="default false"):
        check_docs.validate_phase1_contract(tmp_path, ("README.md",))
