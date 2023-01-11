import sys
import time
from ctypes import *
from ctypes.wintypes import *
from dataclasses import dataclass
from io import StringIO
from tkinter import messagebox
from typing import Callable, Iterator, List, Optional

from . import process
from .sendinput import *
from .vk import Vk
from .window_structs import *

user32 = WinDLL("user32", use_last_error=True)
kernel32 = WinDLL("kernel32", use_last_error=True)
dwmapi = WinDLL("dwmapi", use_last_error=True)


def enum_windows(
    check: Optional[Callable[[HWND], EnumCheckResult]] = None
) -> List[HWND]:
    """Returns a List of all top-level windows on the screen.

    :param check: to determinate if a HWN should be added to list, or stop iteration

    :return: list of window handles
    :rtype: List[HWND]
    """
    check = check or (lambda _: 1)
    hwnds = []

    @WINFUNCTYPE(BOOL, HWND, LPARAM)
    def enum_windows_proc(hwnd: HWND, lParam: LPARAM) -> BOOL:
        r = check(hwnd)
        if EnumCheckResult.CAPTURE in r:
            hwnds.append(hwnd)
        return EnumCheckResult.STOP not in r

    if not user32.EnumWindows(enum_windows_proc, None):
        last_error = get_last_error()
        if last_error:
            raise WinError(last_error)
    return hwnds


def get_foreground_window() -> HWND:
    return user32.GetForegroundWindow()


def get_window_style(hwnd: HWND) -> WindowStyle:
    return WindowStyle(user32.GetWindowLongA(hwnd, -16))


def get_window_exstyle(hwnd: HWND) -> WindowExStyle:
    return WindowExStyle(user32.GetWindowLongA(hwnd, -20))


def show_window(hwnd: HWND, cmd: ShowWindowCmd):
    user32.ShowWindow(hwnd, cmd)


def minimize_window(hwnd: HWND):
    show_window(hwnd, ShowWindowCmd.SW_MINIMIZE)


def maximize_window(hwnd: HWND):
    show_window(hwnd, ShowWindowCmd.SW_MAXIMIZE)


def restore_window(hwnd: HWND):
    show_window(hwnd, ShowWindowCmd.SW_RESTORE)


def get_window_title(hwnd: HWND) -> str:
    title = create_unicode_buffer(255)
    user32.GetWindowTextW(hwnd, title, 255)
    return str(title.value)


def get_window_class_name(hwnd: HWND) -> str:
    buff = create_unicode_buffer(100)
    user32.GetClassNameW(hwnd, buff, 100)
    return str(buff.value)


def get_window_pid(hwnd: HWND) -> DWORD:
    pid = DWORD()
    user32.GetWindowThreadProcessId(hwnd, pointer(pid))
    return pid


def is_window_visible(hwnd: HWND) -> bool:
    return bool(user32.IsWindowVisible(hwnd))


def is_window_cloaked(hwnd: HWND) -> bool:
    cloaked = INT()
    windll.dwmapi.DwmGetWindowAttribute(
        hwnd,
        DwmWindowAttribute.DWMWA_CLOAKED,
        pointer(cloaked),
        sizeof(cloaked),
    )
    return bool(cloaked.value)


def is_window(hwnd: HWND) -> bool:
    return user32.IsWindow(hwnd)


def is_top_level_window(hwnd: HWND) -> bool:
    return user32.IsTopLevelWindow(hwnd)


def is_app_window(hwnd: HWND, style: Optional[WindowExStyle] = None) -> bool:
    style = style or get_window_style(hwnd)
    return bool(
        not is_window_cloaked(hwnd)
        and WindowStyle.SIZEBOX in style
        and not process.is_elevated(get_window_pid(hwnd))
    )


def is_manageable_window(hwnd: HWND) -> bool:
    style = get_window_style(hwnd)
    return bool(
        is_app_window(hwnd, style)
        and get_window_title(hwnd)
        and WindowStyle.MAXIMIZEBOX & style
        and WindowStyle.MINIMIZEBOX & style
        and WindowStyle.VISIBLE in style
        and not WindowStyle.MINIMIZE & style
    )


def get_first_app_window() -> HWND:
    hwnd = get_foreground_window()
    if is_app_window(hwnd):
        return hwnd

    hwnds = enum_windows(
        lambda hwnd: EnumCheckResult.CAPTURE_AND_STOP
        if is_app_window(hwnd)
        else EnumCheckResult.SKIP
    )

    if hwnds:
        return hwnds[0]


def get_window_extended_frame_bounds(hwnd: HWND) -> RECT:
    bound = RECT()
    windll.dwmapi.DwmGetWindowAttribute(
        hwnd,
        DwmWindowAttribute.DWMWA_EXTENDED_FRAME_BOUNDS,
        pointer(bound),
        sizeof(bound),
    )
    return bound


