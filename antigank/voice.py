"""语音播报。

两种播法并存：

1. **整句合成**（现在的默认路径，`say_line`）
   文本形如"敌方盲僧从上路消失"，第一次播的时候合成并缓存到
   `assets/voice/lines/`，之后直接读缓存。

   为什么不用原来的"英雄名 + 动作短语"两段拼接：实测最坏组合
   "敌方德玛西亚皇子" (1.19s) + "出现在敌方上半野区" (1.58s) = 2.77 秒，
   远超方案要求的"每条不超过两秒"。拼段还有个副作用：段与段之间有
   几十毫秒静音，连着播两条时听起来像卡顿。整句合成一次念完，又短又顺。

   仍然卡时长：合成后量一下，超过上限就按比例提高语速重合成一次。

2. **短语序列**（`say_seq`，旧接口，e2e / 单元测试还在用）
   预生成的固定 wav，播放延迟 <5 ms。

两条硬规则：名字必须念出来（宁可晚 1.8 秒、宁可先用推测名），
且任何一段缺失都不能哑掉或退化成"有敌人"。
"""

from __future__ import annotations

import gc
import hashlib
import queue
import threading
import time
import wave
from pathlib import Path

try:
    import winsound
except ImportError:
    winsound = None

ZONE_TEXT = {
    "zone_jg_blue_top": "出现在蓝方上半野区",
    "zone_jg_blue_bot": "出现在蓝方下半野区",
    "zone_jg_red_top": "出现在红方上半野区",
    "zone_jg_red_bot": "出现在红方下半野区",
    "zone_jg_my_top": "出现在我方上半野区",
    "zone_jg_my_bot": "出现在我方下半野区",
    "zone_jg_their_top": "出现在敌方上半野区",
    "zone_jg_their_bot": "出现在敌方下半野区",
    "zone_river": "出现在河道",
    "zone_top": "出现在上路",
    "zone_bot": "出现在下路",
    "zone_baron": "出现在大龙坑",
    "zone_dragon": "出现在小龙坑",
    "zone_deep_blue": "出现在蓝方半场深处",
    "zone_deep_red": "出现在红方半场深处",
}

PHRASES = {
    "danger_mid": "已经在中路",
    "warn_up": "从上方靠近中路",
    "warn_down": "从下方靠近中路",
    "warn_left": "从左侧靠近中路",
    "warn_right": "从右侧靠近中路",
    "warn_ul": "从左上方靠近中路",
    "warn_ur": "从右上方靠近中路",
    "warn_dl": "从左下方靠近中路",
    "warn_dr": "从右下方靠近中路",
    "warn_close": "正在靠近中路",
    "fog": "刚从视野里消失",
    # 名字前统一加"敌方"；foe_<英雄> 缺失时用它 + champ_<英雄> 兜底
    "foe": "敌方",
    "beep": "",
}
PHRASES.update(ZONE_TEXT)


def zone_voice_key(zone_key: str, my_side: str | None) -> str:
    """蓝/红 → 我方/敌方（拿到对局信息后说话更像人话）。"""
    if not zone_key.startswith("jg_"):
        return f"zone_{zone_key}"
    _, color, lane = zone_key.split("_")
    if my_side in ("ORDER", "CHAOS"):
        mine = "blue" if my_side == "ORDER" else "red"
        return f"zone_jg_{'my' if color == mine else 'their'}_{lane}"
    return f"zone_jg_{color}_{lane}"


