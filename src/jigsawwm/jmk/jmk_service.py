"""Jmk Service"""

from datetime import datetime

from jigsawwm.app.job import Service
from jigsawwm.w32.sendinput import Vk, send_combination, send_text


class Sysout:
    def __init__(self):
        self.callbacks = set()
        self.state = {}

class Hotkeys:
    def register_triggers(self, hotkeys):
        pass

    def unregister(self, hotkey):
        pass


class JmkService(Service):
    """JMK service"""

    name = "jmk"

    def __init__(self):
        self.sysout = Sysout()
        self.hotkeys = Hotkeys()
        # self.sysin = SystemInput()
        # self.core = JmkCore()
        # self.hotkeys = JmkHotkeys()
        # self.sysout = SystemOutput()
        # self.sysin.pipe(self.core).pipe(self.hotkeys).pipe(self.sysout)
        # self.sysin.next_handler_when_disabled = self.sysout
        # self.sysin.start()
        self._running = False

    def start(self):
        # self.sysin.is_running = True
        self._running = True

    def stop(self):
        # self.sysin.is_running = False
        self._running = False

    @property
    def is_running(self):
        return self._running

    def shutdown(self):
        self.stop()


def send_today():
    """Send today's date as text input, e.g.: 2024-09-12"""
    send_text(datetime.now().strftime("%Y-%m-%d"))


def send_today_compact():
    """Send today's date in compact form as text input, e.g.: 20240912"""
    send_text(datetime.now().strftime("%Y-%m-%d"))


def send_now():
    """Send current date and time as text input, e.g.: 2024-09-12 13:53:03"""
    send_text(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def send_now_compact():
    """Send current date and time in compact form as text input, e.g.: 20240912135303"""
    send_text(datetime.now().strftime("%Y%m%d%H%M%S"))


def ctrl_w():
    """Send Ctrl+w"""
    send_combination(Vk.LCONTROL, Vk.W)


def ctrl_shift_w():
    """Send Ctrl+w"""
    send_combination(Vk.LCONTROL, Vk.SHIFT, Vk.W)
