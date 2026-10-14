"""Minimal virtual keyboard over /dev/uinput (pure Python; python-evdev needs headers we lack)."""
import fcntl
import os
import struct
import time

KEYS = {"KEY_LEFTCTRL": 29, "KEY_LEFTSHIFT": 42, "KEY_LEFTALT": 56, "KEY_INSERT": 110,
        "KEY_V": 47, "KEY_C": 46}
EV_SYN, EV_KEY, SYN_REPORT = 0, 1, 0
UI_SET_EVBIT, UI_SET_KEYBIT = 0x40045564, 0x40045565
UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405C5503, 0x5501, 0x5502
BUS_VIRTUAL = 0x06


class Keyboard:
    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in KEYS.values():
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        # struct uinput_setup { struct input_id {u16 bustype, vendor, product, version}; char name[80]; u32 ff_effects_max; }
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", BUS_VIRTUAL, 0x1209, 0xD1C7, 1, b"vaani-keyboard", 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.5)  # the compositor needs a moment to pick up a new input device

    def _emit(self, type_, code, value):
        now = time.time()
        os.write(self.fd, struct.pack("llHHi", int(now), int(now % 1 * 1e6), type_, code, value))

    def combo(self, names):
        codes = [KEYS[n] for n in names]
        for c in codes:
            self._emit(EV_KEY, c, 1)
            self._emit(EV_SYN, SYN_REPORT, 0)
            time.sleep(0.01)
        for c in reversed(codes):
            self._emit(EV_KEY, c, 0)
            self._emit(EV_SYN, SYN_REPORT, 0)
            time.sleep(0.01)

    def close(self):
        fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        os.close(self.fd)
