from datetime import datetime

from footy import config, journal, report


def test_render_with_evening_update_and_weekly_review(tmp_path):
    recs = []
    base = {"league": "E0", "league_name": "英超", "date": "2026-10-19", "home": "A", "away": "B",
            "bj_date": "2026-10-19", "bj_time": "22:00", "market": {"H": 0.5, "D": 0.25, "A": 0.25}, "longshots": [],
            "model": {"H": 0.6, "D": 0.2, "A": 0.2}, "bets": [{"selection": "H", "odds": 2.1, "edge": 0.1}]}
    journal.record(recs, [base], datetime(2026, 10, 19, 11), "am")
    journal.record(recs, [base], datetime(2026, 10, 19, 19), "pm")
    recs[0]["result"] = {"hg": 0, "ag": 1}
    review = journal.build_review(recs, [], datetime(2026, 10, 27, 11))
    game = {**base, "bj_date": "2026-10-28", "odds_source": "test", "elo_home": 1500, "elo_away": 1500,
            "model": {"H": 0.55, "D": 0.24, "A": 0.21, "O25": 0.5, "scores": {"top": [[1, 0, 0.1]]}},
            "market": {"H": 0.5, "D": 0.25, "A": 0.25, "O25": 0.5},
            "bets": [{"selection": "H", "label": "主胜", "odds": 2.1, "prob": 0.55, "market_prob": 0.5, "edge": 0.1, "stake": 0.01}],
            "evening": {"shift": {"H": -0.02, "D": 0.01, "A": 0.01}, "am_picks": ["A"], "changed": True}}
    out = report.render([game], {}, {"days_ahead": 10, "live_sources": {}, "session": "pm", "updated_bj": "2026-10-27 19:05"},
                        site_dir=tmp_path, review=review)
    html = out.read_text(encoding="utf-8")
    assert "19:00 场外因素更新版" in html and "推荐有变" in html and "本周复盘" in html and "11:00 版 vs 19:00 版" in html
