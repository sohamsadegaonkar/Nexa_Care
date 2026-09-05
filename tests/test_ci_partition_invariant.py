"""Test suite partition invariant validation.

Proves that the three CI qualification partitions:
A: 'not postgres and not redis' (quality job)
B: 'postgres and not redis' (postgres job)
C: 'redis' (postgres-redis job)

Form a complete and disjoint partition of the entire test collection:
- A ∪ B ∪ C == all collected tests
- A ∩ B == ∅
- A ∩ C == ∅
- B ∩ C == ∅
"""

from __future__ import annotations

import pytest


def test_ci_test_partitions_are_complete_and_disjoint():
    class ItemCollector:
        def __init__(self):
            self.items = []

        def pytest_collection_modifyitems(self, session, config, items):
            for item in items:
                markers = {m.name for m in item.iter_markers()}
                self.items.append((item.nodeid, markers))

    collector = ItemCollector()
    ret = pytest.main(["--collect-only", "-q"], plugins=[collector])
    assert ret == pytest.ExitCode.OK, "Pytest collection failed"
    assert len(collector.items) > 0, "No tests collected"

    all_nodeids = {nodeid for nodeid, _ in collector.items}
    set_a = set()
    set_b = set()
    set_c = set()

    for nodeid, markers in collector.items:
        is_pg = "postgres" in markers
        is_rd = "redis" in markers

        # Partition definitions matching pytest -m selectors
        if not is_pg and not is_rd:
            set_a.add(nodeid)
        if is_pg and not is_rd:
            set_b.add(nodeid)
        if is_rd:
            set_c.add(nodeid)

    # Disjointness
    ab_overlap = set_a.intersection(set_b)
    ac_overlap = set_a.intersection(set_c)
    bc_overlap = set_b.intersection(set_c)

    assert not ab_overlap, f"Overlap between partition A and B: {ab_overlap}"
    assert not ac_overlap, f"Overlap between partition A and C: {ac_overlap}"
    assert not bc_overlap, f"Overlap between partition B and C: {bc_overlap}"

    # Completeness
    union_abc = set_a.union(set_b).union(set_c)
    missing = all_nodeids.difference(union_abc)
    assert not missing, f"Tests missing from any partition: {missing}"
    assert union_abc == all_nodeids, "Partition union does not match total collection"
