from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_docs


ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DOCS = (
    "README.md",
    "docs/SYSTEM_ARCHITECTURE.md",
    "docs/system-design.md",
    "docs/PRD.md",
    "docs/SCRIPTS_REFERENCE.md",
)


def test_active_documentation_links_commands_and_web_positioning() -> None:
    check_docs.validate_active_docs(ROOT, ACTIVE_DOCS)


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
