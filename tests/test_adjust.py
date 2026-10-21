from datetime import datetime

from footy import adjust, config, journal, learn


def _ctx(**home):
    return {"home_name": "A", "away_name": "B", "home": home, "away": {"rest_days": 6}, "weather": {"bad": ["降雨"]}}


def test_deltas_move_points_from_the_weakened_side():
    d = adjust.deltas(_ctx(rest_days=2.5, next_days=3, af={"key_out": [1, 2]}))
    # tired 2 + rotation 1.5 + two extra absences 2 = 5.5 points off the home win, split to draw and away
    assert abs(d["H"] + 0.055) < 1e-9 and abs(d["D"] - 0.0275) < 1e-9 and abs(d["A"] - 0.0275) < 1e-9
    assert d["O25"] == -0.02
    p = adjust.apply({"H": 0.5, "D": 0.25, "A": 0.25, "O25": 0.55, "scores": {}}, d, weight=1.0)
    assert abs(sum(p[k] for k in "HDA") - 1) < 1e-9 and p["H"] < 0.5 and "scores" in p
    assert abs(p["U25"] - 0.47) < 1e-9
    assert adjust.apply({"H": 0.5, "D": 0.25, "A": 0.25}, d, weight=0.0)["H"] == 0.5


def test_evening_reprices_and_flags_changed_picks(monkeypatch):
    monkeypatch.setattr(config, "CTX_WEIGHT", 1.0)
    odds = {"avg_h": 2.0, "avg_d": 3.6, "avg_a": 4.2, "max_h": 2.1, "max_d": 3.8, "max_a": 4.4,
            "avg_over": None, "avg_under": None, "max_over": None, "max_under": None}
    p = {"league": "E0", "bj_date": "2026-10-21", "home": "A", "away": "B", "odds": odds,
         "model": {"H": 0.52, "D": 0.25, "A": 0.23}, "bets": [], "longshots": []}
    pid = "E0|2026-10-21|A|B"
    earlier = {pid: {"versions": {"am": {"picks": [{"selection": "H"}]}}}}
    n = adjust.evening([p], {pid: _ctx(rest_days=2.5, next_days=3, af={"key_out": [1, 2, 3]})}, earlier)
    assert p["model_raw"]["H"] == 0.52 and p["model"]["H"] < 0.52
    assert p["evening"]["am_picks"] == ["H"] and n == p["evening"]["changed"]


def test_versions_and_weeks_in_review():
    recs = []
    base = {"league": "E0", "league_name": "英超", "date": "2026-10-19", "home": "A", "away": "B",
            "bj_date": "2026-10-19", "bj_time": "22:00", "market": {"H": 0.5, "D": 0.25, "A": 0.25}, "longshots": []}
    journal.record(recs, [{**base, "model": {"H": 0.6, "D": 0.2, "A": 0.2}, "bets": [{"selection": "H", "odds": 2.1, "edge": 0.1}]}],
                   datetime(2026, 10, 19, 11), "am")
    journal.record(recs, [{**base, "model": {"H": 0.3, "D": 0.3, "A": 0.4}, "model_raw": {"H": 0.6, "D": 0.2, "A": 0.2},
                           "ctx_adj": {"H": -0.05, "D": 0.025, "A": 0.025, "O25": 0}, "bets": []}],
                   datetime(2026, 10, 19, 19), "pm")
    r = recs[0]
    assert set(r["versions"]) == {"am", "pm"} and r["call"] == "A" and r["model_raw"]["H"] == 0.6
    r["result"] = {"hg": 2, "ag": 0}
    rv = journal.build_review(recs, [], datetime(2026, 10, 27, 11))
    w = rv["weeks"][0]
    assert w["start"] == "2026-10-19" and w["done"] and w["versions"]["changed"] == 1
    assert w["versions"]["am"]["call_hit"] == 1 and w["versions"]["pm"]["call_hit"] == 0
    assert abs(w["versions"]["am"]["picks"]["profit"] - 1.1) < 1e-9 and w["versions"]["pm"]["picks"]["n"] == 0
    rows = learn._ctx_rows(recs)
    assert rows and rows[0]["res"] == "H"
    s0, n = learn.score("CTX_WEIGHT", [], [], rows)
    with learn._patched(CTX_WEIGHT=0.0):
        s_off, _ = learn.score("CTX_WEIGHT", [], [], rows)
    assert n == 1 and s_off > s0  # the shift was wrong here, so switching it off scores better
