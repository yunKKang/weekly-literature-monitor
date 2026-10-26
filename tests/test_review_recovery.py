"""Scope, AI evidence and recovery regressions (no network or real credentials)."""

from dataclasses import replace
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import weekly_monitor as weekly
import state_manager
from literature_monitor.core.models import (
    Paper,
    SearchRequest,
    ProviderPaper,
    PaperSourceRecord,
)
from literature_monitor.core.relevance_filter import load_keyword_config, score_paper
from literature_monitor.core.journals import (
    monitoring_issns,
    validate_catalog,
    resolve_issns,
)
from literature_monitor.core.review_cache import ReviewCache
from literature_monitor.core.review_service import review_papers, score_groups
from literature_monitor.pipeline.base import PipelineState
from literature_monitor.pipeline.stages.llm_review import LLMReviewStage
from test_monitor_reliability import FakePaper

APPROVED = {
    "relevant": True,
    "confidence": "high",
    "reason": "Physical capital footprint quantified using MRIO",
}
ABSTRACT = "We quantify the embodied carbon footprint of gross fixed capital formation using MRIO and IAM scenarios across global supply chains."


def test_synonyms_score_equally_and_configured_threshold_controls_priority():
    cfg = load_keyword_config()
    long = score_paper("Gross fixed capital formation and embodied carbon", "", cfg)
    short = score_paper("GFCF and embodied carbon", "", cfg)
    assert long.score == short.score == 25
    cfg.scoring_rules["high_threshold"] = 100
    assert score_paper("GFCF and embodied carbon", "", cfg).priority == "MEDIUM"


@pytest.mark.parametrize(
    "title",
    [
        "Household MRIO footprints under IAM scenarios",
        "SSP2 projections of input–output tables",
        "Future EXIOBASE pathways without capital endogenization",
    ],
)
def test_independent_scenario_method_channel_needs_no_investment(title):
    paper = Paper(
        "10.1234/method",
        title,
        abstract=ABSTRACT.replace(
            "gross fixed capital formation", "household consumption"
        ),
    )
    req = SearchRequest("2026-01-01", "2026-10-26", topic_id="gfcf_environment")
    outcome = review_papers([paper], req, include_ai=False)[0][1]
    assert not outcome.excluded
    assert "IAM / Prospective MRIO Methods" in outcome.matched_pipelines


@pytest.mark.parametrize(
    "title",
    [
        "PIM-1 membranes for carbon capture",
        "Pension fund investment and carbon emissions",
        "Investment policy and growth",
        "GCF-Net classification of carbon materials",
        "FCF financial portfolio and supply chain policy",
    ],
)
def test_removed_noise_cannot_qualify(title):
    result = score_paper(title, "", load_keyword_config())
    assert result.is_excluded or result.score == 0


def test_valid_catalog_and_monthly_frequency():
    validate_catalog()
    assert len(resolve_issns([], [])) == 31
    assert len(monitoring_issns("2026-10-26")) == 31
    assert len(monitoring_issns("2026-11-02")) == 40
    assert len(monitoring_issns("2026-10-26", include_supplementary=True)) == 40
    assert {"2328-8930", "2662-1355"} <= set(monitoring_issns("2026-11-02"))


def state(**kw):
    return PipelineState(
        doi="10.1234/a",
        title="GFCF and embodied carbon",
        abstract=ABSTRACT,
        authors=("Jane Doe",),
        total_score=30,
        relevance_level="HIGH",
        **kw,
    )


