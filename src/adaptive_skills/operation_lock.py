from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from .config import Settings


_Result = TypeVar("_Result")
_STATES_GUARD = threading.Lock()
_STATES: dict[Path, tuple[threading.RLock, threading.local]] = {}


def _state_for(path: Path) -> tuple[threading.RLock, threading.local]:
    with _STATES_GUARD:
        return _STATES.setdefault(path, (threading.RLock(), threading.local()))


def _lock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def catalog_operation_lock(settings: Settings) -> Iterator[None]:
    """Serialize catalog/project mutations in this library across threads/processes."""

    settings.ensure()
    lock_path = settings.state_dir / "operations.lock"
    process_lock, local = _state_for(lock_path)
    with process_lock:
        depth = getattr(local, "depth", 0)
        if depth:
            local.depth = depth + 1
            try:
                yield
            finally:
                local.depth -= 1
            return

        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            _lock_file(handle)
            local.depth = 1
            try:
                yield
            finally:
                local.depth = 0
                _unlock_file(handle)


def serialized_catalog_operation(
    function: Callable[..., _Result],
) -> Callable[..., _Result]:
    @wraps(function)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> _Result:
        with catalog_operation_lock(self.settings):
            return function(self, *args, **kwargs)

    return wrapped
