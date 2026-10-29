"""The copy verdict and the watch diff, on hand-made analysis.json / copysim.json."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pmx.scout import changes, record, verdict  # noqa: E402


def wallet(tmp_path, rois, shares=None, positions=120, level="reliable", to_end=600):
    shares = shares or {}
    a = {"passport": {"wallet": "0xabc", "name": "w"}, "reliability": {"level": level, "reasons": ["most profit comes from conversions"]},
         "bot": {"verdict": "bot", "style": {"label": "crypto Up/Down"}}, "categories": [{"category": "crypto"}],
         "timing_to_end": {"median_seconds_to_end": to_end}, "recipe": {"steps": [{"rule": "buys both sides"}]}}
    o = {m: {"roi": r, "copied_share": shares.get(m, 1.0), "positions": positions} for m, r in rois.items()}
    (tmp_path / "analysis.json").write_text(json.dumps(a))
    (tmp_path / "copysim.json").write_text(json.dumps({"overall": o}))
    return tmp_path


def test_no_when_a_minute_late_loses(tmp_path):
    v = verdict(wallet(tmp_path, {"his": .074, "m0": .01, "m1": -.121, "m5": -.14, "m15": -.3, "m60": None}))
    assert v["verdict"] == "no" and "one minute late" in v["line"] and "−12.1%" in v["line"]
    assert v["entry_before_end"] == "10 min"


def test_no_right_after(tmp_path):
    v = verdict(wallet(tmp_path, {"his": .074, "m0": -.072, "m1": -.121}))
    assert v["line"] == "no: a copy right after it already loses −7.2%"


def test_yes_names_the_longest_delay_with_half_the_money(tmp_path):
    rois = {"his": .108, "m0": .104, "m1": .102, "m5": .098, "m15": .085, "m60": .012}
    v = verdict(wallet(tmp_path, rois, shares={"m60": .3}, to_end=7 * 86400))
    assert v["line"] == "yes, up to 15 min late" and v["entry_before_end"] == "7 days"


def test_unclear_small_sample_and_unreliable(tmp_path):
    rois = {"his": .336, "m0": .334, "m1": .332, "m5": .329, "m15": .329, "m60": .326}
    assert verdict(wallet(tmp_path, rois, positions=21))["line"] == "unclear: only 21 resolved positions to replay, it could be luck"
    v = verdict(wallet(tmp_path, rois, level="unreliable"))
    assert v["verdict"] == "unclear" and "conversions" in v["line"]
    assert "nothing to replay" in verdict(wallet(tmp_path, {}, positions=0))["line"]
    assert verdict(wallet(tmp_path, rois, positions=1))["line"].startswith("unclear: only 1 resolved position to")


def test_thin_yes(tmp_path):
    rois = {"his": .012, "m0": .009, "m1": .006, "m5": .005, "m15": .004, "m60": .002}
    assert verdict(wallet(tmp_path, rois))["line"] == "yes, up to 60 min late, but thin: +0.6% a minute late"


def test_markets_closed(tmp_path):
    v = verdict(wallet(tmp_path, {"his": .2, "m0": .1, "m1": .05}, shares={"m1": .058}))
    assert v["line"] == "no: a minute later most of its markets have closed" and v["m1"] is None


def test_watch_reports_what_changed(tmp_path):
    d = wallet(tmp_path, {"his": .108, "m0": .104, "m1": .102, "m5": .098, "m15": .085, "m60": .05})
    first = verdict(d)
    assert record(d, first) is None and changes(None, first) == ["first check"]
    d = wallet(tmp_path, {"his": .02, "m0": .01, "m1": -.03})
    second = verdict(d)
    prev = record(d, second)
    c = changes(prev, second)
    assert any(x.startswith("verdict: yes") for x in c) and any("one minute late" in x for x in c)
    assert len((d / "verdicts.jsonl").read_text().splitlines()) == 2
    assert changes(json.loads((d / "verdicts.jsonl").read_text().splitlines()[-1]), second)[0].startswith("no change since")


def test_copy_score(tmp_path):
    full = {"his": .43, "m0": .40, "m1": .344, "m5": .3, "m15": .3, "m60": .25}
    assert verdict(wallet(tmp_path, full))["score"] == 100
    v = verdict(wallet(tmp_path, {"his": .108, "m0": .104, "m1": .102, "m5": .098, "m15": .085, "m60": .012}))
    assert v["score"] == round(100 * .102 / .25)                       # all four delays pay: the edge decides
    assert verdict(wallet(tmp_path, full, level="approximate"))["score"] == 80
    assert verdict(wallet(tmp_path, {"his": .074, "m0": .01, "m1": -.121}))["score"] == 0
    assert verdict(wallet(tmp_path, {}, positions=0))["score"] is None
    assert verdict(wallet(tmp_path, full, positions=15))["score"] == 50   # half the sample it needs
