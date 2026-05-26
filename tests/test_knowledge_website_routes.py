from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.domains.knowledge.schemas import (
    IndexJobDTO,
    KnowledgeSourceDTO,
    WebsitePathRule,
    WebsiteSourceListItemDTO,
    WebsiteUsageResponse,
)


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "good-token":
            return {"sub": "00000000-0000-0000-0000-000000000123"}
        raise AppError(code="auth.unauthorized", message="bad token", status_code=401)


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer good-token"}


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())


def test_website_crawl_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    sid = UUID("00000000-0000-0000-0000-000000000001")
    jid = UUID("00000000-0000-0000-0000-000000000002")
    aid = UUID("00000000-0000-0000-0000-000000000888")

    async def _create(*_a: Any, **_k: Any) -> tuple[KnowledgeSourceDTO, IndexJobDTO]:
        src = KnowledgeSourceDTO.model_validate(
            {
                "id": sid,
                "agent_id": aid,
                "user_id": UUID("00000000-0000-0000-0000-000000000123"),
                "type": "website",
                "title": "example.com",
                "status": "indexing",
                "source_url": "https://example.com/",
                "storage_bucket": None,
                "storage_path": None,
                "metadata": {"origin": "dashboard_website", "website_mode": "crawl"},
                "error_message": None,
                "last_indexed_at": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )
        job = IndexJobDTO.model_validate(
            {
                "id": jid,
                "knowledge_source_id": sid,
                "agent_id": aid,
                "user_id": UUID("00000000-0000-0000-0000-000000000123"),
                "status": "queued",
                "attempt": 1,
                "triggered_by": "api",
                "error_message": None,
                "started_at": None,
                "finished_at": None,
                "phase": "queued",
                "pages_total": 0,
                "pages_processed": 0,
                "chunks_total": 0,
                "chunks_embedded": 0,
                "progress_pct": 0,
                "metrics": {},
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )
        return src, job

    monkeypatch.setattr("app.api.routes.knowledge_website.create_and_enqueue_dashboard_website", _create)

    res = client.post(
        "/api/v1/knowledge/website/crawl",
        headers=_auth_header(),
        json={
            "agent_id": str(aid),
            "protocol": "https://",
            "url_input": "example.com",
            "include_rules": [{"operator": "ends_with", "pattern": ".pdf"}],
            "exclude_rules": [],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["source"]["id"] == str(sid)
    assert body["job"]["status"] == "queued"


def test_website_preview_urls_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    aid = UUID("00000000-0000-0000-0000-000000000888")

    from app.domains.knowledge.schemas import WebsiteUrlPreviewResponse

    async def _pv(_payload: object) -> WebsiteUrlPreviewResponse:
        return WebsiteUrlPreviewResponse(
            discovery_mode="sitemap",
            filtered_url_count=2,
            sample_urls=["https://ex.com/a", "https://ex.com/b"],
            truncated=False,
            message=None,
        )

    monkeypatch.setattr("app.api.routes.knowledge_website.preview_dashboard_website_filtered_urls", _pv)

    res = client.post(
        "/api/v1/knowledge/website/preview-urls",
        headers=_auth_header(),
        json={
            "agent_id": str(aid),
            "protocol": "https://",
            "url_input": "example.com",
            "include_rules": [],
            "exclude_rules": [],
            "max_sample_urls": 10,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["discovery_mode"] == "sitemap"
    assert body["filtered_url_count"] == 2
    assert len(body["sample_urls"]) == 2


def test_website_crawl_route_duplicate_skips_job(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    sid = UUID("00000000-0000-0000-0000-000000000001")
    aid = UUID("00000000-0000-0000-0000-000000000888")

    async def _create(*_a: Any, **_k: Any) -> tuple[KnowledgeSourceDTO, IndexJobDTO | None]:
        src = KnowledgeSourceDTO.model_validate(
            {
                "id": sid,
                "agent_id": aid,
                "user_id": UUID("00000000-0000-0000-0000-000000000123"),
                "type": "website",
                "title": "example.com",
                "status": "skipped_duplicate",
                "source_url": "https://example.com/",
                "storage_bucket": None,
                "storage_path": None,
                "metadata": {"origin": "dashboard_website", "duplicate_reason": "same_root_url"},
                "error_message": None,
                "last_indexed_at": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )
        return src, None

    monkeypatch.setattr("app.api.routes.knowledge_website.create_and_enqueue_dashboard_website", _create)

    res = client.post(
        "/api/v1/knowledge/website/crawl",
        headers=_auth_header(),
        json={"agent_id": str(aid), "protocol": "https://", "url_input": "example.com", "include_rules": [], "exclude_rules": []},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["source"]["status"] == "skipped_duplicate"
    assert body["job"] is None


def test_website_source_pages_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    sid = UUID("00000000-0000-0000-0000-000000000099")

    async def _pages(*_a: Any, **_k: Any) -> tuple[list[dict[str, object]], int]:
        return (
            [
                {
                    "id": UUID("00000000-0000-0000-0000-000000000111"),
                    "url": "https://example.com/a",
                    "status": "parsed",
                    "depth": 0,
                    "last_indexed_at": None,
                    "http_status": 200,
                }
            ],
            3,
        )

    monkeypatch.setattr("app.api.routes.knowledge_website.list_website_source_pages", _pages)

    res = client.get(f"/api/v1/knowledge/website/sources/{sid}/pages?offset=0&limit=10", headers=_auth_header())
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert len(body["pages"]) == 1
    assert body["pages"][0]["url"] == "https://example.com/a"


def test_website_source_delete_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    sid = UUID("00000000-0000-0000-0000-000000000099")
    called: dict[str, bool] = {}

    async def _del(*_a: Any, **_k: Any) -> None:
        called["ok"] = True

    monkeypatch.setattr("app.api.routes.knowledge_website.delete_website_source", _del)

    res = client.delete(f"/api/v1/knowledge/website/sources/{sid}", headers=_auth_header())
    assert res.status_code == 204
    assert res.content == b""
    assert called.get("ok") is True


def test_website_source_retrain_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    sid = UUID("00000000-0000-0000-0000-000000000099")
    jid = UUID("00000000-0000-0000-0000-000000000100")
    aid = UUID("00000000-0000-0000-0000-000000000888")

    async def _retrain(*_a: Any, **_k: Any) -> tuple[KnowledgeSourceDTO, IndexJobDTO]:
        src = KnowledgeSourceDTO.model_validate(
            {
                "id": sid,
                "agent_id": aid,
                "user_id": UUID("00000000-0000-0000-0000-000000000123"),
                "type": "website",
                "title": "example.com",
                "status": "indexing",
                "source_url": "https://example.com/",
                "storage_bucket": None,
                "storage_path": None,
                "metadata": {"origin": "dashboard_website", "website_mode": "crawl"},
                "error_message": None,
                "last_indexed_at": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )
        job = IndexJobDTO.model_validate(
            {
                "id": jid,
                "knowledge_source_id": sid,
                "agent_id": aid,
                "user_id": UUID("00000000-0000-0000-0000-000000000123"),
                "status": "queued",
                "attempt": 1,
                "triggered_by": "api",
                "error_message": None,
                "started_at": None,
                "finished_at": None,
                "phase": "queued",
                "pages_total": 0,
                "pages_processed": 0,
                "chunks_total": 0,
                "chunks_embedded": 0,
                "progress_pct": 0,
                "metrics": {},
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )
        return src, job

    monkeypatch.setattr("app.api.routes.knowledge_website.enqueue_index_website_source_queued", _retrain)
    res = client.post(f"/api/v1/knowledge/website/sources/{sid}/retrain", headers=_auth_header())
    assert res.status_code == 200
    body = res.json()
    assert body["source"]["id"] == str(sid)
    assert body["job"]["id"] == str(jid)


def test_website_usage_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    aid = UUID("00000000-0000-0000-0000-000000000888")

    async def _usage(*_a: Any, **_k: Any) -> WebsiteUsageResponse:
        return WebsiteUsageResponse(
            plan_slug="free",
            plan_name="Free",
            included_storage_bytes=409_600,
            used_storage_bytes=500_000,
            total_links=12,
            show_upgrade=True,
            website_crawl_budget_bytes=512_000,
            website_crawl_last_job_bytes=182_000,
        )

    monkeypatch.setattr("app.api.routes.knowledge_website.get_agent_website_usage", _usage)

    res = client.get(f"/api/v1/knowledge/website/usage?agent_id={aid}", headers=_auth_header())
    assert res.status_code == 200
    data = res.json()
    assert data["plan_slug"] == "free"
    assert data["show_upgrade"] is True
    assert data["total_links"] == 12
    assert data["website_crawl_budget_bytes"] == 512_000
    assert data["website_crawl_last_job_bytes"] == 182_000


def test_website_workspace_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    aid = UUID("00000000-0000-0000-0000-000000000888")
    sid = UUID("00000000-0000-0000-0000-000000000099")

    async def _usage(*_a: Any, **_k: Any) -> WebsiteUsageResponse:
        return WebsiteUsageResponse(
            plan_slug="free",
            plan_name="Free",
            included_storage_bytes=409_600,
            used_storage_bytes=100_000,
            total_links=3,
            show_upgrade=False,
            website_crawl_budget_bytes=512_000,
            website_crawl_last_job_bytes=None,
        )

    async def _sources(*_a: Any, **_k: Any) -> list[WebsiteSourceListItemDTO]:
        return [
            WebsiteSourceListItemDTO.model_validate(
                {
                    "id": sid,
                    "agent_id": aid,
                    "title": "example.com",
                    "source_url": "https://example.com",
                    "status": "ready",
                    "website_mode": "crawl",
                }
            )
        ]

    monkeypatch.setattr("app.api.routes.knowledge_website.get_agent_website_usage", _usage)
    monkeypatch.setattr("app.api.routes.knowledge_website.list_website_sources_for_agent", _sources)

    res = client.get(f"/api/v1/knowledge/website/workspace?agent_id={aid}", headers=_auth_header())
    assert res.status_code == 200
    body = res.json()
    assert body["usage"]["plan_slug"] == "free"
    assert body["usage"]["total_links"] == 3
    assert len(body["sources"]) == 1
    assert body["sources"][0]["id"] == str(sid)
    assert body["sources"][0]["source_url"] == "https://example.com"


@pytest.mark.asyncio
async def test_require_knowledge_source_exists_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import AppError
    from app.domains.knowledge.service import _require_knowledge_source_exists

    class _Sess:
        async def execute(self, _stmt: object, _params: object | None = None) -> object:
            class _R:
                def first(self) -> None:
                    return None

            return _R()

    with pytest.raises(AppError) as ei:
        await _require_knowledge_source_exists(_Sess(), uuid4())  # type: ignore[arg-type]
    assert ei.value.code == "knowledge.source_removed"


def test_website_fetch_page_stats() -> None:
    from app.domains.knowledge.service import _website_fetch_page_stats

    pages: list[dict[str, object]] = [
        {"text": "ok", "http_status": 200},
        {"text": "", "http_status": 200},
        {"text": "", "http_status": None},
        {"text": "x", "http_status": 404},
        {"text": "y", "http_status": 500},
    ]
    s = _website_fetch_page_stats(pages)
    assert s["urls_fetched"] == 5
    assert s["urls_empty_text"] == 2
    assert s["urls_with_text"] == 3
    assert s["urls_http_missing"] == 1
    assert s["urls_http_4xx"] == 1
    assert s["urls_http_5xx"] == 1


def test_website_filter_summary_canonical() -> None:
    from app.domains.knowledge.service import _website_filter_summary

    inc = [{"operator": "contains", "pattern": "/z/"}, {"operator": "contains", "pattern": "/a/"}]
    exc = [{"operator": "contains", "pattern": "/admin"}]
    s = _website_filter_summary(inc, exc)
    assert s["include_rules"][0]["pattern"] == "/a/"
    assert s["include_rules"][1]["pattern"] == "/z/"
    assert s["exclude_rules"] == [{"operator": "contains", "pattern": "/admin"}]


def test_url_passes_excludes_only() -> None:
    from app.domains.knowledge.service import _url_passes_excludes_only

    exc = [{"operator": "contains", "pattern": "/admin"}]
    assert _url_passes_excludes_only("https://x.com/blog", exc)
    assert not _url_passes_excludes_only("https://x.com/admin/login", exc)


def test_url_filter_helpers() -> None:
    from app.domains.knowledge.service import _url_passes_filters

    inc = [{"operator": "ends_with", "pattern": ".pdf"}]
    assert _url_passes_filters("https://x.com/a.pdf", inc, []) is True
    assert _url_passes_filters("https://x.com/a.html", inc, []) is False

    exc = [{"operator": "contains", "pattern": "/admin"}]
    assert _url_passes_filters("https://x.com/blog", [], exc) is True
    assert _url_passes_filters("https://x.com/admin/login", [], exc) is False

    # Case-insensitive path contains (path segment uses ``And`` not ``and``).
    exc2 = [{"operator": "contains", "pattern": "and"}]
    assert _url_passes_filters("https://x.com/InternationalAndDomesticTariffs", [], exc2) is False

    exc3 = [{"operator": "wildcard", "pattern": "*Foo*"}]
    assert _url_passes_filters("https://x.com/barfoo/baz", [], exc3) is False


def test_included_storage_from_max_total_knowledge_mb() -> None:
    from app.domains.knowledge.service import _included_storage_bytes_from_plan_features

    assert _included_storage_bytes_from_plan_features({"max_total_knowledge_mb": 10}) == 10 * 1024 * 1024
    assert _included_storage_bytes_from_plan_features({"max_total_knowledge_mb": 5}) == 5 * 1024 * 1024


def test_included_storage_uses_strictest_when_mb_and_kb_present() -> None:
    from app.domains.knowledge.service import _included_storage_bytes_from_plan_features

    assert (
        _included_storage_bytes_from_plan_features({"max_total_knowledge_mb": 1, "max_knowledge_storage_kb": 500})
        == 500 * 1024
    )


def test_job_crawl_limit_exceeded_flag() -> None:
    from app.domains.knowledge.service import _job_crawl_limit_exceeded

    m = {"crawl_stopped_reason": "budget"}
    assert _job_crawl_limit_exceeded("succeeded", m, 2000, 33) is True
    assert _job_crawl_limit_exceeded("succeeded", m, 33, 33) is False
    assert _job_crawl_limit_exceeded("running", m, 2000, 10) is False
    assert _job_crawl_limit_exceeded("succeeded", {"crawl_stopped_reason": "complete"}, 2000, 33) is False


def test_job_crawl_limit_exceeded_uses_storage_guardrail() -> None:
    from app.domains.knowledge.service import _job_crawl_limit_exceeded

    m = {"crawl_stopped_reason": "budget", "indexed_source_bytes": 40_000}
    assert _job_crawl_limit_exceeded("succeeded", m, 2000, 300, storage_cap_bytes=500_000) is False


def test_url_duplicate_key_normalizes() -> None:
    from app.domains.knowledge.service import _url_duplicate_key

    assert _url_duplicate_key("HTTPS://Example.COM/Foo/") == "https://example.com/foo"


def test_dashboard_path_rules_match() -> None:
    from app.domains.knowledge.service import _dashboard_path_rules_match

    base_md = {"include_rules": [{"operator": "contains", "pattern": "/tapered/"}], "exclude_rules": []}
    assert _dashboard_path_rules_match(
        [{"operator": "contains", "pattern": "/tapered/"}],
        [],
        base_md,
    )
    assert not _dashboard_path_rules_match(
        [{"operator": "contains", "pattern": "/other/"}],
        [],
        base_md,
    )
    assert not _dashboard_path_rules_match([], [], base_md)
    assert _dashboard_path_rules_match(
        [{"operator": "contains", "pattern": "/z/"}, {"operator": "contains", "pattern": "/a/"}],
        [],
        {
            "include_rules": [
                {"operator": "contains", "pattern": "/a/"},
                {"operator": "contains", "pattern": "/z/"},
            ],
            "exclude_rules": [],
        },
    )


def test_sitemap_seed_urls_site_root_adds_default_sitemap() -> None:
    from app.domains.knowledge.service import _sitemap_seed_urls

    assert _sitemap_seed_urls("https://shop.example/") == [
        "https://shop.example/",
        "https://shop.example/sitemap.xml",
    ]


def test_sitemap_seed_urls_explicit_sitemap_only_one_seed() -> None:
    from app.domains.knowledge.service import _sitemap_seed_urls

    assert _sitemap_seed_urls("https://shop.example/sitemap.xml") == ["https://shop.example/sitemap.xml"]


def test_sitemap_seed_urls_non_root_path_no_extra() -> None:
    from app.domains.knowledge.service import _sitemap_seed_urls

    assert _sitemap_seed_urls("https://shop.example/blog/") == ["https://shop.example/blog/"]


def test_rules_from_payload_prefixes_slash_for_starts_with_exact_match() -> None:
    from app.domains.knowledge.service import _rules_from_payload

    assert _rules_from_payload([WebsitePathRule(operator="starts_with", pattern="blog")]) == [
        {"operator": "starts_with", "pattern": "/blog"}
    ]
    assert _rules_from_payload([WebsitePathRule(operator="exact_match", pattern="about")]) == [
        {"operator": "exact_match", "pattern": "/about"}
    ]
    assert _rules_from_payload([WebsitePathRule(operator="contains", pattern="sale")]) == [
        {"operator": "contains", "pattern": "sale"}
    ]


def test_app_error_for_empty_sitemap_discovery() -> None:
    from app.domains.knowledge.service import SitemapDiscoveryResult, _app_error_for_empty_sitemap_discovery

    unreachable = _app_error_for_empty_sitemap_discovery(
        SitemapDiscoveryResult(urls=[], http_success_count=0, http_failure_count=2)
    )
    assert unreachable.code == "knowledge.sitemap_unreachable"

    invalid = _app_error_for_empty_sitemap_discovery(
        SitemapDiscoveryResult(urls=[], http_success_count=1, had_successful_xml_document=False)
    )
    assert invalid.code == "knowledge.sitemap_invalid"

    empty_filters = _app_error_for_empty_sitemap_discovery(
        SitemapDiscoveryResult(urls=[], http_success_count=1, had_successful_xml_document=True)
    )
    assert empty_filters.code == "knowledge.sitemap_empty"


def test_normalize_sitemap_document_url_preserves_query() -> None:
    from app.core.errors import AppError
    from app.domains.knowledge.service import _normalize_sitemap_document_url, _normalize_url_string

    assert (
        _normalize_sitemap_document_url("https://shop.example/sitemap_products_1.xml?from=1&to=2#frag")
        == "https://shop.example/sitemap_products_1.xml?from=1&to=2"
    )
    assert _normalize_url_string("https://shop.example/p?id=1#x") == "https://shop.example/p"
    with pytest.raises(AppError):
        _normalize_sitemap_document_url("not-a-url")


def _mock_sitemap_http_client(
    *,
    index_xml: bytes,
    child_xml: bytes,
    child_path: str = "/sitemap_products_1.xml",
    fail_child_without_query: bool = False,
    fail_url: str | None = None,
) -> type:
    """Build a fake httpx.AsyncClient that records GET URLs and serves test sitemap XML."""

    class _Resp:
        def __init__(self, content: bytes, status_code: int = 200) -> None:
            self.content = content
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    class _FakeClient:
        requested: list[str] = []

        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            _FakeClient.requested.append(url)
            if fail_url and url == fail_url:
                return _Resp(b"", status_code=500)
            if fail_child_without_query and child_path in url and "?" not in url:
                return _Resp(b"", status_code=400)
            if url.endswith("/sitemap.xml"):
                return _Resp(index_xml)
            if child_path in url and "?" in url:
                return _Resp(child_xml)
            if child_path in url:
                return _Resp(child_xml)
            return _Resp(b"<urlset></urlset>")

    return _FakeClient


@pytest.mark.asyncio
async def test_discover_sitemap_urls_nested_shopify_index(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.knowledge import service as svc

    index_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://shop.test/sitemap_products_1.xml?from=1&amp;to=2</loc>
  </sitemap>
</sitemapindex>"""
    child_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://shop.test/products/item-a</loc></url>
</urlset>"""
    fake_cls = _mock_sitemap_http_client(
        index_xml=index_xml,
        child_xml=child_xml,
        fail_child_without_query=True,
    )
    monkeypatch.setattr(svc.httpx, "AsyncClient", fake_cls)

    discovered = await svc._discover_sitemap_urls("https://shop.test/", 100, [], [])
    assert "https://shop.test/products/item-a" in discovered.urls
    child_gets = [u for u in fake_cls.requested if "sitemap_products_1.xml" in u]
    assert child_gets
    assert any("from=1" in u and "to=2" in u for u in child_gets)


@pytest.mark.asyncio
async def test_discover_sitemap_urls_collects_all_before_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.knowledge import service as svc

    index_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://shop.test/sitemap_mixed_1.xml</loc></sitemap>
</sitemapindex>"""
    child_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://shop.test/blogs/post-a</loc></url>
  <url><loc>https://shop.test/products/item-b</loc></url>
  <url><loc>https://shop.test/pages/about</loc></url>
</urlset>"""
    fake_cls = _mock_sitemap_http_client(index_xml=index_xml, child_xml=child_xml, child_path="/sitemap_mixed_1.xml")
    monkeypatch.setattr(svc.httpx, "AsyncClient", fake_cls)

    discovered = await svc._discover_sitemap_urls("https://shop.test/", 100, [], [])
    assert discovered.urls_discovered_total == 3
    assert len(discovered.urls) == 3

    inc = [{"operator": "contains", "pattern": "/blogs/"}]
    filtered = await svc._discover_sitemap_urls("https://shop.test/", 100, inc, [])
    assert filtered.urls_discovered_total == 3
    assert filtered.urls_after_filter == 1
    assert filtered.urls == ["https://shop.test/blogs/post-a"]


def test_cap_path_filtered_urls_strict_contains() -> None:
    from app.domains.knowledge.service import _cap_path_filtered_urls

    urls = [
        "https://shop.test/collections/boys-perfumes",
        "https://shop.test/collections/men-cargo-pants",
        "https://shop.test/products/p1",
        "https://shop.test/cart",
    ]
    inc = [{"operator": "contains", "pattern": "perfumes"}]
    out = _cap_path_filtered_urls(urls, inc, [], 100)
    assert out == ["https://shop.test/collections/boys-perfumes"]


def test_extract_embedded_same_site_paths_collection_products() -> None:
    from app.domains.knowledge.service import _extract_embedded_same_site_paths, _extract_page_urls

    base = "https://shop.test/collections/women-perfumes"
    html = (
        '{"url":"collections/women-perfumes/products/6cswf956-red"}'
        '<a href="/collections/men-cargo-pants">other</a>'
    )
    embedded = _extract_embedded_same_site_paths(base, html)
    assert "https://shop.test/collections/women-perfumes/products/6cswf956-red" in embedded
    page_urls = _extract_page_urls(base, html)
    assert "https://shop.test/collections/women-perfumes/products/6cswf956-red" in page_urls
    assert "https://shop.test/collections/men-cargo-pants" in page_urls


@pytest.mark.asyncio
async def test_expand_urls_from_matching_hubs_strict_path_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.knowledge import service as svc

    hub = "https://shop.test/collections/women-perfumes"
    html = (
        '{"x":"collections/women-perfumes/products/6cswf956-red"}'
        '<a href="/collections/men-cargo-pants">nav</a>'
        '<a href="/cart">cart</a>'
    )

    class _Resp:
        status_code = 200
        content = html.encode()

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            assert url == hub
            return _Resp()

    monkeypatch.setattr(svc.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(svc, "_charge_bytes_and_html_from_response", lambda _r, **_: (100, html))

    inc = [{"operator": "contains", "pattern": "perfumes"}]
    out, added = await svc._expand_urls_from_matching_hubs(
        [hub], include_rules=inc, exclude_rules=[], max_urls=100
    )
    assert hub in out
    assert "https://shop.test/collections/women-perfumes/products/6cswf956-red" in out
    assert "https://shop.test/collections/men-cargo-pants" not in out
    assert "https://shop.test/cart" not in out
    assert added == 1


@pytest.mark.asyncio
async def test_discover_sitemap_urls_include_expands_matching_hub_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.knowledge import service as svc

    index_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://shop.test/sitemap_coll_1.xml</loc></sitemap>
</sitemapindex>"""
    child_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://shop.test/collections/boys-perfumes</loc></url>
  <url><loc>https://shop.test/products/standalone</loc></url>
  <url><loc>https://shop.test/collections/men-cargo-pants</loc></url>
</urlset>"""
    hub_html = '{"u":"collections/boys-perfumes/products/p1-red"}'

    class _Resp:
        def __init__(self, *, content: bytes, status_code: int = 200) -> None:
            self.content = content
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("http error")

    class _FakeClient:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            if url.endswith("sitemap_coll_1.xml"):
                return _Resp(content=child_xml)
            if url.endswith("/sitemap.xml"):
                return _Resp(content=index_xml)
            if "boys-perfumes" in url:
                return _Resp(content=hub_html.encode())
            return _Resp(content=b"<urlset></urlset>")

    monkeypatch.setattr(svc.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(svc, "_charge_bytes_and_html_from_response", lambda _r, **_: (50, hub_html))

    inc = [{"operator": "contains", "pattern": "perfumes"}]
    discovered = await svc._discover_sitemap_urls("https://shop.test/", 100, inc, [])
    assert discovered.urls_discovered_total == 3
    assert discovered.urls_after_filter == 1
    assert "https://shop.test/collections/boys-perfumes" in discovered.urls
    assert "https://shop.test/collections/boys-perfumes/products/p1-red" in discovered.urls
    assert "https://shop.test/collections/men-cargo-pants" not in discovered.urls
    assert discovered.hub_urls_added >= 1


@pytest.mark.asyncio
async def test_discover_sitemap_urls_respects_include_exclude(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.knowledge import service as svc

    index_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://shop.test/sitemap_mixed_1.xml?from=9&amp;to=10</loc>
  </sitemap>
</sitemapindex>"""
    child_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://shop.test/blogs/post-a</loc></url>
  <url><loc>https://shop.test/products/item-b</loc></url>
  <url><loc>https://shop.test/pages/about</loc></url>
</urlset>"""
    fake_cls = _mock_sitemap_http_client(
        index_xml=index_xml,
        child_xml=child_xml,
        child_path="/sitemap_mixed_1.xml",
    )
    monkeypatch.setattr(svc.httpx, "AsyncClient", fake_cls)

    inc = [{"operator": "contains", "pattern": "/blogs/"}]
    only_blogs = await svc._discover_sitemap_urls("https://shop.test/", 100, inc, [])
    assert only_blogs.urls == ["https://shop.test/blogs/post-a"]
    assert any("sitemap_mixed_1.xml?from=9" in u for u in fake_cls.requested)

    fake_cls2 = _mock_sitemap_http_client(
        index_xml=index_xml,
        child_xml=child_xml,
        child_path="/sitemap_mixed_1.xml",
    )
    monkeypatch.setattr(svc.httpx, "AsyncClient", fake_cls2)
    exc = [{"operator": "contains", "pattern": "/products/"}]
    no_products = await svc._discover_sitemap_urls("https://shop.test/", 100, [], exc)
    assert "https://shop.test/blogs/post-a" in no_products.urls
    assert "https://shop.test/pages/about" in no_products.urls
    assert "https://shop.test/products/item-b" not in no_products.urls


@pytest.mark.asyncio
async def test_dashboard_fetch_planned_urls_passes_remaining_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from app.domains.knowledge import service as svc
    from app.domains.knowledge.schemas import KnowledgeSourceDTO

    budgets_seen: list[int | None] = []

    async def fake_fetch(urls: list[str], *, crawl_budget_bytes: int | None = None) -> tuple[object, ...]:
        budgets_seen.append(crawl_budget_bytes)
        pages = [{"url": u, "depth": 0, "http_status": 200, "text": "aa", "title": None} for u in urls]
        b_used = sum(len(str(p["text"]).encode()) for p in pages)
        return pages, 0, None, b_used, "no_more_links"

    monkeypatch.setattr(svc, "_fetch_pages_for_urls", fake_fetch)
    monkeypatch.setattr(svc, "_dashboard_update_crawl_job_progress", AsyncMock())
    monkeypatch.setattr(svc, "_dashboard_persist_crawl_pages", AsyncMock())
    monkeypatch.setattr(svc, "_exclude_remaining_queued_pages_for_run", AsyncMock())

    uid = uuid4()
    sid = uuid4()
    aid = uuid4()
    now = datetime.now(timezone.utc)
    src = KnowledgeSourceDTO(
        id=sid,
        agent_id=aid,
        user_id=uid,
        type="website",
        title="t",
        status="indexing",
        source_url="https://ex.com",
        storage_bucket=None,
        storage_path=None,
        metadata={},
        error_message=None,
        last_indexed_at=None,
        created_at=now,
        updated_at=now,
    )
    urls = [f"https://ex.com/p{i}" for i in range(16)]

    class _Sess:
        async def commit(self) -> None:
            return None

    await svc._dashboard_fetch_planned_urls_in_batches(
        _Sess(),
        job_id=uuid4(),
        source=src,
        user_id=uid,
        crawl_run_id=uuid4(),
        urls=urls,
        crawl_budget_bytes=500,
    )

    assert budgets_seen == [500]


@pytest.mark.asyncio
async def test_embed_texts_batches_large_chunk_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI rejects >~300k tokens per embeddings call; ensure we split into multiple requests."""
    from app.domains import knowledge

    svc = knowledge.service
    batch_input_lens: list[int] = []

    class _Settings:
        openai_api_key = "test-key"
        openai_embedding_model = "text-embedding-3-small"

    class _FakeResponse:
        def __init__(self, n: int) -> None:
            self.status_code = 200
            self._n = n

        def json(self) -> dict:
            return {"data": [{"embedding": [0.0] * svc.EMBEDDING_DIMENSION} for _ in range(self._n)]}

    class _FakeClient:
        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def post(self, *_a: object, json: dict | None = None, **_k: object) -> _FakeResponse:
            assert json is not None
            inp = json["input"]
            batch_input_lens.append(len(inp))
            return _FakeResponse(len(inp))

    monkeypatch.setattr(svc, "get_settings", lambda: _Settings())
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda *a, **k: _FakeClient())

    chunk = "word " * 200  # ~1000 chars → several hundred estimated tokens each
    chunks = [f"{chunk}{i}" for i in range(900)]
    out = await svc._embed_texts(chunks)
    assert len(out) == len(chunks)
    assert len(batch_input_lens) > 1
    assert all(n <= svc._EMBED_BATCH_MAX_INPUTS for n in batch_input_lens)
