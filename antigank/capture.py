"""画面采集。

只抓小地图那块 280x280，所以 GDI（mss）就够用：
    实测 1~2 ms/帧，理论上限 500 fps，12~20 Hz 分析完全过剩。
    而且 GDI 完全不碰显存，也不受"多显卡 / 虚拟显示器选错 output"影响。

dxgi（dxcam / bettercam）作为可选后端：整屏高帧率时才有优势，
这里必须显式指定 backend="dxgi" 才会启用，且失败自动降级到 gdi。
"""

from __future__ import annotations

import threading
import time

import numpy as np


class _GdiBackend:
    """mss：底层是 BitBlt，只拷贝目标矩形，零显存占用。"""

    needs_thread_local = True

    def __init__(self, region: tuple[int, int, int, int]):
        self.region = region
        self._local = threading.local()

    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            import mss

            sct = mss.mss()
            self._local.sct = sct
        return sct

    def grab(self) -> np.ndarray:
        l, t, w, h = self.region
        img = self._sct().grab({"left": l, "top": t, "width": w, "height": h})
        # mss 原始缓冲是 BGRA，取前 3 通道正好是 OpenCV 要的 BGR
        return np.asarray(img)[:, :, :3]

    def close(self) -> None:
        sct = getattr(self._local, "sct", None)
        if sct is not None:
            sct.close()


class _DxgiBackend:
    """DXGI Desktop Duplication。会占用一张 staging texture（约 0.3 MB 显存）。"""

    needs_thread_local = False

    def __init__(self, region: tuple[int, int, int, int]):
        try:
            import dxcam  # type: ignore
        except ImportError:
            import bettercam as dxcam  # type: ignore

        self.region = region
        l, t, w, h = region
        self.cam = dxcam.create(region=(l, t, l + w, t + h), output_color="BGR")
        if self.cam is None:
            raise RuntimeError("dxcam 创建失败（可能选不到正确的显示输出）")
        self.cam.start(target_fps=60, video_mode=False)

    def grab(self) -> np.ndarray:
        frame = self.cam.get_latest_frame()
        if frame is None:
            raise RuntimeError("dxcam 未取到帧")
        return frame  # 注意：这是复用缓冲，调用方不要就地修改

    def close(self) -> None:
        try:
            self.cam.stop()
            self.cam.release()
        except Exception:
            pass


class Capture:
    """带帧率统计的采集器。"""

    def __init__(self, region: tuple[int, int, int, int], backend: str = "gdi"):
        self.region = region
        self.backend_name = backend
        self._impl = None
        self._cost_ms: list[float] = []

        order = ["dxgi", "gdi"] if backend == "dxgi" else ["gdi"]
        last_err: Exception | None = None
        for name in order:
            try:
                self._impl = (_DxgiBackend if name == "dxgi" else _GdiBackend)(region)
                self.backend_name = name
                break
            except Exception as exc:  # 降级
                last_err = exc
        if self._impl is None:
            raise RuntimeError(f"没有可用的采集后端: {last_err}")

    def grab(self) -> np.ndarray:
        t0 = time.perf_counter()
        frame = self._impl.grab()
        self._cost_ms.append((time.perf_counter() - t0) * 1000.0)
        if len(self._cost_ms) > 120:
            del self._cost_ms[:60]
        # dxgi 返回的是复用缓冲；转成独立数组，避免被下一帧覆盖
        if self._impl.needs_thread_local is False:
            return np.array(frame, copy=True)
        return frame

    @property
    def grab_ms(self) -> float:
        return float(np.mean(self._cost_ms)) if self._cost_ms else 0.0

    def close(self) -> None:
        try:
            self._impl.close()
        except Exception:
            pass


def dpi_aware() -> None:
    """让进程直接用物理像素坐标，否则 125%/150% 缩放下截屏和校准框会错位。"""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def screen_size() -> tuple[int, int]:
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    return ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
