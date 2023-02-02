from functools import partial
from typing import Callable, Iterator, Tuple

from .layouts import Layout, dwindle, plug_rect, widescreen_dwindle

# Rect holds physical coordinate for rectangle (left/top/right/bottom)
Rect = Tuple[int, int, int, int]

# Tiler generates physical Rects for specified total number of windows based on given Layout
Tiler = Callable[[Layout, Rect, int], Iterator[Rect]]

# LayoutTiler generates physical Rects for specified total number of windows
LayoutTiler = Callable[[Rect, int], Iterator[Rect]]


def direct_tiler(layout: Layout, work_area: Rect, total_windows: int) -> Iterator[Rect]:
    """Generates physical Rects for work_area Rect with specified layout"""
    rects = layout(total_windows)
    w = work_area[2] - work_area[0]
    h = work_area[3] - work_area[1]
    is_portrait = w < h
    if is_portrait:
        # rotate 90 degree if monitor in portrait mode
        rects = map(lambda r: (r[1], r[0], r[3], r[2]), rects)
    for float_rect in rects:
        yield tuple(int(f) for f in plug_rect(float_rect, work_area))


def obs_tiler(
    layout: Layout,
    work_area: Rect,
    total_windows: int,
    obs_width: int = 1920,
    obs_height: int = 1080,
) -> Iterator[Rect]:
    """Generates physical Rects for work_area Rect with specified layout, but leave a
    reserved area on top right corner for OBS recording
    """
    wl, wt, wr, wb = work_area
    scr_width, scr_height = wr - wl, wb - wt
    # fallback to direct_tiler when work_area is smaller than obs reserved area
    if obs_width >= scr_width or obs_height >= scr_height:
        yield from direct_tiler(layout, work_area, total_windows)
        return
    if total_windows == 0:
        return
    fr = wr - obs_width
    # first window on the left
    yield wl, wt, fr, wb
    if total_windows == 1:
        return
    # second window on the bottom right
    yield fr, wt + obs_height, wr, wb
    if total_windows == 2:
        return
    obs_rect = (fr, wt, wr, wt + obs_height)
    yield from direct_tiler(layout, obs_rect, total_windows - 2)


dwindle_layout_tiler: LayoutTiler = partial(direct_tiler, dwindle)
widescreen_dwindle_layout_tiler: LayoutTiler = partial(direct_tiler, widescreen_dwindle)
obs_dwindle_layout_tiler: LayoutTiler = partial(obs_tiler, dwindle)

if __name__ == "__main__":
    print("direct dwindle")
    for n in range(1, 5):
        print(
            list(
                direct_tiler(
                    layout=dwindle,
                    work_area=(10, 10, 3450, 1450),
                    total_windows=n,
                )
            )
        )
    print("obs dwindle")
    for n in range(1, 5):
        print(
            list(
                obs_tiler(
                    layout=dwindle,
                    work_area=(10, 10, 3450, 1450),
                    total_windows=n,
                )
            )
        )
