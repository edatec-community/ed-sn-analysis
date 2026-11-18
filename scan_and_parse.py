#!/usr/bin/env python3
"""Wait for one barcode scan, then pass the captured text to parse_order_code.py."""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
from pathlib import Path

IDLE_TIMEOUT_SECONDS = 0.8
PARSER = Path(__file__).resolve().with_name("parse_order_code.py")


def read_terminal_scan() -> str:
    """Capture keyboard-wedge scanner input, including embedded line breaks."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    chunks: list[bytes] = []
    try:
        tty.setraw(fd)
        sys.stderr.write("等待扫码或输入…\n")
        sys.stderr.flush()
        while True:
            timeout = None if not chunks else IDLE_TIMEOUT_SECONDS
            readable, _, _ = select.select([fd], [], [], timeout)
            if not readable:
                break
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            if b"\x03" in chunk:
                raise KeyboardInterrupt
            chunks.append(chunk)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    sys.stderr.write("\n")
    return b"".join(chunks).decode("utf-8", errors="replace")


def read_scan() -> str:
    if sys.stdin.isatty():
        return read_terminal_scan()
    return sys.stdin.read()


def main() -> int:
    args = sys.argv[1:]
    if args not in ([], ["--strict"]):
        print(json.dumps({"error": "usage: scan_and_parse.py [--strict]; scan or pipe the scan text"}, ensure_ascii=False))
        return 2

    try:
        scan_text = read_scan()
        if not scan_text:
            print(json.dumps({"error": "no scan input received"}, ensure_ascii=False))
            return 2
        result = subprocess.run(
            [sys.executable, str(PARSER), *args],
            input=scan_text,
            capture_output=True,
            text=True,
            check=False,
        )
    except KeyboardInterrupt:
        print(json.dumps({"error": "scan cancelled"}, ensure_ascii=False))
        return 130
    except OSError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2

    if result.stderr:
        sys.stderr.write(result.stderr)
    sys.stdout.write(result.stdout)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
