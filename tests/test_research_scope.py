"""Prevent removed research sources from leaking into search presets or defaults."""
import json
from pathlib import Path
from literature_monitor.core.journals import resolve_issns, list_journal_pools
from literature_monitor.core.topics import list_search_topics
from literature_monitor.core.utils import get_issn_by_tier, get_issn_list

REMOVED_ISSNS = set(["0018-8158","0024-3590","0025-326X","0034-4257","0046-5070","0167-6369","0171-8630","0196-2892","0272-7714","0924-2716","0964-5691","1015-1621","1385-1101","1470-160X","1541-5856","1548-1603","1568-9883","1569-8432","1574-9541","1939-1404","2044-2041","2072-4292","2211-9264","2296-665X","2296-7745","2352-9385","2666-0172"])
ROOT = Path(__file__).resolve().parents[1]

def test_removed_sources_are_absent_from_every_default_and_tier():
    scopes = [resolve_issns([], []), get_issn_list()]
    scopes += [get_issn_by_tier([tier])[0] for tier in ("S", "A", "B")]
    scopes += [pool["issns"] for pool in list_journal_pools()]
    assert all(REMOVED_ISSNS.isdisjoint(issns) for issns in scopes)

def test_catalog_and_exposed_topic_presets_do_not_advertise_removed_scope():
    catalog = (ROOT / "config/journals.json").read_text().lower()
    assert all(word not in catalog for word in ("algal", "bloom", "remote sensing", "遥感", "藻华"))
    assert [t["id"] for t in list_search_topics()] == ["gfcf_environment"]
    assert not (ROOT / "config/search_topics.json").exists()

def test_catalog_mapping_and_default_scope_stay_consistent():
    data = json.loads((ROOT / "config/journals.json").read_text())
    all_issns = set()
    for pool_id, pool in data["pools"].items():
        declared = {j["issn"] for c in pool["categories"].values() for j in c.get("journals", [])}
        assert declared == set(data["pool_issn_mapping"][pool_id])
        all_issns.update(declared)
    assert all_issns == set(data["issn_list"])
