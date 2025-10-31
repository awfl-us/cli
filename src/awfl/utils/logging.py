import hashlib
import sys
import termios
import tty
import os
from typing import Optional
from prompt_toolkit.shortcuts import print_formatted_text

# Global log state
log_lines = []
_last_hash: Optional[str] = None

# Abort flag controlled by listen_for_escape()
abort_requested: bool = False


def _is_debug() -> bool:
    v = os.getenv("AWFL_DEBUG", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def log_unique(text):
    global _last_hash
    # Ensure string and make it safe for hashing/printing even with surrogate code points
    if not isinstance(text, str):
        text = str(text)
    safe_text = text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')

    h = hashlib.sha1(safe_text.encode('utf-8')).hexdigest()
    if h != _last_hash:
        log_lines.append(safe_text + "\n")
        if len(log_lines) > 20:
            log_lines.pop(0)
        _last_hash = h

        try:
            print_formatted_text(safe_text + "\n")
        except Exception:
            try:
                print(safe_text + "\n", flush=True)
            except Exception:
                pass


def listen_for_escape():
    global abort_requested
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == '\x1b':  # ESC key
                abort_requested = True
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def reset_abort_requested():
    global abort_requested
    abort_requested = False


def set_terminal_title(title: str):
    """Set the terminal/tab/window title using OSC escape sequences.
    Works in macOS Terminal, iTerm2, tmux/screen (propagated), and most xterm-compatible terminals.
    No-ops if stdout is not a TTY.
    """
    try:
        if not sys.stdout.isatty():
            return
    except Exception:
        # If we cannot determine TTY status, attempt anyway
        pass

    # OSC sequences: 0 = icon+title, 2 = title
    seq0 = f"\033]0;{title}\007"
    seq2 = f"\033]2;{title}\007"
    try:
        sys.stdout.write(seq0)
        sys.stdout.write(seq2)
        sys.stdout.flush()
    except Exception:
        # Silently ignore failures
        pass


__all__ = [
    "log_lines",
    "log_unique",
    "listen_for_escape",
    "reset_abort_requested",
    "set_terminal_title",
    "_is_debug",
]