def test_cache_reuses_verdict_and_invalidates_on_content_model_or_rules(
    tmp_path, monkeypatch
):
    path = tmp_path / "review.json"
    cache = ReviewCache(path)
    with patch.object(LLMReviewStage, "_call_llm", return_value=APPROVED) as call:
        first = LLMReviewStage(api_key="fake", cache=cache).run(state())
        second = LLMReviewStage(api_key="fake", cache=cache).run(state())
        assert first.total_score == second.total_score == 35
        assert call.call_count == 1
        LLMReviewStage(api_key="fake", cache=cache, model="another").run(state())
        LLMReviewStage(api_key="fake", cache=cache).run(
            replace(state(), abstract=ABSTRACT + " Extra evidence.")
        )
        monkeypatch.setattr(
            "literature_monitor.core.review_cache.rules_version", lambda: "changed"
        )
        LLMReviewStage(api_key="fake", cache=cache).run(state())
        assert call.call_count == 4
    cache.save()
    assert ReviewCache(path).candidates()[0].authors == ["Jane Doe"]


@pytest.mark.parametrize(
    "response,status",
    [
        (None, "failed"),
        (
            {"relevant": True, "confidence": "low", "reason": "insufficient"},
            "uncertain",
        ),
    ],
)
def test_failed_or_uncertain_review_stays_pending(response, status):
    with patch.object(LLMReviewStage, "_call_llm", return_value=response):
        result = LLMReviewStage(api_key="fake").run(state())
    assert result.breakdown["llm_review"]["status"] == status


def test_ai_budget_is_bounded_even_on_failure():
    stage = LLMReviewStage(api_key="fake", max_papers=1)
    with patch.object(stage, "_call_llm", return_value=None) as call:
        stage.run(state())
        result = stage.run(replace(state(), doi="10.1234/b"))
    assert call.call_count == 1
    assert result.breakdown["llm_review"]["status"] == "budget_skipped"


def test_deepseek_uses_supported_json_and_non_thinking_request():
    stage = LLMReviewStage(api_key="fake")
    response = {
        "choices": [{"message": {"content": json.dumps(APPROVED)}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 30},
    }
    with patch.object(stage, "_post_openai_chat", return_value=response) as post:
        assert stage.run(state()).breakdown["llm_review"]["status"] == "completed"
    body = post.call_args.args[0]
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}


