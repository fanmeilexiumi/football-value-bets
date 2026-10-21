"""Evening update: let the off-pitch factors move the model's probabilities a little.

Each factor takes a few points off one side's win and hands them to the draw
and the other side (bad weather takes them off over 2.5 goals). The sizes
below are rules of thumb; CTX_WEIGHT scales them all and is re-chosen every
week from the journal (see learn.py), so it can fall to 0 if they do not help.
"""

from __future__ import annotations

from . import config
from .value import find_longshots, find_value

TIRED = 0.02  # three days' rest or less against an opponent with five or more
ROTATION = 0.015  # another game within three and a half days
MOTIVATION = 0.015  # less to play for than the opponent
ABSENCE = 0.01  # each starter out more than the opponent, up to three
ABSENCE_CAP = 3
WEATHER_GOALS = 0.02  # rain, strong wind, cold or heat: fewer goals


def deltas(c: dict | None) -> dict | None:
    """Probability shifts at CTX_WEIGHT = 1, per selection."""
    if not c:
        return None
    from .context import _side_flags

    d = {"H": 0.0, "D": 0.0, "A": 0.0, "O25": 0.0}
    out = {s: len(((c.get(s) or {}).get("af") or {}).get("key_out", [])) for s in ("home", "away")}
    for side, win, other in (("home", "H", "A"), ("away", "A", "H")):
        f = _side_flags(c, side)
        extra = min(ABSENCE_CAP, max(0, out[side] - out["away" if side == "home" else "home"]))
        loss = TIRED * f["tired"] + ROTATION * f["rotation"] + MOTIVATION * f["motivation"] + ABSENCE * extra
        d[win] -= loss
        d["D"] += loss / 2
        d[other] += loss / 2
    if (c.get("weather") or {}).get("bad"):
        d["O25"] -= WEATHER_GOALS
    return {k: round(v, 4) for k, v in d.items()}


def apply(model: dict, d: dict | None, weight: float | None = None) -> dict:
    """The model with the shifts added, renormalised; scores and other keys are kept."""
    w = config.CTX_WEIGHT if weight is None else weight
    if not d or not w:
        return dict(model)
    out = dict(model)
    one = {k: max(0.01, model[k] + w * d.get(k, 0.0)) for k in ("H", "D", "A")}
    s = sum(one.values())
    out.update({k: v / s for k, v in one.items()})
    if "O25" in model:
        out["O25"] = min(0.98, max(0.02, model["O25"] + w * d.get("O25", 0.0)))
        out["U25"] = 1 - out["O25"]
    return out


def evening(predictions: list[dict], ctx: dict[str, dict], earlier: dict[str, dict]) -> int:
    """Re-price games that have context; returns how many changed their picks against the morning version."""
    from .zucai import pred_id

    changed = 0
    for p in predictions:
        pid = pred_id(p)
        d = deltas(ctx.get(pid))
        p["ctx_adj"] = d
        am = ((earlier.get(pid) or {}).get("versions") or {}).get("am")
        if not p.get("model") or not d:
            continue
        p["model_raw"] = p["model"]
        p["model"] = apply(p["model"], d)
        p["bets"] = find_value(p["odds"], p["model"], price="max")
        p["longshots"] = find_longshots(p["odds"], p["model"], price="max")
        now = sorted({b["selection"] for b in p["bets"] + p["longshots"]})
        before = sorted({pk["selection"] for pk in am["picks"]}) if am else None
        p["evening"] = {"shift": {k: config.CTX_WEIGHT * v for k, v in d.items() if config.CTX_WEIGHT * v},
                       "am_picks": before, "changed": before is not None and before != now}
        changed += p["evening"]["changed"]
    return changed
