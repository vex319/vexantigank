"""端到端验证：检测 → 识别身份 → 跟踪 → 行为边沿 → 4 秒批播。

用**真实英雄头像**渲染合成小地图，跑完整条链路（不是拿假数据直接喂引擎），
所以能同时验证"改坏的检测/识别"和"改坏的行为规则"。

    1. 上路英雄走进上路野区       → "敌方X从上路消失"
    2. 辅助从下路走进河道         → "敌方X出现在河道"
    3. 打野在上半野区露头         → "敌方X出现在…上半野区"
    4. 中路连续 10 秒不见人       → "敌方X消失"

    python tools/e2e.py       （或 python main.py e2e）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from antigank import config as cfgmod  # noqa: E402
from antigank.batch import BatchScheduler  # noqa: E402
from antigank.behaviour import BehaviourEngine  # noqa: E402
from antigank.champions import ChampionBook  # noqa: E402
from antigank.detect import RingDetector  # noqa: E402
from antigank.roles import auto_assign  # noqa: E402
from antigank.tracker import Tracker  # noqa: E402
from sim import S, frame_with_icons  # noqa: E402

FPS = 4.0
DT = 1.0 / FPS
CHAMP_DIR = ROOT / "assets" / "champions"
ROSTER = ["Aatrox", "LeeSin", "Ahri", "Jinx", "Thresh"]


def _interp(keys, t):
    """keys = [(t, nx, ny), ...]；nx 为 None 表示从这一刻起看不见了。"""
    for i in range(len(keys) - 1):
        t0, x0, y0 = keys[i]
        t1, x1, y1 = keys[i + 1]
        if x1 is None:
            if t >= t1:
                return None
            if t <= t0:
                return x0, y0
            continue
        if t <= t1:
            k = 0.0 if t1 <= t0 else max(0.0, min(1.0, (t - t0) / (t1 - t0)))
            return x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
    last = keys[-1]
    return None if last[1] is None else (last[1], last[2])


def simulate(book, cfg, path, seconds, warmup=1.0):
    """只演一个英雄，但候选名单给全 5 个 —— 识别难度才是真实的。"""
    from main import _identity

    roles = auto_assign(ROSTER)
    det = RingDetector(cfg["detect"])
    trk = Tracker(cfg["track"])
    beh = BehaviourEngine({"warmup": warmup}, namer=book.speak)
    batch = BatchScheduler(period=4.0, max_items=2)

    champion = list(path)[0]
    events, seen = [], 0
    for i in range(int(seconds * FPS)):
        now = i * DT
        pt = _interp(path[champion], now)
        frame = (frame_with_icons([]) if pt is None
                 else frame_with_icons([(int(pt[0] * S), int(pt[1] * S), champion)]))
        found = det.detect(frame)
        seen += len(found)
        tracks = trk.update(found, now)
        _identity(frame, found, tracks, book, ROSTER)
        trk.reconcile(ROSTER, now)
        batch.submit(beh.update(trk.confirmed(now), roles, now))
        for ev in batch.due(now):
            events.append(ev)
    return events, seen


def case(name, path, seconds, want_action, want_text=None):
    book = ChampionBook(CHAMP_DIR)
    cfg = cfgmod.load(ROOT / "config.json")
    events, seen = simulate(book, cfg, path, seconds)
    print(f"\n=== {name} ===")
    print(f"  检出 {seen} 次红圈")
    for e in events:
        print(f"  +{e.t:5.1f}s  {e.text}")
    ok = any(e.action == want_action and (want_text is None or want_text in e.text)
             for e in events)
    if not ok:
        print(f"  期望出现 {want_action}，实际没有")
    print(f"  → {'通过' if ok else '失败'}")
    return ok


def main() -> None:
    res = [
        case("① 上路英雄走进上路野区",
             {"Aatrox": [(0, 0.12, 0.55), (7, 0.12, 0.55), (9, 0.36, 0.20)]},
             20, "off_top", "从上路消失"),
        case("② 辅助从下路走进河道",
             {"Thresh": [(0, 0.84, 0.86), (6, 0.84, 0.86), (9, 0.42, 0.44)]},
             20, "z_river", "出现在河道"),
        case("③ 打野在上半野区露头",
             {"LeeSin": [(0, 0.38, 0.18), (12, 0.40, 0.20)]},
             14, "z_jg_red_top", "野区"),
        case("④ 中路连续 10 秒不见人",
             {"Ahri": [(0, 0.5, 0.5), (5, 0.5, 0.5), (5.2, None, None)]},
             22, "vanish", "消失"),
    ]
    n = sum(1 for r in res if r)
    print(f"\n=== {n}/{len(res)} 通过")
    if n != len(res):
        sys.exit(1)


if __name__ == "__main__":
    main()
