"""PIL 中文字幕层 —— cv2.putText 只支持 ASCII，所有中文都走这里。

只依赖 Pillow + 系统字体（微软雅黑 / 黑体 / 宋体），不引入 GUI 框架，
因为运行时用的是便携版 Python，没有打包 tcl/tk。
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

FONTS = ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc",
         "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc")
_fonts: dict[int, ImageFont.FreeTypeFont] = {}


def font(size: int) -> ImageFont.FreeTypeFont:
    f = _fonts.get(size)
    if f is not None:
        return f
    for path in FONTS:
        try:
            f = ImageFont.truetype(path, size)
            break
        except Exception:
            continue
    else:
        f = ImageFont.load_default()
    _fonts[size] = f
    return f


def text(d: ImageDraw.ImageDraw, xy: tuple[float, float], s: str, size: int = 20,
         fill: tuple[int, int, int] = (255, 255, 255), anchor: str = "la",
         stroke: int = 0,
         stroke_fill: tuple[int, int, int] = (0, 0, 0)) -> None:
    """单行文字。anchor: 'la' 左上 / 'ma' 居中上 / 'mm' 正中。"""
    d.text(xy, s, font=font(size), fill=fill, anchor=anchor,
           stroke_width=stroke, stroke_fill=stroke_fill)


def measure(s: str, size: int) -> float:
    return font(size).getlength(s)
