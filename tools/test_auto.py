"""验证 --auto 的状态机：空闲判定、开局接管、对局结束。

不开游戏也能测：用假的对局接口喂不同的返回序列，看状态机是否按预期切换。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

import main as M
from antigank.config import load

M.IDLE_POLL = 0.01          # 别让测试真等 1.5 秒


class FakeLive:
    """按预设序列返回 probe()/snapshot() 的结果，序列用完就重复最后一个。"""

    def __init__(self, probes=None, snaps=None):
        self.probes = list(probes or [])
        self.snaps = list(snaps or [])
        self.n_probe = 0
        self.n_snap = 0

    def probe(self):
        self.n_probe += 1
        if not self.probes:
            return None
        return self.probes.pop(0) if len(self.probes) > 1 else self.probes[0]

    def snapshot(self):
        self.n_snap += 1
        if not self.snaps:
            return None
        return self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0]


class FakeCap:
    backend_name = "fake"
    grab_ms = 0.0

    def __init__(self):
        self.frames = 0

    def grab(self):
        self.frames += 1
        return np.zeros((280, 280, 3), np.uint8)

    def close(self):
        pass


class FakeDet:
    def detect(self, frame):
        return []


class FakeBook:
    index = {"Ahri": {}, "LeeSin": {}}

    def name(self, key):
        return key or "?"

    def speak(self, key):
        return key or "英雄"

    def resolve(self, raw):
        return raw or ""

    def assign(self, crops, cands):
        return [None] * len(crops)

    def score_matrix(self, patches, cands):
        return [[(c, 0.0) for c in cands] for _ in patches]

    def assign_from(self, matrix, min_score=0.3):
        return [None] * len(matrix)


class FakeVoice:
    def __init__(self):
        self.reset_calls = 0
        self.said = []
        self.lines = []

    def reset(self):
        self.reset_calls += 1

    def say_seq(self, seq, cooldown_key=None):
        self.said.append(seq)

    def say_line(self, text, key=""):
        self.lines.append(text)

    def warmup(self, items, on_done=None):
        # 测试里不合成语音，直接记下"这一局会说哪些句子"
        self.prepared = list(items or [])
        return None


def load_cfg():
    cfg = load(str(ROOT / "config.json"))
    cfg["capture"]["fps"] = 500        # 让单帧循环几乎不睡
    cfg["debug"]["window"] = False
    cfg["voice"]["enabled"] = False
    return cfg


def test_wait_game() -> bool:
    """加载界面(gameTime=0)不能当成开局。"""
    live = FakeLive(probes=[
        None,                                              # 客户端还没起
        {"game_time": 0.0, "enemies": []},                  # 接口通了，还在加载
        {"game_time": -3.0, "enemies": []},                 # 选英雄阶段
        {"game_time": 4.0, "enemies": [{"champion": "Ahri"}]},
    ])
    snap = M._wait_game(live)
    ok = snap is not None and snap["game_time"] == 4.0 and live.n_probe == 4
    print(f"  等待开局：probe {live.n_probe} 次后接管，gameTime={snap['game_time']}  "
          f"{'✓' if ok else '✗'}")
    return ok


def test_session_end() -> bool:
    """接口连续断开 3 次 → 判定本局结束。"""
    cfg = load_cfg()
    live = FakeLive(snaps=[{"game_time": 30.0, "my_side": "ORDER",
                            "enemies": [{"champion": "Ahri"}]}])
    cap, vc = FakeCap(), FakeVoice()
    # 前 2 帧正常，之后一直 None
    calls = {"n": 0}
    real_snap = live.snapshot

    def snap():
        calls["n"] += 1
        return real_snap() if calls["n"] <= 2 else None

    live.snapshot = snap  # type: ignore[method-assign]
    res = M._session(cfg, cap, FakeDet(), FakeBook(), vc, live, False, False, set())
    ok = res == "end" and calls["n"] == 5 and vc.reset_calls == 1
    print(f"  对局结束：返回 {res}，snapshot 调用 {calls['n']} 次"
          f"（2 正常 + 3 次 miss）  {'✓' if ok else '✗'}")
    return ok


def test_session_survives_short_drop() -> bool:
    """偶发超时不能把一局切断：间隔 2 次 miss 后又恢复 → 继续跑。"""
    cfg = load_cfg()
    live = FakeLive()
    seq = [None, None,
           {"game_time": 60.0, "my_side": "ORDER", "enemies": [{"champion": "Ahri"}]},
           None, None,
           {"game_time": 62.0, "my_side": "ORDER", "enemies": [{"champion": "Ahri"}]}]
    live.snapshot = lambda: seq.pop(0) if seq else {"game_time": 63.0,
                                                    "enemies": [{"champion": "Ahri"}]}

    cap = FakeCap()
    # 跑 40 帧后人为叫停
    stop_after = {"n": 40}
    real_grab = cap.grab

    class Stop(Exception):
        pass

    def grab():
        if stop_after["n"] <= 0:
            raise Stop
        stop_after["n"] -= 1
        return real_grab()

    cap.grab = grab  # type: ignore[method-assign]
    try:
        M._session(cfg, cap, FakeDet(), FakeBook(), FakeVoice(), live, False, False, set())
        res = "end"
    except Stop:
        res = "alive"
    ok = res == "alive"
    print(f"  偶发断连：跑到第 40 帧仍在运行（{res}）  {'✓' if ok else '✗'}")
    return ok


def test_no_live_session() -> bool:
    """live 关掉时 session 不会因为 None 崩掉。"""
    cfg = load_cfg()
    cap = FakeCap()
    n = {"v": 30}

    class Stop(Exception):
        pass

    real = cap.grab

    def grab():
        if n["v"] <= 0:
            raise Stop
        n["v"] -= 1
        return real()

    cap.grab = grab  # type: ignore[method-assign]
    try:
        M._session(cfg, cap, FakeDet(), FakeBook(), FakeVoice(), None, False, False, set())
        ok = False
        tag = "异常返回"
    except Stop:
        ok, tag = True, "正常跑到第 30 帧"
    print(f"  无对局接口：{tag}  {'✓' if ok else '✗'}")
    return ok


def main() -> int:
    print("自动模式状态机测试")
    results = [
        test_wait_game(),
        test_session_end(),
        test_session_survives_short_drop(),
        test_no_live_session(),
    ]
    bad = results.count(False)
    print(f"\n{len(results) - bad}/{len(results)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
