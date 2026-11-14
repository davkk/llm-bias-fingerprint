#!/usr/bin/env python3
"""Capture a screenshot of a local page through headless Brave over CDP.

This starts its own browser on a free debugging port and a throwaway profile,
so it cannot collide with another tester. It kills only the browser it started.
"""

import argparse
import asyncio
import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def find_browser():
    candidates = [
        os.environ.get("BRAVE_BIN"),
        "/opt/brave.com/brave/brave",
        shutil.which("brave-browser"),
        shutil.which("brave"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
    ]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def get_json(url, timeout=1.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


async def capture(args):
    import websockets

    browser = find_browser()
    if browser is None:
        raise SystemExit("no Brave or Chromium binary found; set BRAVE_BIN")

    port = free_port()
    tmp_root = os.environ.get("VERIFY_TMP", "/tmp/opencode")
    os.makedirs(tmp_root, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="verify-llm-bias-brave-", dir=tmp_root)
    log_path = os.path.join(profile, "brave.log")
    log = open(log_path, "wb")
    proc = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "about:blank",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                log.flush()
                with open(log_path, "rb") as handle:
                    tail = handle.read()[-2000:].decode("utf-8", "replace")
                raise SystemExit(f"browser exited before CDP was ready:\n{tail}")
            try:
                get_json(f"http://127.0.0.1:{port}/json/version")
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.2)
        else:
            raise SystemExit("browser did not expose CDP within 30 seconds")

        targets = get_json(f"http://127.0.0.1:{port}/json/list")
        pages = [target for target in targets if target.get("type") == "page"]
        if not pages:
            raise SystemExit("browser has no page target")
        ws_url = pages[0]["webSocketDebuggerUrl"]

        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            counter = 0

            async def call(method, params=None):
                nonlocal counter
                counter += 1
                message_id = counter
                await ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
                while True:
                    message = json.loads(await ws.recv())
                    if message.get("id") != message_id:
                        continue
                    if "error" in message:
                        raise RuntimeError(f"{method}: {message['error']}")
                    return message.get("result", {})

            await call("Page.enable")
            await call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": args.width,
                    "height": args.height,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await call("Page.navigate", {"url": args.url})
            await asyncio.sleep(args.wait_ms / 1000)

            if args.click:
                clicked = await call(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            "(() => { const el = document.querySelector("
                            f"{json.dumps(args.click)}); if (!el) return false;"
                            " el.click(); return true; })()"
                        ),
                        "returnByValue": True,
                    },
                )
                if not clicked.get("result", {}).get("value"):
                    raise SystemExit(f"selector not found: {args.click}")
                await asyncio.sleep(0.5)

            eval_value = None
            if args.eval:
                evaluated = await call(
                    "Runtime.evaluate",
                    {
                        "expression": args.eval,
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                )
                eval_value = evaluated.get("result", {}).get("value")

            page_info = await call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "({title: document.title,"
                        " status: (document.getElementById('status') || {}).textContent || null,"
                        " rows: document.querySelectorAll('#body tr').length,"
                        " text: (document.body.innerText || '').slice(0, 800)})"
                    ),
                    "returnByValue": True,
                },
            )

            screenshot = await call(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "captureBeyondViewport": args.full_page,
                },
            )
            os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
            with open(args.out, "wb") as handle:
                handle.write(base64.b64decode(screenshot["data"]))

            info = page_info.get("result", {}).get("value") or {}
            print(f"screenshot: {args.out}")
            print(f"title: {info.get('title')}")
            print(f"status: {info.get('status')}")
            print(f"rows: {info.get('rows')}")
            if eval_value is not None:
                print(f"eval: {json.dumps(eval_value, ensure_ascii=False)}")
            text = (info.get("text") or "").replace("\n", " | ")
            print(f"text: {text[:500]}")
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        log.close()
        shutil.rmtree(profile, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("out")
    parser.add_argument("wait_ms", nargs="?", type=int, default=3500)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--click", help="CSS selector to click after the wait")
    parser.add_argument("--eval", dest="eval", help="JavaScript expression to return")
    parser.add_argument("--full-page", action="store_true")
    args = parser.parse_args()
    asyncio.run(capture(args))


if __name__ == "__main__":
    main()
