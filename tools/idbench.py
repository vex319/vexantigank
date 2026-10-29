"""英雄身份识别基准测试。

用真实 Data Dragon 头像渲染成小地图红点，检验"从 19 px 头像认出是谁"的准确率。
不依赖游戏，也不依赖官方接口 —— 用来给候选参数（遮罩半径、模板尺寸）选出最优值。

    python tools/idbench.py
    python tools/idbench.py --trials 300 --cand 5
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from antigank.champions import ChampionBook  # noqa: E402
from antigank.config import load  # noqa: E402
from antigank.detect import RingDetector  # noqa: E402
from sim import S, frame_with_icons  # noqa: E402

CHAMP_DIR = ROOT / "assets" / "champions"


def _crop(frame, box):
    x0, y0, x1, y1 = box
    return frame[max(0, y0):y1, max(0, x0):x1]


def run(mask_frac: float, size: int, trials: int, cand: int,
        keys: list[str], det: RingDetector) -> tuple[float, list[int]]:
    book = ChampionBook(CHAMP_DIR, size=size, mask_frac=mask_frac)
    rng = random.Random(20261027)
    hit = 0
    miss_hit = [0, 0]   # [命中数, 总数]，用于统计"多帧投票后"

    for _ in range(trials):
        target = rng.choice(keys)
        pool = [k for k in keys if k != target]
        others = rng.sample(pool, cand - 1)
        cx = rng.randrange(30, S - 30)
        cy = rng.randrange(30, S - 30)
        frame = frame_with_icons([(cx, cy, target)])
        found = det.detect(frame)
        if not found:
            continue
        f = found[0]
        ids = book.assign([_crop(frame, f.box)], others + [target])
        got = ids[0][0] if ids[0] else None
        if got == target:
            hit += 1
        miss_hit[1] += 1
    acc = hit / max(1, trials)
    return acc, miss_hit


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--cand", type=int, default=5, help="候选英雄数（= 一局敌方人数）")
    args = ap.parse_args()

    cfg = load(str(ROOT / "config.json"))
    det = RingDetector(cfg["detect"])
    keys = sorted(p.stem for p in CHAMP_DIR.glob("*.png"))
    if not keys:
        print("没有头像，先跑 tools/fetch_champions.py")
        return
    print(f"可用英雄 {len(keys)} 个 · 每轮 {args.trials} 次 · 候选 {args.cand} 人\n")

    best = (0.0, 0.40, 26)
    for size in (22, 26, 30):
        for frac in (0.30, 0.34, 0.38, 0.42, 0.46):
            acc, _ = run(frac, size, args.trials, args.cand, keys, det)
            mark = ""
            if acc > best[0]:
                best = (acc, frac, size)
                mark = "  ←"
            print(f"  模板 {size:2d}px  遮罩 {frac:.2f}   单帧 top-1 {acc:6.1%}{mark}")

    print(f"\n最优：模板 {best[2]}px，遮罩 {best[1]:.2f}，单帧准确率 {best[0]:.1%}")
    print("实际使用中每条轨迹会投票十几帧，稳定性远高于这个单帧数字。")


if __name__ == "__main__":
    main()