def get_window_rect(hwnd: HWND) -> RECT:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, pointer(rect)):
        raise WinError(get_last_error())
    return rect


SWP_NOACTIVATE = 0x0010
SET_WINDOW_RECT_FLAG = SWP_NOACTIVATE


def set_window_rect(hwnd: HWND, rect: RECT):
    x, y, w, h = rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    if not user32.SetWindowPos(hwnd, None, x, y, w, h, SET_WINDOW_RECT_FLAG):
        raise WinError(get_last_error())


@dataclass
class Window:
    """Represents a top-level window

    :param hwnd: HWND the window handle
    """

    _hwnd: HWND
    _last_rect = None

    def __init__(self, hwnd: HWND):
        self._hwnd = hwnd

    def __eq__(self, other):
        return isinstance(other, Window) and self._hwnd == other._hwnd

    def __hash__(self):
        return hash(self._hwnd)

    @property
    def handle(self) -> HWND:
        return self._hwnd

    @property
    def title(self) -> str:
        """Retrieves the text of the specified window's title bar (if it has one)

        Ref: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowtexta

        :return: text of the title bar
        :rtype: str
        """
        return get_window_title(self._hwnd)

    @property
    def class_name(self):
        """Retrieves the name of the class to which the specified window belongs.

        Ref: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclassnamea

        :return: class name
        :rtype: str
        """
        return get_window_class_name(self._hwnd)

    @property
    def exe(self):
        """Retrieves the full path of the executable

        :return: full path of the executable
        :rtype: str
        """
        return process.get_exepath(self.pid)

    @property
    def pid(self) -> int:
        """Retrieves the process id

        :return: process id
        :rtype: int
        """
        return get_window_pid(self._hwnd)

    @property
    def is_visible(self) -> bool:
        """Determines the visibility state of the specified window.

        :return: If the specified window, its parent window, its parent's parent window,
            and so forth, have the WS_VISIBLE style, the return value is `True`.
            Otherwise, the return value is `False`.
        :rtype: bool
        """
        return is_window_visible(self._hwnd)

    def get_style(self) -> WindowStyle:
        """Retrieves style

        :return: window style
        :rtype: WindowStyle
        """
        return get_window_style(self._hwnd)

    def get_exstyle(self) -> WindowExStyle:
        """Retrieves ex-style

        :return: window ex-style
        :rtype: ExWindowStyle
        """
        return get_window_exstyle(self._hwnd)

    def minimize(self):
        """Minimizes the specified window and activates the next top-level window in the Z order."""
        minimize_window(self._hwnd)

    def maximize(self):
        """Activates the window and displays it as a maximized window."""
        maximize_window(self._hwnd)

    def restore(self):
        """Activates and displays the window. If the window is minimized or maximized,
        the system restores it to its original size and position."""
        restore_window(self._hwnd)

    def toggle_maximize(self):
        """Toggle maximize style"""
        if self.get_style() & WindowStyle.MAXIMIZE:
            self.restore()
        else:
            self.maximize()

    @property
    def is_evelated(self):
        """Check if window is elevated (Administrator)"""
        return process.is_elevated(self.pid)

    @property
    def is_cloaked(self) -> bool:
        """Check if window is cloaked (DWM)

        Ref: https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute
        """
        return is_window_cloaked(self._hwnd)

    def exists(self) -> bool:
        return is_window(self._hwnd)

    def get_extended_frame_bounds(self) -> RECT:
        """Retrieves extended frame bounds

        Ref: https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute
        """
        return get_window_extended_frame_bounds(self._hwnd)

    def get_rect(self) -> RECT:
        """Retrieves the dimensions of the bounding rectangle of the specified window.
        The dimensions are given in screen coordinates that are relative to the upper-left
        corner of the screen

        Ref: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowrect

        :return: a RECT with top/left/bottom/right properties
        :rtype: RECT
        """
        return get_window_rect(self._hwnd)

    def set_rect(self, rect: RECT):
        """Sets the dimensions of the bounding rectangle (Call SetWindowPos with RECT)

        Ref: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos

        :param rect: RECT with top/left/bottom/right properties
        """
        set_window_rect(self._hwnd, rect)
        self._last_rect = rect

    def activate(self) -> bool:
        """Brings the thread that created current window into the foreground and activates the window"""
        return set_active_window(self)

    @property
    def last_rect(self) -> Optional[RECT]:
        return self._last_rect


def get_app_windows() -> Iterator[Window]:
    """Get all manageable windows of specified/current desktop"""
    return map(
        Window,
        enum_windows(
            lambda hwnd: EnumCheckResult.CAPTURE
            if is_app_window(hwnd)
            else EnumCheckResult.SKIP
        ),
    )


