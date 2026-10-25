"""Check of the port from ent0n29/polybot (MIT, (c) 2024-2025 Polybot Contributors): the nearest opposite leg and the pair "lock"."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pmx.pairs import complete_sets, imbalance_frac, nearest_opposite_delta_seconds, slug_window  # noqa: E402


def test_nearest_opposite():
    d = nearest_opposite_delta_seconds(np.array([10, 100, 300]), np.array([12, 50, 200]))
    assert list(d) == [2, -50, -100]
    assert nearest_opposite_delta_seconds(np.array([]), np.array([1])).size == 0
    assert imbalance_frac(30, 10) == 0.5


def test_complete_set_lock():
    t = pd.DataFrame([{"condition_id": "m", "outcome_index": 0, "side": "BUY", "size": 100, "price": 0.48},
                      {"condition_id": "m", "outcome_index": 1, "side": "BUY", "size": 100, "price": 0.49}])
    cs = complete_sets(t)
    assert cs["markets"] == 1 and abs(cs["avg_edge_per_share"] - 0.03) < 1e-9 and cs["total_edge_pnl"] == 3


def test_slug_window():
    assert slug_window("btc-updown-15m-1792800000") == ("btc-updown-15m", 1792800000, 1792800900)
    assert slug_window("will-it-rain") == (None, None, None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
