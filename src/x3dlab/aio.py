"""Run coroutines from plain (sync) notebook code without fighting Jupyter's own event loop.

`run(coro)` hands the coroutine to one long-lived background loop and waits for the result,
so it works in Colab cells, worker threads and scripts alike.
"""

from __future__ import annotations

import asyncio
import threading

_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            t = threading.Thread(target=_loop.run_forever, daemon=True, name="x3dlab-aio")
            t.start()
        return _loop


def run(coro, timeout: float | None = None):
    """Run `coro` on the background loop and return its result (raises its exception)."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and running is _loop:
        raise RuntimeError("aio.run() called from the lab's own loop; await the coroutine instead")
    fut = asyncio.run_coroutine_threadsafe(coro, loop())
    return fut.result(timeout)
