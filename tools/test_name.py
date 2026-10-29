"""验证"每条播报都念得出英雄名"这条硬要求。

覆盖过去会丢名字的几种情况：
  1. 名号早已认出            → 播报立刻带名字
  2. 刚出现、名字 1 秒后才解析 → 情报类播报等 identity_grace 秒后带名字播出
  3. 票数永远不够，但本局阵容已知 → 排除法补出剩下的那个名字
  4. 只有一帧中等相关度        → 用累计分最高者当名字，不空等
  5. 敌人短暂离开视野后重生    → 新轨迹继承旧轨迹的名字
  6. 已认出名号的英雄站着不动  → 不被"建筑图标"过滤器误删
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from antigank.config import load
from antigank.detect import Found
from antigank.threat import ThreatEngine
from antigank.tracker import Track, Tracker

CFG = load(str(ROOT / "config.json"))
NAMES = {"Viktor": "维克托", "Anivia": "艾尼维亚", "LeeSin": "李青"}
ROSTER = ["Viktor", "Anivia", "LeeSin", "Ahri", "Darius"]


def namer(k):
    return NAMES.get(k, k or "英雄")


def engine():
    return ThreatEngine(CFG["threat"], CFG["self"], namer=namer)


def show(tag, alerts):
    if not alerts:
        print(f"  {tag}  (无)")
    for a in alerts:
        print(f"  {tag}  {a.level:6s} seq={a.seq}  {a.text}")


def case_already_known() -> bool:
    print("① 名号已认出")
    eng, t = engine(), Track(tid=1, nx=0.62, ny=0.22, t_first=0, t_last=0)
    got = []
    for i in range(4):
        t.t_last, t.hits, t.key = float(i), i + 1, "Viktor"
        got += eng.evaluate([t], float(i))
    show("t=0..3s", got)
    ok = bool(got) and all(a.seq[0] == "foe_Viktor" for a in got)
    print(f"  → {'通过' if ok else '失败'}：整句以「敌方维克托」开头\n")
    return ok


def case_delayed() -> bool:
    print("② 刚出现，名字 1 秒后才解析出来")
    eng = engine()
    t = Track(tid=1, nx=0.62, ny=0.22, t_first=0, t_last=0)
    fired = []
    for i in range(5):
        now = float(i)
        t.t_last, t.hits = now, i + 1
        t.key = "Viktor" if now >= 1.0 else None     # 1 秒后才认出
        for a in eng.evaluate([t], now):
            fired.append((now, a))
            print(f"  t={now:.0f}s {a.level:6s} seq={a.seq}  {a.text}")
    ok = bool(fired) and all(a.seq[0] == "foe_Viktor" for _, a in fired)
    print(f"  → {'通过' if ok else '失败'}：等待期内没有抢先播无名情报\n")
    return ok


def case_roster_elimination() -> bool:
    print("③ 场上 5 个人，4 个已认出来 → 剩下那个只能是第 5 个英雄")
    eng, trk = engine(), Tracker(CFG["track"])
    fired = []
    for i in range(4):
        now = float(i)
        tracks = [Track(tid=j + 1, nx=0.3 + 0.1 * j, ny=0.7, t_first=0,
                        t_last=now, hits=2) for j in range(4)]
        unknown = Track(tid=9, nx=0.62, ny=0.22, t_first=0, t_last=now, hits=2)
        for tr, k in zip(tracks, ["Anivia", "LeeSin", "Ahri", "Darius"]):
            tr.key = k
        trk.tracks = tracks + [unknown]
        trk.reconcile(ROSTER, now)
        print(f"  t={now:.0f}s 排除法给出 {unknown.ident()}")
        for a in eng.evaluate([unknown], now):
            fired.append((now, a))
            print(f"         {a.level:6s} seq={a.seq}  {a.text}")
    ok = bool(fired) and fired[0][1].seq[0] == "foe_Viktor"
    print(f"  → {'通过' if ok else '失败'}：剩下一人就是它\n")
    return ok


def case_score_guess() -> bool:
    print("④ 只有一帧中等相关度 → 立刻用最高分当名字，不空等")
    eng = engine()
    t = Track(tid=2, nx=0.62, ny=0.22, t_first=0, t_last=0)
    fired = []
    for i in range(4):
        now = float(i)
        t.t_last, t.hits = now, i + 1
        t.add_scores([("Anivia", 0.62), ("Viktor", 0.40), ("LeeSin", 0.11)])
        for a in eng.evaluate([t], now):
            fired.append(a)
            print(f"  t={now:.0f}s {a.level:6s} seq={a.seq}  {a.text}")
    ok = bool(fired) and fired[0].seq[0] == "foe_Anivia"
    print(f"  → {'通过' if ok else '失败'}：0.6 的相关度也够念名字\n")
    return ok


def case_inherit() -> bool:
    print("⑤ 敌人走出视野 2 秒后重生，身份要继承")
    trk = Tracker(CFG["track"])
    d1 = Found(nx=0.62, ny=0.22, d=0.075, fill=0.2, conf=1.0,
               box=(120, 40, 148, 68))
    trk.update([d1], 0.0)[0].add_identity("Viktor", 0.95)
    old = trk.tracks[0]
    old.t_last = 0.0
    old.key = "Viktor"
    print(f"  旧轨迹 tid={old.tid} key={old.key}")
    # 2.5 秒后同一位置重新出现（1 Hz 下漏帧 / 进草的典型情况）
    # 挪到超出匹配门限（gate=0.055）但仍在继承半径（2×gate）内
    d2 = Found(nx=0.56, ny=0.29, d=0.075, fill=0.2, conf=1.0,
               box=(150, 50, 178, 78))
    trk.tracks = [old]                      # 旧轨迹还没被 lost_timeout 清掉
    new = [t for t in trk.update([d2], 2.5) if t.tid != old.tid]
    born = new[0] if new else None
    print(f"  新轨迹 tid={getattr(born, 'tid', '?')} key={getattr(born, 'key', '?')}")
    ok = born is not None and born.key == "Viktor"
    print(f"  → {'通过' if ok else '失败'}：重生后名字没丢\n")
    return ok


def case_static_named() -> bool:
    print("⑥ 已认出名号的英雄站着不动，不能被当成建筑丢掉")
    trk = Tracker(CFG["track"])
    f = Found(nx=0.62, ny=0.22, d=0.075, fill=0.2, conf=1.0, box=(120, 40, 148, 68))
    for i in range(8):
        trk.update([f], float(i))
    trk.tracks[0].add_identity("Viktor", 0.95)
    alive = trk.confirmed(8.0)
    print(f"  静止 8 秒后仍在跟踪：{len(alive)} 条，key={alive[0].key if alive else '-'}")
    ok = bool(alive)
    print(f"  → {'通过' if ok else '失败'}\n")
    return ok


def case_resolve() -> bool:
    print("⑦ 国服对局接口给中文名（齐天大圣）→ 归一成英文 key")
    from antigank.champions import ChampionBook

    book = ChampionBook(ROOT / "assets" / "champions")
    pairs = [("齐天大圣", "MonkeyKing"), ("异画师", "Hwei"), ("大发明家", "Heimerdinger"),
             ("黑默丁格", "Heimerdinger"), ("盲僧", "LeeSin"), ("Ahri", "Ahri")]
    ok = True
    for raw, want in pairs:
        got = book.resolve(raw)
        print(f"  {raw} → {got}（期望 {want}）")
        ok = ok and got == want
    bad = [k for k, e in book.index.items()
           if book.resolve(e.get("epithet", "")) != k or book.resolve(e.get("name", "")) != k]
    print(f"  173 个英雄全量反查：{'无冲突' if not bad else f'冲突 {bad}'}")
    ok = ok and not bad
    print(f"  → {'通过' if ok else '失败'}：中文名能对上头像模板\n")
    return ok


def main() -> None:
    res = [case_already_known(), case_delayed(), case_roster_elimination(),
           case_score_guess(), case_inherit(), case_static_named(), case_resolve()]
    print(f"结果 {sum(res)}/{len(res)} 通过")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
