"""shared functions and classes for jmk"""
import logging
import typing
from dataclasses import dataclass
from functools import partial
from threading import Lock

from jigsawwm.w32.vk import * # pylint: disable=unused-wildcard-import,wildcard-import
from jigsawwm import workers
from jigsawwm.w32.sendinput import send_combination

from .core import * # pylint: disable=wildcard-import, unused-wildcard-import


logger = logging.getLogger(__name__)
JmkCombination = typing.Union[typing.List[Vk], str]

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
        self._lock.acquire()
        self.triggerred = True

        def wrapped():
            try:
                release_cb = self.callback()
                if release_cb:
                    self.release_callback = release_cb
            finally:
                self._lock.release()

        workers.submit(wrapped)

    def release(self):
        """Release"""
        if not self.triggerred:
            return
        logger.info("keys released: %s", self.keys)
        self._lock.acquire()
        self.triggerred = False

        def wrapped():
            try:
                if self.release_callback:
                    workers.submit(self.release_callback)
            finally:
                self._lock.release()

        workers.submit(wrapped)


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
