"""shared functions and classes for jmk"""
import logging
import typing
import time
from dataclasses import dataclass, field
from functools import partial
from threading import Lock

from jigsawwm.w32.vk import * # pylint: disable=unused-wildcard-import,wildcard-import
from jigsawwm.w32.sendinput import send_combination



logger = logging.getLogger(__name__)
JmkCombination = typing.Union[typing.List[Vk], str]

@dataclass
class JmkEvent:
    """A jmk event that contains the key/button, pressed state,
    system state(does it came from the OS) and extra data"""

    vk: Vk
    pressed: bool
    system: bool = False
    flags: int = 0
    extra: int = 0
    time: float = field(default_factory=time.time)

    def __repr__(self) -> str:
        evt = 'down' if self.pressed else 'up'
        src = 'sys' if self.system else 'sim'
        return f"JmkEvent({self.vk.name}, {evt}, {src}, {self.flags}, {self.extra})"


JmkHandler = typing.Callable[[JmkEvent], bool]

@dataclass
class JmkTrigger:
    """Key trigger"""
    keys: typing.Iterable[Vk]
    callback: typing.Callable
    release_callback: typing.Callable = None
    triggerred: bool = False
    lit_keys: typing.Set[Vk] = None
    first_lit_at: float = None
    _lock: Lock = field(default_factory=Lock)

    def trigger(self):
        """Trigger"""
        logger.info("keys triggered: %s", self.keys)
        self.triggerred = True
        release_cb = self.callback()
        if release_cb:
            self.release_callback = release_cb

    def release(self):
        """Release"""
        if not self.triggerred:
            return
        logger.info("keys released: %s", self.keys)
        if not self.triggerred:
            return
        self.triggerred = False
        if self.release_callback:
            self.release_callback()

class JmkTriggers(JmkHandler):
    """A handler that handles triggers."""

    next_handler: JmkHandler
    triggers: typing.Dict[typing.FrozenSet[Vk], JmkTrigger]

    def __init__(
        self,
        next_handler,
        triggers: typing.List[typing.Tuple[JmkCombination, typing.Callable, typing.Optional[typing.Callable]]] = None,
    ):
        super().__init__()
        self.next_handler = next_handler
        self.triggers = {}
        if triggers:
            for args in triggers:
                self.register(*args)

    def check_comb(self, comb: typing.List[Vk]):
        """Check if a combination is valid."""

    def expand_comb(self, comb: JmkCombination) -> typing.List[typing.List[Vk]]:
        """Expand a combination to a list of combinations."""
        if isinstance(comb, str):
            comb = parse_combination(comb)
        self.check_comb(comb)
        return expand_combination(comb)

    def register(
        self, comb: JmkCombination, cb: typing.Union[typing.Callable, str], release_cb: typing.Callable = None
    ):
        """Register a trigger."""
        if isinstance(cb, str):
            new_comb = parse_combination(cb)
            cb = partial(send_combination, *new_comb)
        for keys in self.expand_comb(comb):
            if frozenset(keys) in self.triggers:
                raise ValueError(f"hotkey {keys} already registered")
            trigger = JmkTrigger(keys, cb, release_cb)
            self.triggers[frozenset(keys)] = trigger

    def unregister(self, comb: JmkCombination):
        """Unregister a hotkey."""
        for keys in self.expand_comb(comb):
            self.triggers.pop(frozenset(keys))

    def __call__(self, evt: JmkEvent) -> bool:
        return self.next_handler(evt)
