"""Regression coverage for the August 2026 Crossref cursor change and recovery."""

import json
import sys
import urllib.error
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import crossref_client as legacy
import github_issue
import state_manager
import weekly_monitor
from literature_monitor.providers import crossref_client as packaged
from test_monitor_reliability import FakePaper, relevance


@pytest.fixture(autouse=True)
def isolated_weekly_ai(tmp_path, monkeypatch):
    from literature_monitor.core.review_cache import ReviewCache

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(
        weekly_monitor, "ReviewCache", lambda *a: ReviewCache(tmp_path / "review.json")
    )
    monkeypatch.setattr(weekly_monitor, "enrich_abstract", lambda paper: paper)
    monkeypatch.setattr(
        "literature_monitor.pipeline.stages.llm_review.LLMReviewStage._call_llm",
        lambda *a: {
            "relevant": True,
            "confidence": "high",
            "reason": "capital footprint",
        },
    )


def work(doi="10.1234/example"):
    return {
        "DOI": doi,
        "title": ["Capital investment and carbon emissions"],
        "type": "proceedings-article",
    }


def response(items, cursor=None):
    return 200, json.dumps(
        {
            "message-type": "work-list",
            "message": {
                "items": items,
                "next-cursor": cursor,
                "total-results": 3,
            },
        }
    ).encode()


@pytest.mark.parametrize("client", [legacy, packaged])
def test_conference_cursor_requests_keep_filters_without_publication_sort(client):
    replies = [
        response([work(), work("10.1234/SECOND")], "next/+?=&"),
        response([work("https://doi.org/10.1234/EXAMPLE")]),
    ]
    with patch.object(client, "fetch_url", side_effect=replies) as fetch:
        papers = client.fetch_conference_papers(
            ["SIGCOMM & test"],
            "2026-08-24",
            "2026-10-26",
            max_per_conference=2,
            delay_s=0,
        )
    queries = [parse_qs(urlsplit(c.args[0]).query) for c in fetch.call_args_list]
    assert [q.pop("cursor") for q in queries] == [["*"], ["next/+?=&"]]
    assert queries[0] == queries[1]
    assert queries[0]["query.container-title"] == ["SIGCOMM & test"]
    assert queries[0]["filter"] == [
        "from-pub-date:2026-08-24,until-pub-date:2026-10-26,type:proceedings-article"
    ]
    assert "sort" not in queries[0] and "order" not in queries[0]
    assert [p.doi for p in papers] == ["10.1234/example", "10.1234/second"]


@pytest.mark.parametrize("client", [legacy, packaged])
def test_journal_sort_only_applies_without_cursor(client):
    for cursor in ("*", "second-page"):
        query = parse_qs(
            client.build_crossref_query(client.SearchParams(cursor=cursor))
        )
        assert "sort" not in query and "order" not in query
    query = parse_qs(client.build_crossref_query(client.SearchParams()))
    assert query["sort"] == ["published"]


@pytest.mark.parametrize("client", [legacy, packaged])
@pytest.mark.parametrize(
    "reply",
    [
        (400, b"sort-criteria-incompatible-with-cursor"),
        (200, b'{"message-type":"validation-failure"}'),
    ],
)
def test_conference_errors_are_not_reported_as_empty_success(client, reply):
    with patch.object(client, "fetch_url", return_value=reply):
        with pytest.raises(client.CrossrefBatchError):
            client.fetch_conference_papers(["SIGCOMM"], "2026-08-24", delay_s=0)


@pytest.mark.parametrize("client", [legacy, packaged])
def test_partial_journal_failure_does_not_return_incomplete_results(client):
    page = client.CrossrefPage(1, [FakePaper()], None)
    with patch.object(
        client, "search_crossref_page", side_effect=[page, RuntimeError("down")]
    ):
        with pytest.raises(client.CrossrefBatchError, match="down"):
            client.fetch_recent_papers(
                ["1111-1111", "2222-2222"], "2026-08-24", batch_size=1, delay_s=0
            )


