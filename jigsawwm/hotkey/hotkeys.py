from jigsawwm.w32.hook import Hook, KBDLLHOOKMSGID, KBDLLHOOKDATA
from jigsawwm.w32.vk import Vk
from jigsawwm.w32.sendinput import is_synthesized
from typing import Callable, Dict, Sequence, Tuple, FrozenSet, Iterator, Optional
from concurrent.futures import ThreadPoolExecutor
import enum


class Modifier(enum.IntFlag):
    """Keyboard modifier"""

    LCONTROL = enum.auto()
    LMENU = enum.auto()
    LSHIFT = enum.auto()
    LWIN = enum.auto()
    RCONTROL = enum.auto()
    RMENU = enum.auto()
    RSHIFT = enum.auto()
    RWIN = enum.auto()


# { combination: (func, swallow) }
_hotkeys: Dict[FrozenSet[Vk], Tuple[Callable, bool]] = {}
_executor = ThreadPoolExecutor()


def expand_combination(
    combkeys: Sequence[Vk],
    index: Optional[int] = 0,
) -> Iterator[Sequence[Vk]]:
    pass


def hotkey(combkeys: Sequence[Vk] | str, target: Callable | str, swallow: bool = True):
    """Register a system hotkey

    Check `jigsawwm.w32.vk.Vk` for virtual key names

    :param combkeys: Sequence[VirtualKey] | str, virtual keys combination
        example: [Vk.LCONTROL, Vk.LSHIFT, Vk.S] or "LControl+LShift+s"
    :param target: Callable | str, one of the following action would be carried
        out based on the type of the target:

        Callable:   the function would be executed
        str:        the str would be treated as a combination and send accordingly.
                    i.e. "RWin+Space"
        Sequence[str]: the sequence would be treated as key inputs
                    i.e. [ "Alt_Down", "q_Down", "q_Up", "Alt_Up", "h", "e", "l", "l", "o" ]
    :param swallow: stop combination being process by other apps
    """
    global _hotkeys
    combkeys = frozenset(combkeys)
    # check if combination valid
    count = len(list(filter(lambda vk: vk.name not in Modifier.__members__, combkeys)))
    if count != 1:
        raise Exception("require 1 and only 1 triggering key")
    _hotkeys[combkeys] = (target, swallow)


_modifier = Modifier(0)


def _keyboard_proc(msgid: KBDLLHOOKMSGID, msg: KBDLLHOOKDATA) -> bool:
    global _hotkeys, _executor
    # skip key we sent out
    if is_synthesized(msg):
        return False
    global _modifier, _hotkeys
    vkey = Vk(msg.vkCode)
    if vkey.name in Modifier.__members__:
        # update modifier state if
        if msgid == KBDLLHOOKMSGID.WM_KEYDOWN:
            _modifier |= Modifier[vkey.name]
        else:
            _modifier &= ~Modifier[vkey.name]
    elif msgid == KBDLLHOOKMSGID.WM_KEYDOWN:
        # see if combination registered
        combination = frozenset((*map(lambda m: Vk[m.name], _modifier), vkey))
        fs = _hotkeys.get(combination)
        if fs is not None:
            func, swallow = fs
            _executor.submit(func)
            return swallow


hook = Hook(keyboard=_keyboard_proc)
hook.start()
stop_all_hotkeys = hook.stop

if __name__ == "__main__":
    import time
    from functools import partial

    def delay_hello():
        time.sleep(1)
        print("hello world")

    hotkey({Vk.LWIN, Vk.B}, delay_hello, True)

    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            hook.stop()
            break