class Voice:
    def __init__(self, voice_dir: str | Path = "assets/voice",
                 enabled: bool = True, min_interval: float = 1.2,
                 line_max: float = 1.95):
        self.dir = Path(voice_dir)
        self.lines = self.dir / "lines"
        self.line_max = float(line_max)
        self.enabled = enabled and winsound is not None
        self.min_interval = float(min_interval)
        self._q: queue.Queue[tuple] = queue.Queue(maxsize=16)
        self._last_t = -1e9
        self._last_key = ""
        self._warned: set[str] = set()
        self._synthesized = 0        # 运行期现合成的条数（预热不算）
        self.play_fail = 0           # 播放失败次数（诊断用）

        # ---- 合成串行化 ----
        # pyttsx3 / SAPI5 在同一个进程里**只认第一个用它的线程**：第二个线程再
        # 去 init，不会报错，而是 runAndWait 直接挂死或静默返回失败（实测 B 线程
        # 连 6 句全部 ok=False）。表现就是控制台一直在"播报"，耳机里一声没有。
        # 所以进程内只留一个合成线程，谁要合成谁排队。
        self._syn_q: queue.Queue[tuple] = queue.Queue()
        self._syn_thread: threading.Thread | None = None
        self._syn_tid = -1
        self._syn_guard = threading.Lock()
        self._path_locks: dict[str, threading.Lock] = {}

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def has(self, key: str) -> bool:
        return (self.dir / f"{key}.wav").exists()

    def reset(self) -> None:
        """换局时清空积压和冷却，否则上一局的最后一句会拖到新的一局开头。"""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._last_t, self._last_key = -1e9, ""

    def missing(self) -> list[str]:
        return [k for k, text in PHRASES.items() if text and not self.has(k)]

    def _resolve(self, keys) -> list[str]:
        """把序列里的 key 换成实际存在的 wav。

        foe_<英雄> 是"敌方+名字"一条合成的，开局才补生成；万一还没生成好，
        就拆成 foe.wav（敌方）+ champ_<英雄>.wav 两段 —— 无论走哪条路，
        播出来都一定是"敌方XXX"，不会退化成没名字的"有敌人"。
        """
        out: list[str] = []
        for k in keys:
            if not k:
                continue
            if self.has(k):
                out.append(k)
                continue
            if k.startswith("foe_") and self.has("foe"):
                out.append("foe")
                champ = f"champ_{k[4:]}"
                if self.has(champ):
                    out.append(champ)
        return out

    def say(self, key: str) -> None:
        self.say_seq((key,))

    def say_seq(self, keys, cooldown_key: str | None = None) -> None:
        if not self.enabled:
            return
        seq = tuple(self._resolve(keys))
        if not seq:
            return
        ck = cooldown_key or seq[-1]
        now = time.perf_counter()
        if ck == self._last_key and now - self._last_t < self.min_interval:
            return
        if now - self._last_t < 0.9:      # 两段播报之间留出间隔，避免叠字
            return
        if self._q.full():
            return
        self._last_t, self._last_key = now, ck
        self._q.put(("seq", seq))

    # ---------- 整句播报（新） ----------
    def line_path(self, text: str, key: str, max_sec: float | None = None) -> Path | None:
        """取这句话的 wav，必要时现场合成。保证时长不超过 max_sec。

        文件名带文本摘要，改了文案自然会重新生成，不会读到上一版的老录音。
        """
        if not text:
            return None
        limit = float(max_sec if max_sec is not None else self.line_max)
        tag = hashlib.md5(text.encode("utf-8")).hexdigest()[:6]
        path = self.lines / f"{key}-{tag}.wav"

        if path.exists():
            d = wav_seconds(path)
            if 0 < d <= limit:
                return path

        # 同一条句子被两个线程同时要（播放线程 + 预热线程）时不要各合成一遍
        lock = self._path_locks.setdefault(str(path), threading.Lock())
        with lock:
            if path.exists():
                d = wav_seconds(path)
                if 0 < d <= limit:
                    return path
            if not self._synth_sync(text, path, limit):
                return None
        return path if path.exists() else None

    def prepare(self, items) -> int:
        """补齐一批固定短语：items = [(文本, 绝对路径), ...]。

        `_prebuild_names` 原来自己开线程直接调 pyttsx3，正好撞上"第二个线程"
        那个坑，所以改成走这里排队，永远只有合成线程碰引擎。
        """
        made = 0
        for text, path in items or ():
            p = Path(path)
            if p.exists() and p.stat().st_size > 3000:
                continue
            if self._synth_sync(text, p, None):
                made += 1
        return made

    # ---------- 合成线程 ----------
    def _start_synth(self) -> None:
        with self._syn_guard:
            if self._syn_thread is not None and self._syn_thread.is_alive():
                return
            self._syn_thread = threading.Thread(target=self._syn_worker,
                                               name="antigank-tts", daemon=True)
            self._syn_thread.start()

    def _synth_sync(self, text: str, path: Path, limit: float | None) -> bool:
        """把合成任务交给合成线程，并等它做完。"""
        self._start_synth()
        if threading.get_ident() == self._syn_tid:      # 已经是合成线程自己在调
            return self._synth_attempt(text, path, limit)
        done = threading.Event()
        box: list[bool] = []
        self._syn_q.put((text, path, limit, done, box))
        if not done.wait(30.0):
            return False
        return bool(box and box[0])

    def _syn_worker(self) -> None:
        self._syn_tid = threading.get_ident()
        while True:
            text, path, limit, done, box = self._syn_q.get()
            try:
                box.append(self._synth_attempt(text, path, limit))
            except Exception:
                box.append(False)
            finally:
                done.set()

    def _synth_attempt(self, text: str, path: Path, limit: float | None) -> bool:
        """合成一条；超时长就提速重录一次。只能在合成线程里调。"""
        rate = 320
        if not synth_wav(text, path, rate=rate):
            return False
        self._synthesized += 1
        d = wav_seconds(path)
        if limit and d > limit:
            target = min(620, int(rate * d / (limit * 0.96)))
            if target > rate and synth_wav(text, path, rate=target):
                self._synthesized += 1
        return path.exists() and path.stat().st_size > 3000

    def say_line(self, text: str, key: str = "") -> None:
        """排队播一句整句语音。合放在播放线程里做，不占主循环。"""
        if not self.enabled or not text:
            return
        try:
            self._q.put_nowait(("line", text, key or "line"))
        except queue.Full:
            pass

    def warmup(self, items, on_done=None) -> threading.Thread:
        """后台把这一批句子先生成好（开局各一次，约 5 秒）。

        items = [(文本, 缓存 key), ...]。生成失败的直接跳过，运行时还会再试。
        """

        def job() -> None:
            for text, key in items or ():
                if not text:
                    continue
                try:
                    self.line_path(text, key)
                except Exception:
                    pass
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass

        th = threading.Thread(target=job, daemon=True)
        th.start()
        return th

    @property
    def synthesized(self) -> int:
        return self._synthesized

    def _run(self) -> None:
        while True:
            item = self._q.get()
            try:
                kind = item[0]
                if kind == "seq":
                    for key in item[1]:
                        self._play(self.dir / f"{key}.wav", key)
                elif kind == "line":
                    path = self.line_path(item[1], item[2])
                    if path is not None:
                        self._play(path, item[2])
                    elif item[2] not in self._warned:
                        self._warned.add(item[2])
                        print(f"  [voice] 这句没合成出来：{item[1]}", flush=True)
            except Exception:
                pass

    def _play(self, path: Path, tag: str) -> None:
        try:
            if path.exists():
                winsound.PlaySound(str(path), winsound.SND_FILENAME)
            elif tag not in self._warned:
                self._warned.add(tag)
                winsound.Beep(880, 90)
        except Exception as e:
            # 音频设备被独占（游戏独占模式）时这里会抛异常，以前被 pass 掉了，
            # 结果就是"检测到了、控制台也在播报、就是没声音"，还查不出原因。
            self.play_fail += 1
            if self.play_fail <= 3:
                print(f"  [voice] 播放失败 {path.name}: {e}", flush=True)