@pytest.mark.parametrize("client", [legacy, packaged])
def test_cursor_limit_fails_instead_of_silently_truncating(client):
    with (
        patch.object(client, "fetch_url", return_value=response([work()], "next")),
        patch.object(client.config, "MAX_CURSOR_PAGES", 1),
    ):
        with pytest.raises(client.CrossrefBatchError, match="MAX_CURSOR_PAGES"):
            client.fetch_conference_papers(
                ["SIGCOMM"], "2026-08-24", max_per_conference=1, delay_s=0
            )


def test_failed_conference_fetch_leaves_checkpoint_and_dois_unchanged():
    state = state_manager.MonitorState(last_from_date="2026-08-24", run_count=29)
    before = deepcopy(state)
    with (
        patch.object(weekly_monitor, "load_state", return_value=state),
        patch.object(
            weekly_monitor,
            "fetch_recent_papers",
            side_effect=legacy.CrossrefBatchError("400"),
        ),
        patch.object(weekly_monitor, "save_state") as save,
        patch.object(weekly_monitor, "notify_new_papers") as notify,
    ):
        with pytest.raises(legacy.CrossrefBatchError):
            weekly_monitor.run_monitor(verbose=False)
    assert state == before
    save.assert_not_called()
    notify.assert_not_called()


def test_state_normalizes_dois_and_keeps_fifo_order(tmp_path):
    state = state_manager.MonitorState(
        seen_dois_list=["https://doi.org/10.1234/FIRST", "10.1234/second"]
    )
    state_manager.update_state_after_run(
        state,
        "2026-10-26",
        "2026-10-26",
        ["10.1234/FIRST", "10.1234/third", "10.1234/THIRD"],
        0,
    )
    assert state.seen_dois_list == ["10.1234/first", "10.1234/second", "10.1234/third"]
    assert state.total_papers_found == 1
    path = tmp_path / "state.json"
    state_manager.save_state(state, path)
    assert state_manager.load_state(path) == state
    path.write_text("broken")
    with pytest.raises(RuntimeError):
        state_manager.load_state(path)


def report(body):
    return {"title": "[Weekly Report] previous", "body": body}


def paper_info(doi="10.1234/example"):
    return github_issue.PaperInfo(
        doi,
        "Title",
        [],
        "Journal",
        "2026",
        20,
        "HIGH",
        [],
        [],
        "https://doi.org/" + doi,
    )


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setattr(github_issue.config, "GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(github_issue.config, "GITHUB_REPOSITORY", "owner/repo")


def test_history_reads_all_pages_including_closed_reports():
    first = [report("")] * 100
    last = [report("**DOI:** [10.1234/OLD](url)")]
    with patch.object(
        github_issue,
        "fetch_url",
        side_effect=[
            (200, json.dumps(first).encode()),
            (200, json.dumps(last).encode()),
        ],
    ) as fetch:
        issues = github_issue.list_report_issues("owner/repo", "token")
    assert github_issue.reported_dois(issues) == {"10.1234/old"}
    assert "state=all" in fetch.call_args_list[0].args[0]
    assert "page=2" in fetch.call_args_list[1].args[0]


def test_published_dois_survive_state_eviction_and_case_variants(credentials):
    issues = [report("**DOI:** [10.1234/EXAMPLE](url)")]
    with (
        patch.object(github_issue, "list_report_issues", return_value=issues),
        patch.object(
            github_issue, "create_github_issue", return_value={"number": 2}
        ) as create,
    ):
        assert github_issue.notify_new_papers(
            [
                paper_info("https://doi.org/10.1234/EXAMPLE"),
                paper_info("10.1234/new"),
                paper_info("10.1234/NEW"),
            ],
            "2026-07-25",
            "2026-10-26",
            3,
            72,
        )
    assert create.call_count == 1
    assert github_issue.reported_dois([report(create.call_args.args[1])]) == {
        "10.1234/new"
    }


def test_history_read_failure_prevents_publication(credentials):
    with (
        patch.object(
            github_issue, "list_report_issues", side_effect=RuntimeError("403")
        ),
        patch.object(github_issue, "create_github_issue") as create,
    ):
        assert not github_issue.notify_new_papers(
            [paper_info()], "2026-08-24", "2026-10-26", 1, 72
        )
    create.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("response lost"),
        TimeoutError("timeout"),
        urllib.error.HTTPError("url", 502, "bad gateway", {}, None),
    ],
)
def test_ambiguous_issue_creation_does_not_blindly_retry_post(error):
    with patch.object(
        github_issue.urllib.request, "urlopen", side_effect=error
    ) as post:
        assert (
            github_issue.create_github_issue(
                "title", "body", repo="owner/repo", token="token"
            )
            is None
        )
    assert post.call_count == 1