def get_manageable_windows() -> Iterator[Window]:
    """Get all manageable windows of specified/current desktop"""
    return map(
        Window,
        enum_windows(
            lambda hwnd: EnumCheckResult.CAPTURE
            if is_manageable_window(hwnd)
            else EnumCheckResult.SKIP
        ),
    )


def get_active_window() -> Optional[Window]:
    """Retrieves current activated window"""
    hwnd = get_foreground_window()
    if hwnd:
        return Window(hwnd)


def set_active_window(window: Window) -> bool:
    """Brings the thread that created the specified window into the foreground and activates the window

    Ref: https://github.com/AutoHotkey/AutoHotkey/blob/e379b60e44d35494d4a19d1e5001f2dd38773391/source/window.cpp#L25
    """
    # simple way
    if user32.SetForegroundWindow(window.handle):
        # print("simple way works")
        return
    # well, simple way didn't work, we have to make our process Foreground
    our_thread_id = kernel32.GetCurrentThreadId()
    fore_thread_id = None
    target_thread_id = user32.GetWindowThreadProcessId(window.handle, None)

    uf = False  # attached our thread to the fore thread
    ft = False  # attached the fore thread to the target thread
    curr_fore_hwnd = user32.GetForegroundWindow()
    if curr_fore_hwnd:
        fore_thread_id = user32.GetWindowThreadProcessId(curr_fore_hwnd, None)
        if fore_thread_id and fore_thread_id != our_thread_id:
            uf = user32.AttachThreadInput(our_thread_id, fore_thread_id, True)
            # print("attach our thread to the fore thread:", uf)
        if fore_thread_id and target_thread_id and fore_thread_id != target_thread_id:
            ft = user32.AttachThreadInput(fore_thread_id, target_thread_id, True)
            # print("attach fore thread to the target thread:", ft)
    new_fore_window = None
    retry = 5
    while new_fore_window != window.handle and retry > 0:
        send_input(
            INPUT(
                type=INPUTTYPE.KEYBOARD,
                ki=KEYBDINPUT(wVk=Vk.MENU, dwFlags=KEYEVENTF.KEYUP),
            ),
            INPUT(
                type=INPUTTYPE.KEYBOARD,
                ki=KEYBDINPUT(wVk=Vk.MENU, dwFlags=KEYEVENTF.KEYUP),
            ),
        )
        user32.SetForegroundWindow(window.handle)
        new_fore_window = user32.GetForegroundWindow()
        retry -= 1
        time.sleep(0.01)
    # print(
    #     f"our: {our_thread_id}   fore: {fore_thread_id}   target{target_thread_id}  succeeded: {new_fore_window == window.handle}"
    # )
    # detach input thread
    if uf:
        user32.AttachThreadInput(our_thread_id, fore_thread_id, False)
    if ft:
        user32.AttachThreadInput(fore_thread_id, target_thread_id, False)
    # print("detached")


def minimize_active_window():
    window = get_active_window()
    if window:
        window.minimize()


def toggle_maximize_active_window():
    window = get_active_window()
    if window:
        window.toggle_maximize()


def sprint_window(hwnd: HWND) -> str:
    f = StringIO()
    inspect_window(hwnd, file=f)
    return f.getvalue()


def inspect_window(hwnd: HWND, file=sys.stdout):
    print(file=file)
    window = Window(hwnd)
    if not window.exists():
        print("window doesn't exist anymore")
        return
    print("hwnd         :", window.handle, file=file)
    print("title        :", window.title, file=file)
    print("pid          :", window.pid, file=file)
    print("class name   :", window.class_name, file=file)
    print("exe path     :", window.exe, file=file)
    style = window.get_style()
    style_flags = []
    for s in WindowStyle:
        if s in style:
            style_flags.append(s.name)
    print("style        :", ", ".join(style_flags), file=file)
    exstyle = window.get_exstyle()
    exstyle_flags = []
    for s in WindowExStyle:
        if s in exstyle:
            exstyle_flags.append(s.name)
    print("exstyle      :", ", ".join(exstyle_flags), file=file)
    print("is_cloaked   :", window.is_cloaked, file=file)
    rect = window.get_rect()
    print("rect         :", rect.left, rect.top, rect.right, rect.bottom, file=file)
    bound = window.get_extended_frame_bounds()
    print("bound        :", bound.left, bound.top, bound.right, bound.bottom, file=file)


def inspect_active_window():
    text = sprint_window(get_foreground_window())
    messagebox.showinfo("JigsawWM", text)


if __name__ == "__main__":
    import time

    time.sleep(2)
    inspect_active_window()
    # for window in get_app_windows():
    #     inspect_window(window.handle)
    # for win in get_windows():
    #     inspect_window(win)
