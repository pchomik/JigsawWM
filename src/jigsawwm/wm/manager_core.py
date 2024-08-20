"""WindowManagerCore is the core of the WindowManager, it manages the windows"""
import logging
import os
import pickle
import time
from typing import Dict, List, Set, Tuple, Optional, Callable
from queue import SimpleQueue
from threading import Thread

from jigsawwm.w32 import hook
from jigsawwm.w32.monitor import (
    Monitor,
    get_monitor_from_window,
    get_monitors,
    get_cursor_pos,
    get_monitor_from_pos
)
from jigsawwm.w32.reg import get_current_desktop_id
from jigsawwm.w32.window import (
    DWORD,
    HWND,
    LONG,
    Window,
    is_visible_app_window,
    get_foreground_window,
    iter_windows,
    windows_might_hidden_by_us,
)
from jigsawwm.w32.monitor import get_monitor_from_cursor
from jigsawwm.w32.winevent import WinEvent
from jigsawwm.jmk import sysinout, Vk
from jigsawwm import ui, workers

from .virtdesk_state import VirtDeskState, MonitorState
from .config import WmConfig
from .debug_state import inspect_virtdesk_states

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = os.path.join(os.getenv("LOCALAPPDATA"), "jigsawwm", "wm.state")

class WindowManagerCore:
    """WindowManager detect the monitors/windows state and arrange them dynamically
    keep in mind that not all windows are managed by the WindowManager, only those
    returned by `jigsawwm.w32.get_normal_windows()` and not ignored would be managed.

    The WindowManager works just like other Dynamic Window Managers, it store all
    managed windows in a list, the first one in the list is called Master, it would
    normally take up most area of the screen while others occuppy the rest.

    :param List[Theme] themes: all avaiable themes for user to switch
    :param ignore_exe_names: list of executable filenames that you don't want them
                             to be managed/arranged
    :param init_exe_sequence: list of executable filenames and title search criteria
                              that are to be kept in exactly this order when
                              distributing into windows
    """

    virtdesk_states: Dict[bytearray, VirtDeskState]
    config: WmConfig
    _hook_ids: List[int] = []
    _wait_mouse_released: bool = False
    _managed_windows: Dict[HWND, Window] = {}
    _queue: Optional[SimpleQueue]  = None
    _consumer: Optional[Thread] = None

    def __init__(
        self,
        config: WmConfig
    ):
        self.config = config
        self.virtdesk_states = {}

    @property
    def virtdesk_state(self) -> VirtDeskState:
        """Retrieve virtual desktop state"""
        desktop_id = get_current_desktop_id()
        virtdesk_state = self.virtdesk_states.get(desktop_id)
        if virtdesk_state is None:
            # make sure monitor_state for current virtual desktop exists
            virtdesk_state = VirtDeskState(self.config, desktop_id)
            self.virtdesk_states[desktop_id] = virtdesk_state
        return virtdesk_state

    def get_active_monitor_state(self) -> MonitorState:
        """Get active monitor state"""
        monitor = get_monitor_from_cursor()
        return self.virtdesk_state.get_monitor_state(monitor)

    def get_active_tilable_window(self) -> Tuple[Window, MonitorState]:
        """Get active windows"""
        logger.debug("get_active_tilable_window")
        window, monitor_state = self.get_active_window()
        if not window or window not in monitor_state.tilable_windows:
            return None, None
        return window, monitor_state

    def get_active_window(self) -> Tuple[Window, MonitorState]:
        """Get active windows"""
        logger.debug("get_active_window")
        hwnd = get_foreground_window()
        if not hwnd:
            return None, None,
        window = self._managed_windows.get(hwnd)
        if not window:
            return None, None,
        monitor_state = self.virtdesk_state.get_monitor_state(get_monitor_from_window(window.handle))
        if window not in monitor_state.windows:
            return None, None
        return window, monitor_state

    def start(self):
        """Start the WindowManagerCore service"""
        self.config.prepare()
        self._queue = SimpleQueue()
        self._consumer = Thread(target=self._consume_events)
        self._consumer.start()
        self.init_sync()
        self.install_hooks()
        ui.on_screen_changed(self._screen_changed_callback)

    def stop(self):
        """Stop the WindowManagerCore service"""
        ui.on_screen_changed(None)
        self.uninstall_hooks()
        self._queue.put(False)
        self._consumer.join()

    def _consume_events(self):
        while True:
            try:
                # wait for the next task
                event = self._queue.get()
                if not event:
                    break # terminate
                event, args, ts = event
                delay = self.is_event_interested(event, args)
                if delay:
                    # delay for a certain time for windows state to be stable
                    #  case 1: CVR won't be tiled when restored with maximized mode
                    #  case 2: libreoffice is not tiled on first launch
                    tts = delay - (time.time() - ts)
                    if tts > 0:
                        time.sleep(tts)
                    logger.info("!!! REACT on event %s for window %s", event.name, Window(args))
                    self.sync_windows()
                elif event == WinEvent.EVENT_SWITCH_WORKSPACE:
                    self._switch_workspace(*args)
                elif event == WinEvent.EVENT_MOVE_TO_WORKSPACE:
                    self._move_to_workspace(*args)
                elif event == WinEvent.EVENT_HIDE_SPLASH:
                    ui.hide_windows_splash()
            except: # pylint: disable=bare-except
                logger.exception("consume_queue error", exc_info=True)

    def is_event_interested(self, event: WinEvent, hwnd: HWND) -> float:
        """Check if event is interested"""
        if event == WinEvent.EVENT_SCREEN_CHANGED:
            logger.info("new monitors: %s old monitors: %s", set(get_monitors()), self.virtdesk_state.monitor_states.keys())
            return 0.5
        window = self._managed_windows.get(hwnd) or Window(hwnd)
        # ignore if left mouse button is pressed in case of dragging
        if not self._wait_mouse_released and event == WinEvent.EVENT_OBJECT_PARENTCHANGE and sysinout.state.get( Vk.LBUTTON )  :
            # delay the sync until button released to avoid flickering
            logger.debug("start waiting mouse release on event %s from window %s", event.name, window)
            self._wait_mouse_released = True
            return False
        elif self._wait_mouse_released:
            if not sysinout.state.get( Vk.LBUTTON ):
                logger.debug("finish waiting mouse release on event %s from window %s", event.name, window)
                self._wait_mouse_released = False
            else:
                return False
        # # filter by event
        if event == WinEvent.EVENT_SYSTEM_FOREGROUND:
            # a window belongs to hidden workspace just got activated
            # put your default browser into workspace and then ctrl-click a link, e.g. http://google.com 
            state = self.virtdesk_state.find_window_in_hidden_workspaces(hwnd)
            if state:
                monitor_state, workspace_index  = state
                logger.info("switch workspace to index %d on monitor %s for event %s of activated window %s", workspace_index, monitor_state.monitor.name, event.name, window)
                monitor_state.switch_workspace(workspace_index, no_activation=True)
            logger.debug("ignore foreground window %s", window)
            return False
        elif event == WinEvent.EVENT_OBJECT_SHOW or event == WinEvent.EVENT_OBJECT_UNCLOAKED:
            if hwnd in self._managed_windows or not self.is_window_manageable(window):
                return False
        elif event == WinEvent.EVENT_OBJECT_HIDE: # same as above
            # fix case: toggle chrome fullscreen
            # window.is_visible is for vscode, it somehow genertes hide event when unfocused
            if hwnd not in self._managed_windows or window.is_visible or self.virtdesk_state.find_window_in_hidden_workspaces(hwnd):
                return False
            # if logger.isEnabledFor(logging.DEBUG):
            #     inspect_virtdesk_states(self.virtdesk_states)
        elif event == WinEvent.EVENT_SYSTEM_MOVESIZEEND:
            if self.try_swapping_window(window):
                return False
        elif event == WinEvent.EVENT_SYSTEM_MINIMIZEEND:
            if not window.is_visible:
                return False
            # if logger.isEnabledFor(logging.DEBUG):
            #     inspect_virtdesk_states(self.virtdesk_states)
        elif event == WinEvent.EVENT_SYSTEM_MINIMIZESTART:
            if self.virtdesk_state.find_window_in_hidden_workspaces(hwnd):
                return False
        else:
            if event not in (
                WinEvent.EVENT_OBJECT_LOCATIONCHANGE,
                WinEvent.EVENT_OBJECT_NAMECHANGE,
                # WinEvent.EVENT_OBJECT_CREATE,
                WinEvent.EVENT_SYSTEM_MENUSTART,
                WinEvent.EVENT_SYSTEM_MENUEND,
                WinEvent.EVENT_OBJECT_REORDER,
                WinEvent.EVENT_SYSTEM_CAPTURESTART,
                WinEvent.EVENT_SYSTEM_CAPTUREEND,
            ):
                # do NOT inspect Window instance here, would crash the app
                # logger.debug("ignore winevent %s for window %s", event.name, hwnd)
                pass
            return False

        return 0.2

    def try_swapping_window(self, window: Window) -> Optional[Tuple[Window, MonitorState]]:
        """Check if the window is being reordered"""
        logger.debug("try swapping windows")
        monitor = get_monitor_from_window(window.handle)
        monitor_state = self.virtdesk_state.get_monitor_state(monitor)
        target_window, target_monitor_state = None, None
        window_index = -1
        try:
            window_index = monitor_state.workspace.tilable_windows.index(window)
        except ValueError:
            return
        if window_index < 0:
            return False
        pos = get_cursor_pos()
        target_monitor = get_monitor_from_pos(pos.x, pos.y)
        target_monitor_state = self.virtdesk_state.get_monitor_state(target_monitor)
        target_window_index = -1
        for i, w in enumerate(target_monitor_state.workspace.tilable_windows):
            r = w.restricted_actual_rect
            if pos.x > r.left and pos.x < r.right and pos.y > r.top and pos.y < r.bottom:
                target_window = w
                target_window_index = i
        if not target_window or target_window == window:
            return False
        # swap
        a = monitor_state.workspace.tilable_windows
        b = target_monitor_state.workspace.tilable_windows
        a[window_index], b[target_window_index] = target_window, window
        a = window.restricted_rect
        if not a:
            raise ValueError("window has no restricted rect")
        b = target_window.restricted_rect
        if not b:
            raise ValueError("target window has no restricted rect")
        window.set_restrict_rect(b)
        target_window.set_restrict_rect(a)
        self.save_state()
        return True

    def init_sync(self):
        """The first synchronization of windows state to the system state at app startup"""
        # load windows state from the last session
        if not self.load_state():
            self.sync_windows()

    def sync_windows(self) -> bool:
        """Synchronize internal windows state to the system state synchronously"""
        virtdesk_state = self.virtdesk_state
        # gather all manageable windows
        manageable_windows = self.get_manageable_windows()
        # sync monitors
        monitors = set(get_monitors())
        group_wins_by_mons: Dict[Monitor, Set[Window]] = {
            monitor: set() for monitor in monitors
        }
        removed_monitors = set(virtdesk_state.monitor_states.keys()) - monitors
        for removed_monitor in removed_monitors:
            removed_state = virtdesk_state.monitor_states.pop(removed_monitor)
            # when new monitor plugged in, the existing monitor handle would be changed as well
            # need to keep the monitor state and workspace state
            reappeard_monitor = next(filter(lambda m: m.name == removed_monitor.name, monitors), None) # pylint: disable=cell-var-from-loop
            if reappeard_monitor:
                removed_state.monitor = reappeard_monitor
                virtdesk_state.monitor_states[reappeard_monitor] = removed_state
                for workspace in removed_state.workspaces:
                    workspace.monitor = reappeard_monitor
                continue
            for workspace in removed_state.workspaces:
                # unhide all windows in the workspace and append them to the list
                # to be re-arranged
                for window in workspace.windows:
                    if not window.exists():
                        continue
                    logger.debug("unhide %s", window)
                    window.show()
                    manageable_windows.add(window)
        if not manageable_windows:
            return
        # group manageable windows by their current monitor
        monitors =  list(sorted(monitors, key=lambda m: m.name))
        for window in manageable_windows:
            # sometimes the window reappeared after being hidden
            if virtdesk_state.find_window_in_hidden_workspaces(window.handle):
                window.hide()
                continue
            monitor = None
            if window.handle not in self._managed_windows: # first seen
                monitor = self.find_monitor_from_config(window, monitors)
            if not monitor: # not rule found for the window or it has been seen before
                monitor = get_monitor_from_window(window.handle)
            if not monitor:
                continue
            self._managed_windows[window.handle] = window
            # monitor = (
                # virtdesk_state.find_monitor_of_window(window) # window has been managed
                # or self.find_monitor_from_config(window, monitors) # window has a rule
                # or (init and get_monitor_from_window(window.handle)) # fallback: window existing before manager start
                # or get_monitor_from_cursor() #  fallback: window appearing after manager start
            # )
            # add window to lists
            group_wins_by_mons[monitor].add(window)
        # synchronize windows on each monitor
        # pass down to monitor_state for further synchronization
        changed = False
        for monitor, windows in group_wins_by_mons.items():
            logger.info("distribute windows %s to %s", windows, monitor.name)
            monitor_state = virtdesk_state.get_monitor_state(monitor)
            changed |= monitor_state.sync_windows(windows)
        if changed:
            self.save_state()

    def find_monitor_from_config(self, window: Window, monitors: List[Monitor]) -> Optional[Monitor]:
        """Find monitor from the config rules for the window"""
        # logger.debug("find_monitor_from_config %s", window)
        rule = self.config.find_rule_for_window(window)
        if rule:
            logger.info("rule %s found for %s", rule, window)
            window.attrs["rule"] = rule
            if len(monitors) > rule.to_monitor_index:
                return monitors[rule.to_monitor_index]
        # logger.debug("no rule found for %s", window)
        return None

    def get_manageable_windows(self) -> Set[Window]:
        """Retrieve all manageable windows"""
        # TODO: compress self._managed_windows to remove dead windows
        manageable_windows = set()
        def check_window(hwnd: HWND) -> bool:
            window = Window(hwnd)
            if not self.is_window_manageable(window):
                return True
            if hwnd in self._managed_windows:
                # reuse the previous window object
                manageable_windows.add(self._managed_windows[hwnd])
            else:
                # never seen before, create a new window for it
                manageable_windows.add(window)
            return True
        iter_windows(check_window)
        return manageable_windows

    def is_window_ignored(self, window: Window) -> bool:
        """Check if the window is manageable by the WindowManager"""
        return window.handle == ui.instance.winId() or not self.config.is_window_manageable(window)

    def is_window_manageable(self, window: Window) -> bool:
        """Check if the window is manageable by the WindowManager"""
        return not self.is_window_ignored(window) and is_visible_app_window(window.handle)

    def _put_hide_splash(self):
        self._queue.put_nowait((WinEvent.EVENT_HIDE_SPLASH, None, time.time()))

    def switch_workspace(self, workspace_index: int, monitor_name: str = None, hide_splash_in: Optional[float] = None) -> Callable:
        """Switch to a specific workspace"""
        if monitor_name:
            monitor_state = self.virtdesk_state.get_monitor_state_by_name(monitor_name)
        else:
            monitor_state = self.get_active_monitor_state()
        self._queue.put_nowait((WinEvent.EVENT_SWITCH_WORKSPACE, (monitor_state, workspace_index, hide_splash_in), time.time()))
        return self._put_hide_splash

    def _switch_workspace(self, monitor_state: MonitorState, workspace_index: int, hide_splash_in: Optional[float] = None) -> Callable:
        logger.debug("switch workspace to %d", workspace_index)
        monitor_state.switch_workspace(workspace_index)
        self.save_state()
        ui.show_windows_splash(monitor_state, None)
        if hide_splash_in:
            logger.debug("hide splash in %s", hide_splash_in)
            workers.submit_with_delay(ui.hide_windows_splash, hide_splash_in)

    def move_to_workspace(self, workspace_index: int):
        """Move active window to a specific workspace"""
        window, monitor_state = self.get_active_window()
        if not window:
            return
        self._queue.put_nowait((WinEvent.EVENT_MOVE_TO_WORKSPACE, (monitor_state, window, workspace_index), time.time()))

    def _move_to_workspace(self, monitor_state: MonitorState, window: Window, workspace_index: int):
        monitor_state.move_to_workspace(window, workspace_index)
        self.save_state()

    def unhide_workspaces(self):
        """Unhide all workspaces"""
        for virtdesk_state in self.virtdesk_states.values():
            for monitor_state in virtdesk_state.monitor_states.values():
                logger.info("unhiding monitor %s", monitor_state.monitor)
                monitor_state.unhide_workspaces()

    def save_state(self):
        """Save the windows state"""
        logger.info("saving state")
        with open(DEFAULT_STATE_PATH, "wb") as f:
            pickle.dump(self.virtdesk_states, f)

    def load_state(self):
        """Load the windows state"""
        logger.info("loading state")
        loaded = False
        if os.path.exists(DEFAULT_STATE_PATH):
            with open(DEFAULT_STATE_PATH, "rb") as f:
                try:
                    self.virtdesk_states = pickle.load(f)
                    logger.info("load windows states from the last session")
                except: # pylint: disable=bare-except
                    logger.exception("load windows states error", exc_info=True)
            for virtdesk_state in self.virtdesk_states.values():
                virtdesk_state.update_config(self.config)
            logger.info("recover possibly lost windows")
            for w in windows_might_hidden_by_us():
                if self.is_window_ignored(w):
                    continue
                state = self.virtdesk_state.find_window_in_hidden_workspaces(w.handle)
                if not state:
                    logger.warning("recover possibly lost window %s", w)
                    w.toggle(True)
                else:
                    logger.info("window %s belong to monitor %s workspace #%d", w, state[0], state[1])
            self.sync_windows()
            loaded = True
        else:
            logger.info("nothing from the last session")
        return loaded

    def _winevent_callback(
        self,
        event: WinEvent,
        hwnd: HWND,
        _id_obj: LONG,
        _id_chd: LONG,
        _id_evt_thread: DWORD,
        evt_time: DWORD,
    ):
        self._queue.put_nowait((event, hwnd, time.time()))

    def _screen_changed_callback(self):
        # wait a little bit for monitors to be ready
        self._queue.put_nowait((WinEvent.EVENT_SCREEN_CHANGED, None, time.time()))

    def install_hooks(self):
        """Install hooks for window events"""
        self._hook_ids = [
            hook.hook_winevent(
                WinEvent.EVENT_MIN,
                WinEvent.EVENT_MAX,
                self._winevent_callback,
            ),
        ]

    def uninstall_hooks(self):
        """Uninstall hooks for window events"""
        for hook_id in self._hook_ids:
            hook.unhook_winevent(hook_id)
        self._hook_ids = []
        self.unhide_workspaces()


    def inspect_state(self):
        """Inspect the state of the virtual desktops"""
        inspect_virtdesk_states(self.virtdesk_states)