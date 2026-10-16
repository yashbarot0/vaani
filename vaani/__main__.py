"""vaani daemon | toggle | command | bob | bob-reset | cancel | mode flow|raw | status | quit | bob-helper"""
import os
import socket
import sys
from . import config


def send(args):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    try:
        s.connect(str(config.SOCKET))
    except (FileNotFoundError, ConnectionRefusedError):
        sys.exit("vaani is not running (start it with: systemctl --user start vaani)")
    s.sendall(" ".join(args).encode())
    print(s.recv(1024).decode())


def main():
    args = sys.argv[1:]
    if args == ["daemon"]:
        os.environ["GDK_BACKEND"] = "x11"  # X11 lets the pill be an unfocusable overlay on GNOME
        from .daemon import run
        run()
    elif args == ["bob-helper"]:
        from .bobhelper import main as helper_main  # the online part, run by vaani-bob.service
        helper_main()
    elif args and args[0] in ("toggle", "command", "bob", "bob-reset", "cancel", "mode", "status", "quit"):
        send(args)
    else:
        sys.exit(__doc__)


main()
