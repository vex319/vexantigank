"""语音链路的回归测试（不需要开游戏、不需要真的出声）。

这里盯的是一类很难查的故障：**控制台一直在"播报"，耳机里一声没有**。
根因有两条，以前都被静默吞掉了：

1. `eng.stop()` 之后下一次 `pyttsx3.init()` 永远不返回；
2. pyttsx3 在进程里第二个线程调用时**不报错**，直接返回失败（文件根本
   不生成），于是播放线程整场哑着。

所以用例 ②③ 必须覆盖"多线程同时要合成"和"连续多次合成"。

    python tools/test_voice.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from antigank.voice import Voice, wav_seconds  # noqa: E402

PROBE = ROOT / "assets" / "voice" / "_test"
LINE_MAX = 1.95


def clean() -> None:
    PROBE.mkdir(parents=True, exist_ok=True)
    for p in PROBE.glob("*.wav"):
        try:
            p.unlink()
        except OSError:
            pass


def case_cached() -> bool:
    print("① 已生成的句子直接命中缓存，不改写文件")
    vc = Voice(PROBE, enabled=False, line_max=LINE_MAX)
    text = "敌方测试命中缓存"
    p1 = vc.line_path(text, "cache", max_sec=LINE_MAX)
    if not p1:
        print("  合成失败\n  → 失败\n")
        return False
    mtime = p1.stat().st_mtime
    synth_before = vc.synthesized
    p2 = vc.line_path(text, "cache", max_sec=LINE_MAX)
    print(f"  第一次 {p1.name} {wav_seconds(p1):.2f}s")
    print(f"  第二次 {p2.name if p2 else None} 合成次数 {synth_before} → {vc.synthesized}")
    ok = p2 == p1 and vc.synthesized == synth_before and p2.stat().st_mtime == mtime
    print(f"  → {'通过' if ok else '失败'}：没重复合成\n")
    return ok


def case_threads() -> bool:
    print("② 三个线程同时要合成：一句都不能丢")
    vc = Voice(PROBE, enabled=False, line_max=LINE_MAX)
    texts = ["敌方盲僧出现在我方下半野区",
             "敌方九尾妖狐从上路消失",
             "敌方暗裔剑魔出现在河道",
             "敌方德玛西亚皇子出现在敌方上半野区"]
    res: list[tuple[str, bool, float]] = []

    def worker(tag: str) -> None:
        for i, t in enumerate(texts):
            p = vc.line_path(t, f"{tag}{i}", max_sec=LINE_MAX)
            res.append((f"{tag}{i}", bool(p), wav_seconds(p) if p else -1.0))

    ths = [threading.Thread(target=worker, args=(f"T{k}",)) for k in range(3)]
    t0 = time.perf_counter()
    [t.start() for t in ths]
    [t.join(120) for t in ths]
    dt = time.perf_counter() - t0
    bad = [r for r in res if not r[1]]
    print(f"  3 线程 × 4 句，{dt:.1f}s 完成 {len(res) - len(bad)}/{len(res)}")
    for r in bad[:3]:
        print(f"    失败 {r[0]}")
    ok = not bad and len(res) == 12
    print(f"  → {'通过' if ok else '失败'}：没有线程被 SAPI 卡死\n")
    return ok


def case_sequential() -> bool:
    print("③ 同一线程连做 6 次：验证不再有 stop() 造成的死锁")
    vc = Voice(PROBE, enabled=False, line_max=LINE_MAX)
    ok_n = 0
    for i in range(6):
        p = vc.line_path(f"敌方连续测试第{i}句", f"seq{i}", max_sec=LINE_MAX)
        if p:
            ok_n += 1
    print(f"  成功 {ok_n}/6")
    ok = ok_n == 6
    print(f"  → {'通过' if ok else '失败'}：第 7 次以后也能继续合成\n")
    return ok


def case_length() -> bool:
    print("④ 长句子自动提速，卡在 1.95 秒内")
    vc = Voice(PROBE, enabled=False, line_max=LINE_MAX)
    long_text = "敌方德玛西亚皇子出现在敌方上半野区"
    p = vc.line_path(long_text, "long", max_sec=LINE_MAX)
    d = wav_seconds(p) if p else -1
    print(f"  「{long_text}」→ {d:.2f}s（上限 {LINE_MAX}）")
    ok = bool(p) and 0 < d <= LINE_MAX
    print(f"  → {'通过' if ok else '失败'}\n")
    return ok


def case_prepare() -> bool:
    print("⑤ vc.prepare 补齐缺失短语，已存在的跳过")
    vc = Voice(PROBE, enabled=False)
    items = [("敌方测试甲", PROBE / "ph_a.wav"),
             ("敌方测试乙", PROBE / "ph_b.wav")]
    made1 = vc.prepare(items)
    made2 = vc.prepare(items)
    print(f"  第一次补 {made1} 条，第二次补 {made2} 条（应为 0）")
    ok = made1 == 2 and made2 == 0 and all(p.exists() for _, p in items)
    print(f"  → {'通过' if ok else '失败'}\n")
    return ok


def main() -> None:
    clean()
    results = [case_cached(), case_threads(), case_sequential(),
               case_length(), case_prepare()]
    clean()
    n = sum(1 for r in results if r)
    print(f"合计 {n}/{len(results)} 通过")
    raise SystemExit(0 if n == len(results) else 1)


if __name__ == "__main__":
    main()
