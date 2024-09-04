"""Virtual Desktop State module"""

import time
import logging
from typing import Dict, Optional, Tuple, Callable, List

from jigsawwm.jmk import sysinout, Vk
from jigsawwm.w32.winevent import WinEvent
from jigsawwm.w32.monitor_detector import MonitorDetector, Monitor
from jigsawwm.w32.window_detector import WindowDetector, Window, HWND
from jigsawwm.w32.monitor import set_cursor_pos
from jigsawwm.w32.window import topo_sort_windows
from jigsawwm.ui import Splash

from .monitor_state import MonitorState
from .workspace_state import WorkspaceState
from .const import (
    PREFERRED_MONITOR_INDEX,
    PREFERRED_WORKSPACE_INDEX,
    PREFERRED_WINDOW_INDEX,
    STATIC_WINDOW_INDEX,
    MONITOR_STATE,
    WORKSPACE_STATE,
)
from .config import WmConfig

logger = logging.getLogger(__name__)


class VirtDeskState:
    """VirtDeskState holds variables needed by a Virtual Desktop

    :param WindowManager manager: associated WindowManager
    :param bytearray desktop_id: virtual desktop id
    """

    desktop_id: bytearray
    config: WmConfig
    monitor_states: Dict[Monitor, MonitorState] = {}
    active_monitor_index: int = 0
    window_detector: WindowDetector
    monitor_detector: MonitorDetector
    splash: Splash
    _wait_mouse_released: bool = False
    _previous_switch_workspace_for_window_activation = 0.0

    def __init__(self, desktop_id: bytearray, config: WmConfig, splash: Splash):
        self.desktop_id = desktop_id
        self.window_detector = WindowDetector(created=self.apply_rule_to_window)
        self.monitor_detector = MonitorDetector()
        self.config = config
        self.splash = splash

    def on_monitors_changed(self):
        """Syncs the monitor states with the virtual desktop"""
        result = self.monitor_detector.detect_monitor_changes()
        if not result.changed:
            logger.info("no monitor changes detected")
            return
        windows_tobe_rearranged = set()
        # process new monitors
        if result.new_monitors:
            windows_tobe_rearranged = self.window_detector.windows
            for m in result.new_monitors:
                monitor_index = self.monitor_detector.monitors.index(m)
                logger.info("new monitor connected: %s index: %d", m, monitor_index)
                self.monitor_states[m] = MonitorState(
                    monitor_index,
                    m.name,
                    self.config.workspace_names,
                    m.get_work_rect(),
                    self.config.get_theme_for_monitor(m),
                )
        # remove monitor states
        if result.removed_monitors:
            for m in result.removed_monitors:
                ms = self.monitor_states.pop(m)
                logger.info("monitor disconnected: %s", ms.name)
                for ws in ms.workspaces:
                    windows_tobe_rearranged |= ws.windows
        # rearrange windows
        if windows_tobe_rearranged:
            for w in windows_tobe_rearranged:
                m = self.monitor_detector.monitors[
                    w.attrs.get(PREFERRED_MONITOR_INDEX, 0)
                    % len(self.monitor_detector.monitors)
                ]
                monitor_state = self.monitor_states[m]
                monitor_state.add_windows(w)
            for ms in self.monitor_states.values():
                ms.workspace.sync_windows()

    def handle_window_event(self, event: WinEvent, hwnd: Optional[HWND] = None):
        """Check if we need to sync windows for given window event"""
        # ignore if left mouse button is pressed in case of dragging
        if (
            not self._wait_mouse_released
            and event == WinEvent.EVENT_OBJECT_PARENTCHANGE
            and sysinout.state.get(Vk.LBUTTON)  # assuming JMK is enabled...
        ):
            # delay the sync until button released to avoid flickering
            self._wait_mouse_released = True
            return
        elif self._wait_mouse_released:
            if not sysinout.state.get(Vk.LBUTTON):
                self._wait_mouse_released = False
            else:
                return
        if not hwnd:
            return
        window = self.window_detector.get_window(hwnd)
        if not window.manageable:
            return
        # # filter by event
        if event == WinEvent.EVENT_SYSTEM_FOREGROUND:
            self.on_foreground_window_changed(window)
        if (
            event == WinEvent.EVENT_OBJECT_HIDE
            or event == WinEvent.EVENT_OBJECT_SHOW
            or event == WinEvent.EVENT_OBJECT_UNCLOAKED
        ):
            self.on_windows_changed()
        elif event == WinEvent.EVENT_SYSTEM_MOVESIZEEND:
            self.on_moved_or_resized(window)
        elif (
            event == WinEvent.EVENT_SYSTEM_MINIMIZESTART
            or event == WinEvent.EVENT_SYSTEM_MINIMIZEEND
        ):
            self.on_minimize_changed(window)

    def on_windows_changed(self, starting_up=False):
        """Syncs the window states with the virtual desktop"""
        if not self.monitor_states:
            logger.warning("no monitors found")
            return
        result = self.window_detector.detect_window_changes()
        if not result.changed:
            logger.info("no window changes detected")
            return
        # handle new windows
        if result.new_windows:
            for i, w in enumerate(topo_sort_windows(result.new_windows)):
                logger.info("new window appeared: %s", w)
                if PREFERRED_MONITOR_INDEX not in w.attrs:
                    logger.debug(
                        "window %s has no preferred monitor index, set it to %d",
                        w,
                        self.active_monitor_index,
                    )
                    if starting_up:
                        w.attrs[PREFERRED_MONITOR_INDEX] = (
                            self.monitor_state_from_window(w).index
                        )
                    else:
                        w.attrs[PREFERRED_MONITOR_INDEX] = self.active_monitor_index
                monitor_state = self.monitor_state_from_index(
                    w.attrs[PREFERRED_MONITOR_INDEX]
                )
                w.attrs[PREFERRED_WINDOW_INDEX] = i
                monitor_state.add_windows(w)
        # handle removed windows
        if result.removed_windows:
            logger.info("window disappeared: %s", result.removed_windows)
            for w in result.removed_windows:
                monitor_state: MonitorState = w.attrs[MONITOR_STATE]
                monitor_state.remove_windows(w)
        for ms in self.monitor_states.values():
            ms.workspace.sync_windows()

    def apply_rule_to_window(self, window: Window) -> bool:
        """Check if window is to be tilable"""
        rule = self.config.find_rule_for_window(window)
        if rule:
            logger.info("applying rule %s on %s", rule, window)
            if rule.manageable is not None:
                window.manageable = rule.manageable
            if rule.tilable is not None:
                window.tilable = rule.tilable
            if rule.preferred_monitor_index is not None:
                window.attrs[PREFERRED_MONITOR_INDEX] = (
                    rule.preferred_monitor_index % len(self.monitor_detector.monitors)
                )
            if rule.preferred_workspace_index is not None:
                window.attrs[PREFERRED_WORKSPACE_INDEX] = rule.preferred_workspace_index
            if rule.static_window_index is not None:
                window.attrs[STATIC_WINDOW_INDEX] = rule.static_window_index

    def on_foreground_window_changed(self, window: Window):
        """Try to switch workspace for window activation"""
        # a window belongs to hidden workspace just got activated
        # put your default browser into workspace and then ctrl-click a link, e.g. http://google.com
        now = time.time()
        elapsed = now - self._previous_switch_workspace_for_window_activation
        if elapsed < 1:
            # child windows got spread across multiple workspaces
            logger.warning("workspace switching happened too frequently, possible loop")
            return
        if MONITOR_STATE not in window.attrs:
            return
        ms: MonitorState = window.attrs[MONITOR_STATE]
        self.active_monitor_index = ms.index
        logger.debug(
            "set active_monitor_index: %d due to %s", self.active_monitor_index, window
        )
        ws: WorkspaceState = window.attrs[WORKSPACE_STATE]
        ws.last_active_window = window
        if not ws.showing:
            self._previous_switch_workspace_for_window_activation = now
            ms.switch_workspace(ws.index)
            logger.info(
                "switch to workspace %s due window %s got activated",
                ws,
                window,
            )

    def on_moved_or_resized(
        self, window: Window
    ) -> Optional[Tuple[Window, MonitorState]]:
        """Check if the window is being reordered"""
        # when dragging chrome tab into a new window, the window will not have MONITOR_STATE
        ms: MonitorState = window.attrs[MONITOR_STATE]
        dst_ms = self.monitor_state_from_cursor()
        # window being dragged to another monitor
        if dst_ms != ms:
            logger.info("move %s to another monitor %s", window, dst_ms)
            ms.remove_windows(window)
            dst_ms.add_windows(window)
            window.attrs[PREFERRED_MONITOR_INDEX] = dst_ms.index
            ms.workspace.sync_windows()
            dst_ms.workspace.sync_windows()
            return
        if not window.tilable:
            return
        # window being reordered
        src_idx = window.attrs[PREFERRED_WINDOW_INDEX]
        dst_idx = ms.workspace.tiling_index_from_cursor()
        if dst_idx >= 0:
            self.swap_window(
                idx=src_idx, delta=dst_idx - src_idx, workspace=ms.workspace
            )

    def on_minimize_changed(self, window: Window):
        """Handle window minimized event"""
        ws: WorkspaceState = window.attrs[WORKSPACE_STATE]
        ws.sync_windows()

    def monitor_state_from_cursor(self) -> MonitorState:
        """Retrieve monitor_state from current cursor"""
        return self.monitor_states[self.monitor_detector.monitor_from_cursor()]

    def monitor_state_from_index(self, index: int) -> MonitorState:
        """Retrieve monitor_state from index"""
        index = index % len(self.monitor_detector.monitors)
        return self.monitor_states[self.monitor_detector.monitors[index]]

    def monitor_state_from_window(self, window: Window) -> MonitorState:
        """Retrieve monitor_state from current cursor"""
        return self.monitor_states[
            self.monitor_detector.monitor_from_window(window.handle)
        ]

    @property
    def monitor_state(self) -> MonitorState:
        """Retrieve current active monitor's state"""
        return self.monitor_states[
            self.monitor_detector.monitors[self.active_monitor_index]
        ]

    def switch_window_splash(self, delta: int):
        """Switch to next or previous window"""
        window = self.window_detector.foreground_window()
        if not window or not window.manageable or not window.tilable:
            window = self.monitor_state.workspace.last_active_window
        if not window or not window.manageable or not window.tilable:
            if self.monitor_state.workspace.tiling_windows:
                window = self.monitor_state.workspace.tiling_windows[0]
        if not window or not window.manageable or not window.tilable:
            return
        monitor_state: MonitorState = window.attrs[MONITOR_STATE]
        workspace_state: WorkspaceState = window.attrs[WORKSPACE_STATE]
        src_index: int = window.attrs[PREFERRED_WINDOW_INDEX]
        dst_index = (src_index + delta) % len(workspace_state.tiling_windows)
        dst_window = workspace_state.tiling_windows[dst_index]
        dst_window.activate()
        self.splash.show_splash.emit(monitor_state, dst_window)

    def reorder_windows(
        self,
        reorderer: Callable[[List[Window], int], None],
        idx: Optional[int] = None,
        workspace: Optional[WorkspaceState] = None,
    ):
        """Reorder windows"""
        if workspace is None:
            window = self.window_detector.foreground_window()
            if not window.manageable or not window.tilable:
                return
            workspace = window.attrs[WORKSPACE_STATE]
            if idx is None:
                idx = window.attrs[PREFERRED_WINDOW_INDEX]
        if len(workspace.tiling_windows) < 2:
            return
        window = workspace.tiling_windows[idx]
        next_active_window = reorderer(workspace.tiling_windows, idx)
        workspace.arrange()
        (next_active_window or window).activate()

    def swap_window(
        self,
        delta: int,
        idx: Optional[int] = None,
        workspace: Optional[WorkspaceState] = None,
    ):
        """Swap current active managed window with its sibling by offset"""

        def swap(windows: List[Window], src_idx: int):
            dst_idx = (src_idx + delta) % len(windows)
            windows[src_idx], windows[dst_idx] = windows[dst_idx], windows[src_idx]

        self.reorder_windows(swap, idx=idx, workspace=workspace)

    def set_master(self):
        """Set the active active managed window as the Master or the second window
        in the list if it is Master already
        """

        def set_master(windows: List[Window], src_idx: int):
            src_window = windows[src_idx]
            if src_idx == 0:
                src_idx = 1
                src_window = windows[1]
            # shift elements from the beginning to the src_window
            for i in reversed(range(1, src_idx + 1)):
                windows[i] = windows[i - 1]
            # assign new master
            windows[0] = src_window
            return src_window

        self.reorder_windows(set_master)

    def toggle_tilable(self):
        """Toggle window tilable"""
        window = self.window_detector.foreground_window()
        window.tilable = not window.tilable
        if not window.tilable:
            window.shrink()
        workspace_state: WorkspaceState = window.attrs[WORKSPACE_STATE]
        workspace_state.sync_windows()

    def move_to_monitor(self, delta: int):
        """Move window to another monitor"""
        if len(self.monitor_detector.monitors) < 2:
            return
        window = self.window_detector.foreground_window()
        if not window.manageable or not window.tilable:
            return
        monitor_state: MonitorState = window.attrs[MONITOR_STATE]
        preferred_monitor_index = (monitor_state.index + delta) % len(
            self.monitor_detector.monitors
        )
        window.attrs[PREFERRED_MONITOR_INDEX] = preferred_monitor_index
        target_monitor_state = self.monitor_state_from_index(preferred_monitor_index)
        monitor_state.remove_windows(window)
        target_monitor_state.add_windows(window)
        monitor_state.workspace.sync_windows()
        target_monitor_state.workspace.sync_windows()
        if monitor_state.workspace.tiling_windows:
            monitor_state.workspace.tiling_windows[0].activate()

    def switch_monitor_splash(self, delta: int):
        """Switch to another monitor by given offset"""
        logger.debug("switch_monitor_by_offset: %s", delta)
        srcms = self.monitor_state_from_cursor()
        dstms = self.monitor_state_from_index(srcms.index + delta)
        self.active_monitor_index = dstms.index
        window = dstms.workspace.last_active_window
        if not window and dstms.workspace.tiling_windows:
            window = dstms.workspace.tiling_windows[0]
        else:
            set_cursor_pos(dstms.rect.center_x, dstms.rect.center_y)
        self.splash.show_splash.emit(dstms, window)
        if window:
            window.activate()

    def switch_theme_splash(self, delta: int) -> Callable:
        """Switch theme by offset"""
        logger.info("switching theme by offset: %s", delta)
        monitor_state = self.monitor_state_from_cursor()
        theme_index = self.config.get_theme_index(monitor_state.workspace.theme.name)
        theme = self.config.themes[(theme_index + delta) % len(self.config.themes)]
        self.monitor_state.workspace.set_theme(theme)
        self.splash.show_splash.emit(monitor_state)
