"""行为播报的回归测试（方案.txt 的每一条）。

    python tools/test_behaviour.py

测的都是"边沿"这类最容易写错的地方：要报一次，不能每帧都报；
要等状态稳定，不能贴边抖一下就报；批播每 4 秒最多两条。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from antigank.batch import BatchScheduler              # noqa: E402
from antigank.behaviour import BehaviourEngine, Event  # noqa: E402
from antigank.roles import auto_assign, merge_assign, swap  # noqa: E402
from antigank.tracker import Track                    # noqa: E402
from antigank.voice import Voice, wav_seconds         # noqa: E402

ROLES = {"Aatrox": "top", "LeeSin": "jungle", "Ahri": "mid",
         "Jinx": "adc", "Thresh": "support"}
NAMES = {"Aatrox": "暗裔剑魔", "LeeSin": "盲僧", "Ahri": "九尾妖狐",
         "Jinx": "暴走萝莉", "Thresh": "魂锁典狱长"}


def namer(k):
    return NAMES.get(k, k)


def tr(tid, key, nx, ny, now, hits=4):
    return Track(tid=tid, nx=nx, ny=ny, t_first=now - 8, t_last=now, hits=hits, key=key)


def engine(warmup=1.0):
    return BehaviourEngine({"warmup": warmup, "visible_hold": 1.5,
                            "leave_hold": 3.0, "enter_hold": 2.0,
                            "zone_hold": 1.0, "mid_vanish": 10.0, "cd": 25.0},
                           namer=namer)


def run_seq(eng, script, roles=None, t0=0.0):
    """script = [(t, [(tid, key, x, y), ...]), ...]，返回所有事件。"""
    out = []
    for t, items in script:
        now = t0 + t
        tracks = [tr(i, k, x, y, now) for i, (k, x, y) in enumerate(items)]
        out.extend(eng.update(tracks, roles or ROLES, now))
    return out


def show(tag, evs):
    print(f"  {tag}")
    if not evs:
        print("    （无播报）")
    for e in evs:
        print(f"    +{e.t:5.1f}s  {e.text}")


def case_leave_and_return() -> bool:
    print("① 上路英雄离开上路 → 报一次；回到上路 → 再报一次")
    eng = engine()
    script = []
    for t in range(0, 6):                      # 0~5 秒在上路
        script.append((t, [("Aatrox", 0.12, 0.55)]))
    for t in range(6, 15):                     # 6 秒起走进上路野区
        script.append((t, [("Aatrox", 0.35, 0.20)]))
    for t in range(15, 22):                    # 15 秒回到上路
        script.append((t, [("Aatrox", 0.12, 0.55)]))
    evs = run_seq(eng, script)
    show("全过程", evs)
    off = [e for e in evs if e.action == "off_top"]
    on = [e for e in evs if e.action == "on_top"]
    ok = (len(off) == 1 and len(on) == 1
          and off[0].text == "敌方暗裔剑魔从上路消失"
          and on[0].text == "敌方暗裔剑魔出现在上路"
          and 8.9 <= off[0].t <= 9.1)
    print(f"  → {'通过' if ok else '失败'}：各报一次，离开判定用掉 3 秒防抖\n")
    return ok


def case_no_flicker() -> bool:
    print("② 在上路边线上蹭两下（离开 1 秒就回来）→ 不播")
    eng = engine()
    script = []
    for t in range(0, 6):
        script.append((t, [("Aatrox", 0.12, 0.55)]))
    script.append((6, [("Aatrox", 0.30, 0.25)]))   # 走开 1 秒
    script.append((7, [("Aatrox", 0.12, 0.55)]))   # 马上回来
    for t in range(8, 14):
        script.append((t, [("Aatrox", 0.12, 0.55)]))
    evs = run_seq(eng, script)
    show("全过程", evs)
    ok = not evs
    print(f"  → {'通过' if ok else '失败'}：抖动不产生播报\n")
    return ok


def case_jungle() -> bool:
    print("③ 打野进野区 / 河道 → 报；同一区域不重复报")
    eng = engine()
    script = []
    for t in range(0, 4):
        script.append((t, [("LeeSin", 0.35, 0.20)]))    # 红方上半野区
    for t in range(4, 8):
        script.append((t, [("LeeSin", 0.35, 0.20)]))    # 待着不动
    for t in range(8, 13):
        script.append((t, [("LeeSin", 0.40, 0.42)]))    # 河道
    evs = run_seq(eng, script)
    show("全过程", evs)
    zs = [e for e in evs if e.action.startswith("z_")]
    ok = (len(zs) == 2 and zs[0].action == "z_jg_red_top"
          and "野区" in zs[0].text and zs[1].action == "z_river")
    print(f"  → {'通过' if ok else '失败'}：进野区报一次，换区域再报一次\n")
    return ok


def case_lane_role_ignores_jungle() -> bool:
    print("④ 上路英雄进野区 → 只报\"从上路消失\"，不报野区位置")
    eng = engine()
    script = []
    for t in range(0, 6):
        script.append((t, [("Aatrox", 0.12, 0.55)]))
    for t in range(6, 12):
        script.append((t, [("Aatrox", 0.35, 0.20)]))
    evs = run_seq(eng, script)
    show("全过程", evs)
    ok = len(evs) == 1 and evs[0].action == "off_top"
    print(f"  → {'通过' if ok else '失败'}：角色决定报什么\n")
    return ok


def case_mid_vanish() -> bool:
    print("⑤ 中路连续 10 秒不见人 → 报\"消失\"")
    eng = engine()
    script = []
    for t in range(0, 4):                      # 0~3 秒在中路
        script.append((t, [("Ahri", 0.5, 0.5)]))
    for t in range(4, 22):                     # 之后完全看不到
        script.append((t, []))
    evs = run_seq(eng, script)
    show("全过程", evs)
    v = [e for e in evs if e.action == "vanish"]
    ok = len(v) == 1 and v[0].text == "敌方九尾妖狐消失" and 12.9 <= v[0].t <= 13.1
    print(f"  → {'通过' if ok else '失败'}：第 3 秒最后露面，第 13 秒（正好 10 秒）报消失\n")
    return ok


def case_batch() -> bool:
    print("⑥ 批播：2 秒一批，最多一条，同一英雄不占两个名额")
    b = BatchScheduler(period=2.0, max_items=1)
    b.reset(now=0.0)
    evs = [Event("Aatrox", "off_top", "A", 0.9, 1.0, role="top"),
           Event("Aatrox", "z_river", "A2", 0.4, 1.0, role="top"),
           Event("LeeSin", "z_jg_my_top", "L", 1.0, 2.0, role="jungle"),
           Event("Ahri", "vanish", "H", 0.55, 3.0, role="mid")]
    b.submit(evs)
    early = b.due(1.9)
    first = b.due(2.0)
    second = b.due(4.0)
    print(f"    1.9s → {len(early)} 条（还没到点）")
    print(f"    2.0s → {[e.text for e in first]}")
    print(f"    4.0s → {[e.text for e in second]}")
    ok = (not early and len(first) == 1 and first[0].key == "Ahri"
          and len(second) == 1 and second[0].key == "LeeSin")
    print(f"  → {'通过' if ok else '失败'}：按分路优先级，中路先播；一批只出一条\n")
    return ok


def case_priority() -> bool:
    print("⑦ 分路优先级可调整：改顺序就改播报先后")
    evs = [Event("Aatrox", "off_top", "上路", 0.9, 1.0, role="top"),
           Event("Jinx", "off_bot", "下路", 0.9, 1.0, role="adc"),
           Event("LeeSin", "z_jg_my_top", "打野", 1.0, 1.0, role="jungle")]

    b = BatchScheduler(period=2.0, max_items=1)
    b.reset(now=0.0)
    b.submit(evs)
    default_first = b.due(2.0)
    print(f"    默认 中>野>辅>下>上 → 先播 {default_first[0].text if default_first else None}")

    b2 = BatchScheduler(period=2.0, max_items=1,
                        priority=["top", "adc", "support", "mid", "jungle"])
    b2.reset(now=0.0)
    b2.submit(evs)
    top_first = b2.due(2.0)
    print(f"    改成 上>下>辅>中>野 → 先播 {top_first[0].text if top_first else None}")

    b3 = BatchScheduler(period=2.0, max_items=1, priority=["adc"])
    print(f"    只给一个角色也会自动补齐 → {b3.priority}")
    ok = (default_first and default_first[0].role == "jungle"
          and top_first and top_first[0].role == "top"
          and len(b3.priority) == 5)
    print(f"  → {'通过' if ok else '失败'}：优先级是真的起作用\n")
    return ok


def case_roles() -> bool:
    print("⑧ 角色自动分配与互换")
    roster = ["Aatrox", "LeeSin", "Ahri", "Jinx", "Thresh"]
    a = auto_assign(roster)
    print(f"    接口没给位置 → {a}")
    ok = sorted(a.values()) == sorted(ROLES.values())

    b = auto_assign(roster, {"Jinx": "BOTTOM", "Thresh": "UTILITY",
                             "LeeSin": "JUNGLE", "Ahri": "MIDDLE", "Aatrox": "TOP"})
    print(f"    接口给了位置 → {b}")
    ok = ok and b == ROLES

    swapped = swap(b, "Thresh", "top")
    print(f"    把辅助拖到上路 → {swapped}")
    ok = ok and swapped["Thresh"] == "top" and swapped["Aatrox"] == "support"
    ok = ok and sorted(swapped.values()) == sorted(ROLES.values())

    merged = merge_assign(roster, {"Thresh": "top"})
    print(f"    读回保存的角色 → {merged}")
    ok = ok and merged["Thresh"] == "top" and merged["Aatrox"] == "support"
    ok = ok and len(set(merged.values())) == 5
    print(f"  → {'通过' if ok else '失败'}：永远 5 个角色各一人\n")
    return ok


def case_voice_len() -> bool:
    print("⑨ 每条语音不超过 2 秒（最长名字 + 最长动作）")
    vc = Voice(ROOT / "assets" / "voice", enabled=False, line_max=1.95)
    samples = [
        ("敌方德玛西亚皇子出现在敌方上半野区", "JarvanIV__z_jg_their_top"),
        ("敌方魂锁典狱长出现在我方下半野区", "Thresh__z_jg_my_bot"),
        ("敌方暗裔剑魔从上路消失", "Aatrox__off_top"),
        ("敌方九尾妖狐消失", "Ahri__vanish"),
    ]
    ok = True
    for text, key in samples:
        p = vc.line_path(text, key)
        d = wav_seconds(p) if p else -1
        print(f"    {d:4.2f}s  {text}")
        ok = ok and 0 < d <= 1.95
    print(f"  → {'通过' if ok else '失败'}：整句合成，超长自动提速\n")
    return ok


def main() -> None:
    res = [case_leave_and_return(), case_no_flicker(), case_jungle(),
           case_lane_role_ignores_jungle(), case_mid_vanish(), case_batch(),
           case_priority(), case_roles(), case_voice_len()]
    n = sum(1 for r in res if r)
    print(f"=== {n}/{len(res)} 通过")
    if n != len(res):
        sys.exit(1)


if __name__ == "__main__":
    main()