def wav_seconds(path: str | Path) -> float:
    """wav 的时长（秒）。读不出来返回 0。"""
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / max(1, w.getframerate())
    except Exception:
        return 0.0


def synth_wav(text: str, path: str | Path, rate: int = 300) -> bool:
    """用 SAPI5 合成一条语音（需要 pyttsx3）。

    **两条铁律，动了就会挂死**（实测出来的，别改回去）：

    1. 合成完只做 `del eng` + `gc.collect()`，绝对不调 `eng.stop()`。
       stop() 会把 SAPI 的内部事件循环卡住，紧接着下一次 `pyttsx3.init()`
       就永远不返回 —— 主程序里表现为"第 4 个英雄的语音生成到一半，整条
       语音链路再也不出声"。
    2. 整个进程只能有一个线程调这个函数。pyttsx3 在第二个线程里 init 不会
       报错，而是静默失败（runAndWait 直接返回，文件根本不生成）。Voice
       已经用单合成线程把它串行化了，别在别处直接调。
    """
    try:
        import pyttsx3  # type: ignore
    except ImportError:
        return False
    if not text:
        return False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    eng = None
    try:
        eng = pyttsx3.init("sapi5")
        for v in eng.getProperty("voices"):
            blob = f"{v.name} {v.id}"
            if any(s in blob for s in ("Chinese", "Huihui", "zh-CN", "Xiaoxiao")):
                eng.setProperty("voice", v.id)
                break
        eng.setProperty("rate", rate)
        eng.setProperty("volume", 1.0)
        eng.save_to_file(text, str(path))
        eng.runAndWait()
    except Exception:
        return False
    finally:
        # 必须彻底销毁，否则下一次 init 会卡死
        del eng
        gc.collect()
    return path.exists() and path.stat().st_size > 3000


def write_tone(path: str | Path, kind: str, rate: int = 22050) -> None:
    """没有 TTS 时的降级音。"""
    import math
    import struct

    if kind == "danger_mid":
        seq = [(1200, 90), (0, 30), (1200, 90), (0, 30), (1200, 140)]
    elif kind == "fog":
        seq = [(700, 90), (0, 40), (560, 120)]
    elif kind == "beep":
        seq = [(900, 100)]
    else:
        seq = [(880, 70), (0, 25), (880, 70)]
    out: list[int] = []
    for freq, ms in seq:
        n = int(rate * ms / 1000)
        for i in range(n):
            if freq == 0:
                out.append(0)
            else:
                env = min(1.0, i / 80.0) * min(1.0, (n - i) / 200.0)
                out.append(int(9000 * env * math.sin(2 * math.pi * freq * i / rate)))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, s))) for s in out))
