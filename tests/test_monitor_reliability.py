import re
import sys
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import crossref_client
import obsidian_report
import paper_utils
import weekly_monitor
from relevance_filter import RelevanceResult, compile_patterns
from state_manager import MonitorState


class FakePaper:
    doi = "10.1234/example"
    title = "Capital investment and carbon emissions"
    authors = [{"name": "Doe, Jane"}]
    journal = "Example Journal"
    year = "2026"
    publication_date = "2026-04-01"
    abstract = None

    def to_dict(self):
        return {
            "doi": self.doi,
            "title": self.title,
            "abstract": "Investment in infrastructure affects embodied carbon.",
            "publication_date": self.publication_date,
        }


def relevance(score=20, priority="HIGH"):
    return RelevanceResult(
        doi="10.1234/example",
        title="Capital investment and carbon emissions",
        score=score,
        priority=priority,
        matched_pipeline="GFCF x Environmental Impact Accounting",
        matched_pipelines=["GFCF x Environmental Impact Accounting"],
        matched_investment_terms=["investment"],
        matched_domain_terms=["carbon"],
        matched_bonus_terms=[],
        matched_asset_types=["structures"],
        matched_themes=[],
        matched_keywords=["investment", "carbon"],
        matched_tiers=["GFCF x Environmental Impact Accounting"],
        is_excluded=False,
        exclusion_reason=None,
        passed_consistency=True,
        negative_matches=[],
    )


class WeeklyMonitorReliabilityTests(unittest.TestCase):
    def test_notify_failure_does_not_save_state(self):
        paper = FakePaper()
        state = MonitorState(last_from_date="2026-03-01")
        fetch_recent = Mock(return_value=[paper])

        with (
            patch.object(weekly_monitor, "load_state", return_value=state),
            patch.object(weekly_monitor, "fetch_recent_papers", fetch_recent),
            patch.object(weekly_monitor, "fetch_conference_papers", return_value=[]),
            patch.object(weekly_monitor, "load_keyword_config", return_value=object()),
            patch.object(
                weekly_monitor,
                "filter_papers",
                return_value=[(paper.to_dict(), relevance())],
            ),
            patch.object(weekly_monitor, "notify_new_papers", return_value=False),
            patch.object(weekly_monitor, "save_state") as save_state,
        ):
            result = weekly_monitor.run_monitor(verbose=False)

        self.assertNotEqual(result, 0)
        save_state.assert_not_called()
        self.assertEqual(state.last_from_date, "2026-03-01")
        self.assertEqual(fetch_recent.call_args.kwargs["from_date"], "2026-01-30")

    def test_no_new_papers_updates_state_after_successful_fetch(self):
        paper = FakePaper()
        state = MonitorState(
            last_from_date="2026-04-01",
            seen_dois_list=[paper.doi],
            run_count=2,
        )

        with (
            patch.object(weekly_monitor, "load_state", return_value=state),
            patch.object(weekly_monitor, "today_str", return_value="2026-04-10"),
            patch.object(weekly_monitor, "fetch_recent_papers", return_value=[paper]),
            patch.object(weekly_monitor, "fetch_conference_papers", return_value=[]),
            patch.object(weekly_monitor, "filter_papers") as filter_papers,
            patch.object(weekly_monitor, "notify_new_papers") as notify_new_papers,
            patch.object(weekly_monitor, "save_state") as save_state,
        ):
            result = weekly_monitor.run_monitor(verbose=False)

        self.assertEqual(result, 0)
        filter_papers.assert_not_called()
        notify_new_papers.assert_not_called()
        save_state.assert_called_once()
        saved_state = save_state.call_args.args[0]
        self.assertEqual(saved_state.last_run_date, "2026-04-10")
        self.assertEqual(saved_state.last_from_date, "2026-04-10")
        self.assertEqual(saved_state.run_count, 3)
        self.assertEqual(saved_state.seen_dois_list, [paper.doi])

    def test_crossref_search_raises_on_http_error(self):
        params = crossref_client.SearchParams(year_from="2026-01-01")

        with patch.object(crossref_client, "fetch_url", return_value=(500, b"boom")):
            with self.assertRaises(Exception):
                crossref_client.search_crossref(params)

    def test_fetch_url_retries_once_on_network_error(self):
        response = Mock()
        response.status = 200
        response.read.return_value = b"ok"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch.object(
            paper_utils.urllib.request,
            "urlopen",
            side_effect=[urllib.error.URLError("boom"), response],
        ) as urlopen:
            status, body = paper_utils.fetch_url(
                "https://example.com",
                timeout_s=1,
                max_retries=1,
                retry_delay_s=0,
            )

        self.assertEqual(status, 200)
        self.assertEqual(body, b"ok")
        self.assertEqual(urlopen.call_count, 2)

    def test_fetch_recent_papers_propagates_batch_failures(self):
        with patch.object(
            crossref_client,
            "search_crossref_page",
            side_effect=RuntimeError("network down"),
        ):
            with self.assertRaises(Exception):
                crossref_client.fetch_recent_papers(
                    ["1234-5678"],
                    "2026-01-01",
                    "2026-01-02",
                    delay_s=0,
                    batch_size=1,
                )

    def test_fetch_recent_papers_pages_and_deduplicates_cursor_results(self):
        paper_a = FakePaper()
        paper_b = Mock()
        paper_b.doi = "10.1234/second"

        pages = [
            crossref_client.CrossrefPage(
                total_results=2,
                results=[paper_a],
                next_cursor="cursor-2",
            ),
            crossref_client.CrossrefPage(
                total_results=2,
                results=[paper_a, paper_b],
                next_cursor="cursor-3",
            ),
            crossref_client.CrossrefPage(
                total_results=2,
                results=[],
                next_cursor="cursor-3",
            ),
        ]

        with patch.object(
            crossref_client,
            "search_crossref_page",
            side_effect=pages,
        ) as search_page:
            results = crossref_client.fetch_recent_papers(
                ["1234-5678"],
                "2026-01-01",
                "2026-01-02",
                delay_s=0,
                batch_size=1,
            )

        self.assertEqual(
            [paper.doi for paper in results],
            ["10.1234/example", "10.1234/second"],
        )
        self.assertEqual(search_page.call_count, 3)
        self.assertEqual(
            [call.args[0].cursor for call in search_page.call_args_list],
            ["*", "cursor-2", "cursor-3"],
        )

    def test_invalid_regex_pattern_raises(self):
        with self.assertRaises(re.error):
            compile_patterns(["["])


class ObsidianStateTests(unittest.TestCase):
    def test_obsidian_export_uses_explicit_state_path(self):
        paper = FakePaper()

        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "out"
            state_path = Path(tmp) / "obsidian_state.json"

            with (
                patch.object(obsidian_report, "fetch_papers", return_value=([paper], 1)),
                patch.object(obsidian_report, "filter_by_score", return_value=[(paper, relevance())]),
            ):
                obsidian_report.run_export(
                    from_date="2026-04-01",
                    to_date="2026-04-02",
                    output_dir=output_dir,
                    min_score=5,
                    update_state=True,
                    state_path=state_path,
                )

            self.assertTrue(state_path.exists())


class WorkflowSafetyTests(unittest.TestCase):
    def test_workflow_validates_and_quotes_days_back(self):
        workflow = (ROOT / ".github" / "workflows" / "weekly-monitor.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('[[ "$DAYS_BACK" =~ ^[0-9]+$ ]]', workflow)
        self.assertIn('python weekly_monitor.py --days "$DAYS_BACK"', workflow)


if __name__ == "__main__":
    unittest.main()
