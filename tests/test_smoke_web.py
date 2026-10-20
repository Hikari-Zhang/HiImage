from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from scripts import smoke_web


def test_build_probe_urls_keeps_web_prefix_and_uses_matching_websocket_scheme() -> None:
    probes = smoke_web.build_probe_urls("https://example.test/hiimage/")

    assert probes.root == "https://example.test/hiimage/"
    assert probes.health == "https://example.test/hiimage/api/health"
    assert probes.spa_fallback.startswith("https://example.test/hiimage/__smoke__/route-")
    assert probes.websocket == "wss://example.test/hiimage/api/ws/progress"
    assert urlsplit(probes.spa_fallback).path != "/hiimage/"


@pytest.mark.parametrize(
    ("kind", "status", "content_type", "body"),
    [
        ("root", 200, "text/html; charset=utf-8", '<div id="root"></div>'),
        ("spa", 200, "text/html", '<div id="root"></div>'),
        ("health", 200, "application/json", '{"status":"ok","version":"2.0.0"}'),
    ],
)
def test_validate_response_accepts_expected_web_contract(
    kind: str,
    status: int,
    content_type: str,
    body: str,
) -> None:
    smoke_web.validate_response(kind, status, content_type, body)


@pytest.mark.parametrize(
    ("kind", "status", "content_type", "body", "message"),
    [
        ("root", 404, "text/html", "missing", "HTTP 200"),
        ("spa", 200, "application/json", "{}", "HTML"),
        ("health", 200, "application/json", '{"status":"down"}', "status=ok"),
    ],
)
def test_validate_response_rejects_false_positive_smoke_results(
    kind: str,
    status: int,
    content_type: str,
    body: str,
    message: str,
) -> None:
    with pytest.raises(smoke_web.SmokeFailure, match=message):
        smoke_web.validate_response(kind, status, content_type, body)