@pytest.fixture
def scheduled(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    cache = tmp_path / "cache.json"
    state_manager.save_state(
        state_manager.MonitorState(last_from_date="2026-10-19"), path
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(weekly, "load_state", lambda: state_manager.load_state(path))
    monkeypatch.setattr(
        weekly, "save_state", lambda s: state_manager.save_state(s, path)
    )
    monkeypatch.setattr(weekly, "ReviewCache", lambda *a: ReviewCache(cache))
    monkeypatch.setattr(weekly, "today_str", lambda: "2026-10-26")
    monkeypatch.setattr(weekly, "enrich_abstract", lambda p: p)
    monkeypatch.setattr(weekly.github_issue, "list_report_issues", lambda *a: [])
    paper = FakePaper()
    paper.abstract = ABSTRACT
    paper.to_dict = lambda: {"abstract": ABSTRACT, "authors": ["Jane Doe"]}
    monkeypatch.setattr(weekly, "fetch_recent_papers", lambda **kw: [paper])
    return path, cache


def test_pending_queue_retries_after_acquisition_watermark_advances(scheduled):
    path, cache = scheduled
    with (
        patch.object(LLMReviewStage, "_call_llm", return_value=None),
        patch.object(weekly, "notify_new_papers", return_value=True) as notify,
    ):
        assert weekly.run_monitor(verbose=False) == 0
        assert notify.call_args.args[0] == []
    saved = state_manager.load_state(path)
    assert saved.last_from_date == "2026-10-26"
    assert saved.review_pending_count == 1
    assert saved.last_review_complete_date is None
    assert cache.exists()
    # The source no longer returns this DOI, but the pending queue still does.
    with (
        patch.object(weekly, "fetch_recent_papers", return_value=[]),
        patch.object(LLMReviewStage, "_call_llm", return_value=APPROVED),
        patch.object(weekly, "notify_new_papers", return_value=True) as notify,
    ):
        assert weekly.run_monitor(verbose=False) == 0
        papers = notify.call_args.args[0]
        assert len(papers) == 1 and papers[0].ai_status == "completed"
        assert papers[0].authors == ["Jane Doe"]
    assert state_manager.load_state(path).review_pending_count == 0


def test_reprocessing_ignores_seen_but_excludes_reported_dois(scheduled):
    path, cache = scheduled
    s = state_manager.load_state(path)
    s.seen_dois_list = ["10.1234/example"]
    state_manager.save_state(s, path)
    with (
        patch.object(LLMReviewStage, "_call_llm", return_value=APPROVED),
        patch.object(weekly, "notify_new_papers", return_value=True) as notify,
    ):
        weekly.run_monitor(verbose=False, reprocess_since="2026-07-25")
        assert len(notify.call_args.args[0]) == 1
    with (
        patch.object(
            weekly.github_issue, "reported_dois", return_value={"10.1234/example"}
        ),
        patch.object(LLMReviewStage, "_call_llm") as ai,
        patch.object(weekly, "notify_new_papers", return_value=True) as notify,
    ):
        weekly.run_monitor(verbose=False, reprocess_since="2026-07-25")
        assert notify.call_args.args[0] == []
        ai.assert_not_called()
    assert state_manager.load_state(path).last_from_date == "2026-10-26"


def test_dry_run_preserves_bytes_and_makes_no_ai_calls(scheduled):
    path, cache = scheduled
    before = path.read_bytes()
    with (
        patch.object(LLMReviewStage, "_call_llm") as ai,
        patch.object(weekly, "notify_new_papers") as notify,
    ):
        assert weekly.run_monitor(dry_run=True, verbose=False) == 0
    assert path.read_bytes() == before and not cache.exists()
    ai.assert_not_called()
    notify.assert_not_called()


def test_missing_credential_aborts_before_fetch_or_state_changes(
    scheduled, monkeypatch
):
    path, cache = scheduled
    before = path.read_bytes()
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with patch.object(weekly, "fetch_recent_papers") as fetch:
        assert weekly.run_monitor(verbose=False) == 2
    fetch.assert_not_called()
    assert path.read_bytes() == before and not cache.exists()


def test_manual_review_always_persists_ai_audit_once(monkeypatch):
    import sqlite3
    from literature_monitor.db.schema import init_db
    from literature_monitor.db import repositories as repo

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    req = SearchRequest(
        "2026-01-01",
        "2026-10-26",
        topic_id="gfcf_environment",
        journal_pool_ids=["main_pool"],
    )
    from dataclasses import asdict

    run_id = repo.create_search_run(conn, asdict(req), req.date_from, req.date_to)
    paper = Paper("10.1234/a", "GFCF and embodied carbon", abstract=ABSTRACT)
    record = ProviderPaper(paper, PaperSourceRecord("test", "a", paper.doi, {}))
    with patch.object(LLMReviewStage, "_call_llm", return_value=APPROVED) as ai:
        assert score_groups(conn, run_id, req, [[record]], set(), []) == 1
        assert ai.call_count == 1
    audit = json.loads(
        conn.execute("SELECT score_breakdown_json FROM scored_results").fetchone()[0]
    )["llm_review"]
    assert audit["status"] == "completed" and audit["model"] == "deepseek-flash"


@pytest.mark.asyncio
async def test_async_search_uses_same_review_and_records_failure_status(
    tmp_path, monkeypatch
):
    from literature_monitor.core import async_search_service as service
    from literature_monitor.db.connection import get_connection

    monkeypatch.setenv("LITMON_DB_PATH", str(tmp_path / "async.db"))
    p = Paper("10.1234/async", "GFCF and embodied carbon", abstract=ABSTRACT)

    async def fetch(*a, **kw):
        return [ProviderPaper(p, PaperSourceRecord("test", "async", p.doi, {}))], []

    monkeypatch.setattr(service, "fetch_all_providers", fetch)
    req = SearchRequest(
        "2026-01-01",
        "2026-10-26",
        topic_id="gfcf_environment",
        journal_pool_ids=["main_pool"],
    )
    with patch.object(LLMReviewStage, "_call_llm", return_value=APPROVED) as ai:
        result = await service.async_create_and_run_search(req)
        assert result.status == "completed" and ai.call_count == 1
    with get_connection() as conn:
        audit = json.loads(
            conn.execute("SELECT score_breakdown_json FROM scored_results").fetchone()[
                0
            ]
        )["llm_review"]
    assert audit["status"] == "completed"
    with pytest.raises(ValueError, match="Unknown topic"):
        await service.async_create_and_run_search(replace(req, topic_id="removed"))
    with get_connection() as conn:
        assert (
            conn.execute(
                "SELECT status FROM search_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
            == "failed"
        )


def test_large_report_parts_have_complete_separate_manifests(monkeypatch):
    import github_issue as issues
    from test_crossref_recovery import paper_info, report

    monkeypatch.setattr(issues.config, "GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(issues.config, "GITHUB_REPOSITORY", "owner/repo")
    papers = [
        replace(
            paper_info("10.1234/" + str(i)),
            ai_reason="evidence " * 350,
            ai_status="completed",
        )
        for i in range(40)
    ]
    with (
        patch.object(issues, "list_report_issues", return_value=[]),
        patch.object(issues, "create_github_issue", return_value={"number": 1}) as post,
    ):
        assert issues.notify_new_papers(papers, "2026-07-25", "2026-10-26", 100, 40)
    assert post.call_count > 1
    manifests = [
        issues.reported_dois([report(call.args[1])]) for call in post.call_args_list
    ]
    assert sum(map(len, manifests)) == len(set.union(*manifests)) == 40
    assert all(len(call.args[1]) <= 50000 for call in post.call_args_list)


@pytest.mark.parametrize(
    "client_name", ["paper_utils", "literature_monitor.core.utils"]
)
def test_get_retries_rate_limit_using_retry_after(client_name):
    import importlib, urllib.error, httpx

    client = importlib.import_module(client_name)
    if client_name == "paper_utils":
        from unittest.mock import MagicMock

        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b"ok"
        error = urllib.error.HTTPError(
            "https://example.com", 429, "limited", {"Retry-After": "4"}, None
        )
        request = patch.object(
            client.urllib.request, "urlopen", side_effect=[error, response]
        )
    else:
        from unittest.mock import MagicMock

        http = MagicMock()
        http.__enter__.return_value = http
        http.get.side_effect = [
            httpx.Response(
                429,
                headers={"Retry-After": "4"},
                request=httpx.Request("GET", "https://example.com"),
            ),
            httpx.Response(
                200, content=b"ok", request=httpx.Request("GET", "https://example.com")
            ),
        ]
        request = patch("httpx.Client", return_value=http)
    with request, patch.object(client.time, "sleep") as sleep:
        assert client.fetch_url("https://example.com", max_retries=1) == (200, b"ok")
        sleep.assert_called_once_with(4.0)


@pytest.mark.parametrize("start", ["20261001", "2026-13-01", "2027-01-01"])
def test_invalid_reprocessing_date_never_fetches(scheduled, start):
    with patch.object(weekly, "fetch_recent_papers") as fetch:
        with pytest.raises(ValueError):
            weekly.run_monitor(verbose=False, reprocess_since=start)
    fetch.assert_not_called()


def test_monthly_journals_cover_time_since_previous_supplementary_run(scheduled):
    path, _ = scheduled
    s = state_manager.load_state(path)
    s.last_from_date = "2026-10-26"
    s.last_supplementary_date = "2026-10-05"
    state_manager.save_state(s, path)
    with (
        patch.object(weekly, "today_str", return_value="2026-11-02"),
        patch.object(weekly, "fetch_recent_papers", return_value=[]) as fetch,
        patch.object(weekly, "notify_new_papers", return_value=True),
    ):
        weekly.run_monitor(verbose=False, overlap_days=0)
    assert fetch.call_args.kwargs["from_date"] == "2026-10-05"
    assert len(fetch.call_args.kwargs["issns"]) == 40
    assert state_manager.load_state(path).last_supplementary_date == "2026-11-02"
