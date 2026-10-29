#!/usr/bin/env python
"""反 Gank 小地图哨兵。

    python main.py calibrate    校准小地图位置（点左上角 + 右下角两个点）
    python main.py roles        分配敌方 5 人的分路角色（上/野/中/下/辅）
    python main.py priority     调整多条播报待发时的先后顺序
    python main.py run          开始实时监测（手动启动，跑一局）
    python main.py run --auto   常驻待机，开局自动接管，一局结束自动等下一局
    python main.py run --debug  带预览窗口
    python main.py doctor       自检：采集速度 / 检出数量 / 英雄库 / 语音
    python main.py selftest     合成数据验证检测算法
    python main.py e2e          验证"从上路消失 / 出现在河道 / 野区露头 / 中路消失"
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from antigank import config as cfgmod
from antigank.batch import BatchScheduler
from antigank.behaviour import JUNGLE_ZONES, LANE_SAY, BehaviourEngine
from antigank.capture import Capture, dpi_aware, screen_size
from antigank.champions import ChampionBook
from antigank.detect import RingDetector, annotate
from antigank.geo import (describe_sides, mid_perp_dist, zone_key, zone_label,
                          zone_say)
from antigank.live import LiveClient
from antigank.roles import (LANE_OF, ROLE_SHORT, WATCH_JUNGLE, describe,
                            merge_assign)
from antigank.tracker import Tracker
from antigank.voice import Voice, wav_seconds

VOICE_DIR = ROOT / "assets" / "voice"
CHAMP_DIR = ROOT / "assets" / "champions"
LOG_PATH = ROOT / "antigank.log"


class _Tee:
    """同时写控制台和日志文件。

    双击 启动.bat 时窗口一闪而过、或者控制台输出被缓冲掉，日志就彻底丢了，
    只能看到最后一行 "exited"，完全不知道发生了什么。这里把每一行同时落盘，
    出问题直接看 antigank.log。

    逐行 flush 是刻意的：程序被 Ctrl+C / 强杀时缓冲区不会丢。
    """

    def __init__(self, console, path: Path):
        self._console = console
        self._fp = open(path, "w", encoding="utf-8", buffering=1)
        self.encoding = getattr(console, "encoding", None) or "utf-8"
        self.buffer = getattr(console, "buffer", None)

    def write(self, s):  # noqa: D401
        try:
            self._console.write(s)
        except Exception:
            pass                       # 控制台关了也要保证落盘
        try:
            self._fp.write(s)
        except Exception:
            pass
        return len(s)

    def flush(self) -> None:
        for f in (self._console, self._fp):
            try:
                f.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return self._console.isatty()
        except Exception:
            return False

    def fileno(self) -> int:
        return self._console.fileno()


def _install_log() -> None:
    try:
        sys.stdout = _Tee(sys.stdout, LOG_PATH)  # type: ignore[assignment]
        sys.stderr = sys.stdout                  # type: ignore[assignment]
    except Exception:
        pass                                     # 日志装不上不能影响主功能
    print(f"=== antigank {time.strftime('%Y-%m-%d %H:%M:%S')} · "
          f"Python {sys.version.split()[0]} · {sys.executable}")
    sys.stdout.flush()


def _crop(frame, box):
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return None
    return frame[max(0, y0):y1, max(0, x0):x1]


def _prebuild_names(book: ChampionBook, vc: Voice, keys: list[str]) -> None:
    """开局后把 5 个敌方英雄的名字补生成成 wav（约 3 秒，不影响报警）。

    两条都要：「敌方XXX」是正常播报用的整句，「XXX」是可拆开兜底的单名字。
    合成必须走 `vc.prepare`：pyttsx3 一个进程只认第一个用它的线程，
    这里自己开线程直接合成会把播放线程那一侧整个废掉（一句都播不出）。
    """
    items = []
    for key in keys:
        name = book.speak(key)
        items.append((f"敌方{name}", vc.dir / f"foe_{key}.wav"))
        items.append((name, vc.dir / f"champ_{key}.wav"))
    made = vc.prepare(items)
    if made:
        print(f"  [voice] 已补齐 {made} 条英雄名语音")


def _lines_for(book: ChampionBook, roster: list[str], roles: dict[str, str],
               my_side: str | None) -> list[tuple[str, str]]:
    """这一局可能要说到的所有句子，开局后台先合成好。

    每局 5 个英雄 × 各自的角色，最多二十来句，约 6 秒跑完；跑不完也没关系，
    真到了那一步运行时会现合成一句（多 0.3 秒延迟）。
    """
    out: list[tuple[str, str]] = []
    for key in roster:
        role = roles.get(key)
        if not role:
            continue
        name = book.speak(key)
        pre = f"敌方{name}"
        lane = LANE_OF.get(role)
        if lane:
            out.append((f"{pre}从{LANE_SAY[lane]}消失", f"{key}__off_{lane}"))
            out.append((f"{pre}出现在{LANE_SAY[lane]}", f"{key}__on_{lane}"))
        if role in WATCH_JUNGLE:
            for zk in sorted(JUNGLE_ZONES):
                out.append((f"{pre}出现在{zone_say(zk, my_side)}", f"{key}__z_{zk}"))
        if role == "mid":
            out.append((f"{pre}消失", f"{key}__vanish"))
    return out


LATCH_BONUS = 0.10      # 已确认身份的一点点加成，防止名字在两帧之间来回跳


def _identity(frame, found, tracks, book: ChampionBook, candidates: list[str]) -> None:
    if not book.index or not found or not candidates:
        return
    ready: list[tuple[int, object]] = []
    for i, f in enumerate(found):
        patch = _crop(frame, f.box)
        if patch is not None and patch.size:
            ready.append((i, patch))
    if not ready:
        return

    raw = book.score_matrix([p for _, p in ready], candidates)
    matrix = [list(row) for row in raw]
    pos_of = {det_i: pos for pos, (det_i, _) in enumerate(ready)}

    # 已经认出来的英雄在这帧里享受一点加成：同一个英雄只可能出现一次，
    # 身份一旦稳定就别再抖 —— 抖一次就是一次报错名字。
    for tr in tracks:
        pos = pos_of.get(tr.det_idx)
        if pos is None or not tr.key:
            continue
        row = matrix[pos]
        for j, (key, sc) in enumerate(row):
            if key == tr.key:
                row[j] = (key, sc + LATCH_BONUS)
                break
        row.sort(key=lambda kv: -kv[1])

    ids = book.assign_from(matrix)
    for tr in tracks:
        pos = pos_of.get(tr.det_idx)
        if pos is None:
            continue
        tr.add_scores(raw[pos])       # 全候选累计分：票不够也能叫出名字
        hit = ids[pos]
        if hit:
            tr.add_identity(hit[0], hit[1])


def cmd_calibrate(a: argparse.Namespace) -> None:
    from antigank.calibrate import run

    run(a.config, delay=float(getattr(a, "delay", 3.0)))


def cmd_pick(a: argparse.Namespace) -> None:
    from antigank.picker import pick

    pick(a.config, demo=bool(getattr(a, "demo", False)))


def cmd_roles(a: argparse.Namespace) -> None:
    from antigank.roles_ui import assign

    assign(a.config, demo=bool(getattr(a, "demo", False)))


def cmd_saytest(a: argparse.Namespace) -> None:
    """验证"能不能听见"：合成 3 句、逐句播出来。

    专门用来区分两类"没声音"：
      - 合成失败（日志会写"没合成出来"）
      - 合成好了但放不出来（系统静音 / 游戏独占音频设备）
    """
    cfg = cfgmod.load(a.config)
    vc = Voice(VOICE_DIR, True, 0, line_max=float(cfg["behaviour"]["line_max"]))
    if not vc.enabled:
        print("语音功能被关掉了：config.json 的 voice.enabled = false，或系统没有 winsound")
        return

    lines = [("敌方盲僧出现在我方下半野区", "saytest_jg"),
             ("敌方九尾妖狐从上路消失", "saytest_top"),
             ("敌方德玛西亚皇子出现在大龙坑", "saytest_baron")]
    print("准备语音（第一次要合成，约 1~2 秒）…")
    ready = []
    for text, key in lines:
        p = vc.line_path(text, key)
        if p is None:
            print(f"  ✗ 合成失败：{text}")
            continue
        ready.append((text, key))
        print(f"  ✓ {text}  {wav_seconds(p):.2f}s")

    if not ready:
        print("\n一条都没合成出来 —— 检查 pyttsx3 和中文语音包（Windows 设置 → 时间和语言 → 语音）")
        return

    print(f"\n开始播放 {len(ready)} 句，注意听。")
    t0 = time.perf_counter()
    for text, key in ready:
        print(f"  ▶ {text}")
        vc.say_line(text, key)
        time.sleep(max(2.2, wav_seconds(vc.line_path(text, key)) + 0.4))
    time.sleep(0.5)

    print()
    if vc.play_fail:
        print(f"有 {vc.play_fail} 次播放报错 —— 音频设备可能被游戏独占（把游戏改成"
              "窗口化/无边框，或在声音设置里关掉独占模式）")
    else:
        print("播放接口没报错（用时 "
              f"{time.perf_counter() - t0:.1f}s）。如果还是听不见，按顺序查：")
        print("  1. Windows 音量合成器里 python.exe / 控制台是否被静音或音量拉到 0")
        print("  2. 输出设备是不是你戴的那副（右下角小喇叭 → 选对设备）")
        print("  3. 游戏如果开的是「全屏独占」，改成无边框窗口再试")


def cmd_priority(a: argparse.Namespace) -> None:
    from antigank.priority_ui import adjust

    adjust(a.config)


def cmd_doctor(a: argparse.Namespace) -> None:
    dpi_aware()
    cfg = cfgmod.load(a.config)
    region = cfgmod.region_tuple(cfg)
    sw, sh = screen_size()
    print(f"屏幕          {sw} x {sh}")
    print(f"小地图区域    left={region[0]} top={region[1]} {region[2]}x{region[3]}")
    if region[0] == 0 and region[1] == 0:
        print("  ! 还没校准，先跑 python main.py calibrate")

    cap = Capture(region, cfg["capture"]["backend"])
    for _ in range(6):          # 丢掉前几帧（GDI/DIB 初始化远慢于稳态）
        frame = cap.grab()
    t0 = time.perf_counter()
    n = 0
    while time.perf_counter() - t0 < 1.2:
        frame = cap.grab()
        n += 1
    dt = time.perf_counter() - t0
    print(f"采集后端      {cap.backend_name}    {n / dt:6.1f} fps    {cap.grab_ms:.2f} ms/帧（稳态）")

    det = RingDetector(cfg["detect"])
    det.detect(frame)           # 首次调用要初始化 OpenCV 线程池，单独预热
    t0 = time.perf_counter()
    for _ in range(5):
        found = det.detect(frame)
    cost = (time.perf_counter() - t0) * 1000 / 5
    print(f"检测耗时      {cost:.2f} ms/帧（稳态）    检出红色目标 {len(found)} 个")

    book = ChampionBook(CHAMP_DIR, version=cfg["champions"].get("version", ""))
    print(f"英雄库        {len(book.index)} 个（本地头像 {len(list(CHAMP_DIR.glob('*.png')))} 张）")
    notify = cfg["champions"].get("notify") or []
    print(f"播报名单      {'全部' if not notify else '、'.join(book.speak(k) for k in notify)}")

    live = None
    if cfg["live"]["enabled"]:
        live = LiveClient(interval=cfg["live"]["interval"]).start()
        time.sleep(1.5)
    snap = live.snapshot() if live else None
    if snap:
        print(f"对局接口      已连接 · 游戏时间 {snap['game_time'] / 60:.0f} 分 · 我方 {snap['my_side']}")
        print(f"敌方阵容      {', '.join(book.speak(book.resolve(e['champion'])) for e in snap['enemies'])}")
        roster = [book.resolve(e["champion"]) for e in snap["enemies"] if e.get("champion")]
        positions = {book.resolve(e["champion"]): e.get("position", "")
                     for e in snap["enemies"] if e.get("champion")}
        rmap = merge_assign(roster, (cfg.get("roles") or {}).get("assign"), positions)
        print(f"分路角色      {describe(rmap, book.speak)}")
    else:
        print("对局接口      未连接（不在对局中）。将退回全英雄模板匹配。")

    if found:
        cands = ([book.resolve(k) for k in live.enemy_champions()] if live else []) \
            or list(book.index)
        ids = book.assign([_crop(frame, f.box) for f in found], cands)
        for f, hit in zip(found, ids):
            tag = f"{book.speak(hit[0])} {hit[1]:+.2f}" if hit else "识别失败"
            print(f"  ({f.nx:.3f},{f.ny:.3f}) {zone_label(zone_key(f.nx, f.ny)):12s} {tag}")

    import cv2

    cv2.imwrite(str(ROOT / "assets" / "doctor_frame.png"), annotate(frame, found))
    print("  已存 assets/doctor_frame.png")
    cap.close()
    if live:
        live.stop()

    vc = Voice(VOICE_DIR, cfg["voice"]["enabled"], 0)
    miss = vc.missing()
    print(f"语音          短语缺 {len(miss)} 条，英雄名 {len(list(VOICE_DIR.glob('champ_*.wav')))} 条")
    if miss:
        print("  跑 python tools/make_voice.py 生成")


def _render_debug(frame, found, tracks, now, beh: BehaviourEngine,
                  book: ChampionBook, roles: dict[str, str], batch=None):
    """预览窗口：红框 + 名字 + 角色。

    文字一律用 Pillow 画 —— cv2.putText 只认 ASCII，中文会变成一串问号，
    以前这里其实一直是"????"。
    """
    import numpy as np
    from PIL import Image, ImageDraw

    from antigank import textdraw

    img = annotate(frame, found, None)
    S = img.shape[0]
    pil = Image.fromarray(img[:, :, ::-1])
    d = ImageDraw.Draw(pil)

    for t in tracks:
        cx, cy = int(t.nx * S), int(t.ny * S)
        key = t.ident()
        name = book.speak(key) if key else "?"
        short = ROLE_SHORT.get(roles.get(key or "", ""), "")
        textdraw.text(d, (cx + 9, max(0, cy - 8)), f"{name}{short}", 13, (245, 247, 250))
        if not t.visible(now, 1.5):
            d.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), outline=(90, 90, 220), width=1)

    live_n = sum(1 for t in tracks if t.visible(now, 1.5))
    head = f"t={now:5.1f}s  视野 {live_n}/{len(tracks)}"
    if batch is not None:
        head += f"  待播 {batch.pending()}"
    textdraw.text(d, (8, 4), head, 13, (130, 235, 150))
    return np.array(pil)[:, :, ::-1].copy()


IDLE_POLL = 1.5      # 空闲待机时多久探一次对局接口
END_MISSES = 3       # 连续几次探不到就判定本局结束


def _wait_game(live: LiveClient) -> dict:
    """阻塞到游戏正式开始（gameTime > 0）。

    注意"接口连得上"不等于"开局了"：选英雄和加载界面 Live API 就已经能访问，
    此时 gameTime 还是 0 或负数。所以判据只能用 gameTime。
    """
    print("空闲待机中，等待对局开始…（Ctrl+C 退出）")
    loading = False
    ticks = 0
    while True:
        snap = live.probe()
        if LiveClient.in_game(snap):
            return snap  # type: ignore[return-value]
        ticks += 1
        if snap and not loading:
            loading = True
            print("  客户端已连上，等游戏正式开始…")
        elif not snap and loading:
            loading = False
            print("  客户端断开，继续等")
        elif ticks and ticks % 40 == 0 and not loading:
            # 约 1 分钟一次。国服/WeGame 客户端的 2999 端口行为和 Riot 客户端
            # 一致，但万一环境里压根没有这个接口，得让人知道该换手动模式。
            print(f"  ……仍在等待（{int(ticks * IDLE_POLL)}s）。"
                  f"若游戏已开始却一直连不上，可能是本机没有对局接口，"
                  f"改用 python main.py run 手动开始")
        time.sleep(IDLE_POLL)


def _session(cfg, cap, det, book: ChampionBook, vc: Voice, live: LiveClient | None,
             show: bool, verbose: bool, notify_cfg: set[str],
             cfg_path: str = "") -> str:
    """跑一局。返回 "quit"（用户关窗口）或 "end"（对局结束）。"""
    import cv2

    trk = Tracker(cfg["track"])
    bcfg = cfg.get("behaviour") or {}
    beh = BehaviourEngine(bcfg, namer=book.speak)
    priority = bcfg.get("priority") or None
    batch = BatchScheduler(bcfg.get("period", 2.0), bcfg.get("max_items", 1),
                           priority)
    vc.reset()
    notify = set(notify_cfg)
    saved_roles = (cfg.get("roles") or {}).get("assign") or {}
    roles: dict[str, str] = {}

    fps = max(1, int(cfg["capture"]["fps"]))
    period = 1.0 / fps

    t_origin = time.perf_counter()
    last_report = 0.0
    roster: list[str] = []
    name_job: threading.Thread | None = None
    miss = 0
    warned_roles = False

    # 没有对局接口时（或单次启动还没连上），退一步用上一局的阵容，
    # 至少让行为引擎有角色可分，不至于整局一声不响。
    if live is None:
        roster = [k for k in (cfg["champions"].get("roster") or []) if k in book.index]
        if roster:
            roles = merge_assign(roster, saved_roles)
            beh.set_roles(roles)
            print("没有对局接口，按上一局阵容分配分路：" + describe(roles, book.speak))

    while True:
        loop_t0 = time.perf_counter()
        frame = cap.grab()
        found = det.detect(frame)
        now = loop_t0 - t_origin

        tracks = trk.update(found, now)

        if live is not None:
            snap = live.snapshot()
            if snap is None:
                # 对局结束（或客户端崩溃）会关闭 2999 端口。连续几次才算，
                # 免得偶发超时把一局切断。
                miss += 1
                if miss >= END_MISSES:
                    print("\n对局结束，对局接口已断开")
                    return "end"
            else:
                miss = 0
                beh.my_side = snap.get("my_side")
                # 国服接口给中文名（"齐天大圣"），全部归一成英文 key 再用
                enemies = [book.resolve(e["champion"]) for e in snap.get("enemies", [])
                           if e.get("champion")]
                if enemies and enemies != roster:
                    roster = enemies
                    print(f"本局敌方：{'、'.join(book.speak(k) for k in enemies)}")
                    # 记住本局阵容：不在局内点「勾选英雄」时，至少能列上一局这 5 个
                    cfg["champions"]["roster"] = list(enemies)
                    if cfg_path:
                        try:
                            cfgmod.save(cfg, cfg_path)
                        except Exception:
                            pass
                    # 配置里的名单和本局阵容完全不重合（换了英雄），
                    # 继续按名单过滤会整局一声不响，所以本局放开为全部播报
                    if notify and not (notify & set(enemies)):
                        print("！播报名单与本局阵容无交集（可能换了英雄），本局改为全部播报")
                        print("  建议重跑 python main.py pick 重新勾选")
                        notify = set()
                    if name_job is None or not name_job.is_alive():
                        name_job = threading.Thread(
                            target=_prebuild_names,
                            args=(book, vc, roster), daemon=True)
                        name_job.start()

                    # 分路角色：用户手动保存的优先，其次接口的 position，
                    # 再不行按 上→野→中→下→辅 顺序补齐。
                    positions = {}
                    for e in snap.get("enemies", []):
                        k2 = book.resolve(e["champion"])
                        if k2 and e.get("position"):
                            positions[k2] = e["position"]
                    roles = merge_assign(roster, saved_roles, positions)
                    beh.set_roles(roles)
                    print("  " + describe_sides(beh.my_side))
                    print("  分路：" + describe(roles, book.speak))
                    # 这一局可能要说到的句子先合成好，省得第一次播报卡半秒
                    vc.warmup(_lines_for(book, roster, roles, beh.my_side))

        _identity(frame, found, tracks, book, roster or list(book.index))
        # 阵容是已知的 5 个人，用排除法把"差一票就叫不出名字"的轨迹补齐，
        # 保证每条播报都念得出英雄名
        trk.reconcile(roster, now)

        alive = trk.confirmed(now)

        # ---- 行为播报：每 4 秒攒一批，一批最多两条 ----
        if roles:
            batch.submit(beh.update(alive, roles, now))
            for ev in batch.due(now):
                if notify and ev.key not in notify:
                    continue
                print(f"[{int(now // 60):02d}:{now % 60:04.1f}] 播报  {ev.text}")
                vc.say_line(ev.text, f"{ev.key}__{ev.action}")
        elif not warned_roles and now > 20:
            warned_roles = True
            print("！还没拿到敌方阵容，无法分配分路，这局不会播报。")
            print("  检查对局接口，或先在菜单里点「分配分路」")

        if show:
            cv2.imshow("antigank-debug",
                       _render_debug(frame, found, alive, now, beh, book, roles, batch))
            if cv2.waitKey(1) & 0xFF == ord("q"):
                return "quit"

        if verbose and now - last_report >= 3.0:
            last_report = now
            nearest = min(alive, key=lambda t: mid_perp_dist(t.nx, t.ny), default=None)
            extra = ""
            if nearest is not None:
                nk = nearest.ident()
                extra = (f"  最近 {book.speak(nk) if nk else '?'}"
                         f" [{zone_label(zone_key(nearest.nx, nearest.ny))}]")
            print(f"  · 采集 {cap.grab_ms:.1f}ms  检出 {len(found)}  "
                  f"跟踪 {len(alive)}  识别 {sum(1 for t in alive if t.ident())}/{len(alive)}{extra}")

        spent = time.perf_counter() - loop_t0
        if spent < period:
            time.sleep(period - spent)


def cmd_menu(a: argparse.Namespace) -> None:
    """启动菜单：双击 启动.bat 看到的第一个窗口。"""
    from antigank.menu import run_menu

    while True:
        act = run_menu(a.config)
        if not act:
            print("已退出。")
            return
        if act == "run":
            cmd_run(argparse.Namespace(config=a.config, fps=0, debug=False, auto=True))
            print("\n监测已停止，回到菜单（Ctrl+C 或直接关窗口退出）")
        elif act == "cal":
            cmd_calibrate(argparse.Namespace(config=a.config, delay=3.0))
        elif act == "pick":
            cmd_pick(argparse.Namespace(config=a.config, demo=False))
        elif act == "roles":
            cmd_roles(argparse.Namespace(config=a.config, demo=False))
        elif act == "priority":
            cmd_priority(argparse.Namespace(config=a.config))


def cmd_run(a: argparse.Namespace) -> None:
    import cv2

    print("启动中…（初始化采集与英雄库）")
    sys.stdout.flush()
    dpi_aware()
    cfg = cfgmod.load(a.config)
    if a.fps:
        cfg["capture"]["fps"] = a.fps
    auto = bool(getattr(a, "auto", False))

    region = cfgmod.region_tuple(cfg)
    sw, sh = screen_size()
    if region[0] == 0 and region[1] == 0:
        # 没校准不再硬拦：按 LoL 默认布局给小地图贴右下角，先让它能跑起来。
        # 猜错了在预览窗口里一眼就能看出来，比直接退出有用得多。
        region = cfgmod.default_region(sw, sh)
        print("还没校准，先用右下角猜测框 "
              f"{region[2]}x{region[3]} @ ({region[0]},{region[1]})")
        print("  红圈套不准就跑：python main.py calibrate（点左上角 + 右下角）")
    if auto and not cfg["live"]["enabled"]:
        print("--auto 靠对局接口判断开局，但 config.json 里 live.enabled 是 false")
        print("把 live.enabled 改成 true，或去掉 --auto 手动启动")
        return

    cap = Capture(region, cfg["capture"]["backend"])
    det = RingDetector(cfg["detect"])
    book = ChampionBook(CHAMP_DIR, version=cfg["champions"].get("version", ""))
    vc = Voice(VOICE_DIR, cfg["voice"]["enabled"], cfg["voice"]["min_interval"],
               line_max=float(cfg["behaviour"]["line_max"]))

    # 自动模式要尽快感知开局/结束，轮询压到 1.5 s；本地接口，无网络开销
    live = None
    if cfg["live"]["enabled"]:
        live = LiveClient(interval=1.5 if auto else cfg["live"]["interval"]).start()

    show = bool(a.debug or cfg["debug"]["window"])
    verbose = bool(cfg["debug"]["print"])
    notify = set(cfg["champions"].get("notify") or [])

    print(f"采集后端 {cap.backend_name} · 区域 {region[2]}x{region[3]} · "
          f"分析 {cfg['capture']['fps']} Hz")
    bc = cfg.get("behaviour") or {}
    from antigank.roles import ROLE_NAME

    order = bc.get("priority") or list(batch.priority)
    print(f"英雄库 {len(book.index)} 个 · 每 {bc.get('period', 2):g} 秒最多 "
          f"{bc.get('max_items', 1)} 条，单条 ≤ {bc.get('line_max', 2)} 秒")
    print("播报优先级 " + " > ".join(ROLE_NAME.get(r, r) for r in order))
    if vc.missing():
        print(f"提示：{len(vc.missing())} 条短语语音缺失（python tools/make_voice.py）")
    if notify:
        print("只播报：" + "、".join(book.speak(k) for k in notify))
    print("模式 " + ("自动（常驻，开局自动接管，一局结束自动等下一局）" if auto else "单次（立刻开始监测）"))
    print("Ctrl+C 退出" + ("，预览窗口按 q 退出" if show else ""))

    game_no = 0
    try:
        while True:
            if auto and live is not None:
                _wait_game(live)
                game_no += 1
                print(f"\n=== 第 {game_no} 局，开始监测 ===")
            res = _session(cfg, cap, det, book, vc, live, show, verbose, notify,
                           str(a.config))
            if res == "quit" or not auto:
                break
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        cap.close()
        if live:
            live.stop()
        if show:
            cv2.destroyAllWindows()



def cmd_selftest(a: argparse.Namespace) -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    from sim import selftest  # type: ignore

    cfg = cfgmod.load(a.config)
    det = RingDetector(cfg["detect"])
    res = selftest(det, frames=a.frames)
    bad = res["recall"] < 0.95 or res["precision"] < 0.95
    print("\n判定：" + ("不达标，需要调 detect 阈值" if bad else "达标"))
    if bad:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="LOL 小地图实时反 Gank 哨兵")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("menu", help="图形菜单：开始监测 / 框选小地图 / 勾选英雄 / 分配分路")

    p = sub.add_parser("run", help="开始监测")
    p.add_argument("--debug", action="store_true", help="显示预览窗口")
    p.add_argument("--fps", type=int, default=0, help="覆盖分析帧率")
    p.add_argument("--auto", action="store_true",
                   help="常驻待机：开局自动开始，一局结束自动等下一局")

    cal = sub.add_parser("calibrate", help="校准小地图位置（点两个角）")
    cal.add_argument("--delay", type=float, default=3.0,
                     help="截屏前等待几秒，留时间切回游戏（0 = 立刻截）")
    pk = sub.add_parser("pick", help="勾选要播报的敌方英雄（只列本局阵容）")
    pk.add_argument("--demo", action="store_true", help="不在局内时用固定阵容预览界面")
    rl = sub.add_parser("roles", help="分配敌方 5 人的分路角色（上/野/中/下/辅）")
    rl.add_argument("--demo", action="store_true", help="不在局内时用固定阵容预览界面")
    sub.add_parser("priority", help="调整多条播报待发时的先后顺序（分路优先级）")
    sub.add_parser("saytest", help="放三句话，验证能不能听见（排查没声音）")
    sub.add_parser("doctor", help="环境自检")
    sub.add_parser("e2e", help="验证报警链路")

    p2 = sub.add_parser("selftest", help="合成数据验证检测算法")
    p2.add_argument("--frames", type=int, default=40)

    args = ap.parse_args()
    if not args.cmd:
        # 不带参数 = 打开图形菜单，不再只打印英文帮助
        args.cmd = "menu"
    _install_log()
    if args.cmd == "e2e":
        sys.path.insert(0, str(ROOT / "tools"))
        from e2e import main as e2e_main  # type: ignore

        e2e_main()
        return
    try:
        {"menu": cmd_menu, "run": cmd_run, "calibrate": cmd_calibrate,
         "pick": cmd_pick, "roles": cmd_roles, "priority": cmd_priority,
         "saytest": cmd_saytest,
         "doctor": cmd_doctor,
         "selftest": cmd_selftest}[args.cmd](args)
    except KeyboardInterrupt:
        print("\n已停止")
    except Exception:
        # 双击 bat 时窗口会一闪而过，先把 traceback 写进日志再抛出去
        import traceback

        traceback.print_exc()
        print(f"\n出错了，完整日志在 {LOG_PATH}")
        sys.stdout.flush()
        raise


if __name__ == "__main__":
    main()