def test_post_success_then_checkpoint_failure_is_safe_to_resume(tmp_path, credentials):
    path = tmp_path / "state.json"
    state_manager.save_state(
        state_manager.MonitorState(
            last_run_date="2026-08-24",
            last_from_date="2026-08-24",
            run_count=29,
        ),
        path,
    )
    before = path.read_bytes()
    issues = []

    def publish(title, body, labels):
        issues.append(report(body))
        return {"html_url": "https://github.com/owner/repo/issues/1"}

    with (
        patch.object(
            weekly_monitor,
            "load_state",
            side_effect=lambda: state_manager.load_state(path),
        ),
        patch.object(weekly_monitor, "today_str", return_value="2026-10-26"),
        patch.object(
            weekly_monitor,
            "fetch_recent_papers",
            side_effect=lambda **kw: [FakePaper(), FakePaper()],
        ) as fetch,
        patch.object(weekly_monitor, "load_keyword_config", return_value=object()),
        patch.object(
            weekly_monitor,
            "filter_papers",
            side_effect=lambda papers, *a, **kw: [(p, relevance()) for p in papers],
        ),
        patch.object(github_issue, "list_report_issues", side_effect=lambda *a: issues),
        patch.object(github_issue, "create_github_issue", side_effect=publish) as post,
    ):
        with patch.object(
            weekly_monitor, "save_state", side_effect=OSError("push/disk failed")
        ):
            with pytest.raises(OSError):
                weekly_monitor.run_monitor(verbose=False)
        assert path.read_bytes() == before
        with patch.object(
            weekly_monitor,
            "save_state",
            side_effect=lambda s: state_manager.save_state(s, path),
        ):
            assert weekly_monitor.run_monitor(verbose=False) == 0
            assert weekly_monitor.run_monitor(verbose=False) == 0
        assert post.call_count == 1
        assert fetch.call_args_list[0].kwargs["from_date"] == "2026-07-25"
    state = state_manager.load_state(path)
    assert state.last_from_date == "2026-10-26"
    assert state.seen_dois_list == ["10.1234/example"]
    assert state.run_count == 31


def test_dry_run_does_not_mutate_or_save_state():
    state = state_manager.MonitorState(last_from_date="2026-08-24")
    before = deepcopy(state)
    with (
        patch.object(weekly_monitor, "load_state", return_value=state),
        patch.object(weekly_monitor, "fetch_recent_papers", return_value=[FakePaper()]),
        patch.object(weekly_monitor, "load_keyword_config", return_value=object()),
        patch.object(weekly_monitor, "filter_papers", return_value=[]),
        patch.object(weekly_monitor, "save_state") as save,
        patch.object(weekly_monitor, "notify_new_papers") as notify,
    ):
        assert weekly_monitor.run_monitor(dry_run=True, verbose=False) == 0
    assert state == before
    save.assert_not_called()
    notify.assert_not_called()
