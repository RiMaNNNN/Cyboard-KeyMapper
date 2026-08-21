"""
Standalone, monolithic ``startup_error_log`` decorator.

Dependencies:
    - Python standard library only (functools, pathlib, datetime, traceback).

Purpose
-------
A decorator factory for startup-time functions that run BEFORE a real logger
has been initialised. If the decorated function raises, the exception is
captured and written to a daily emergency log file on disk, then re-raised so
the caller can decide what to do (usually let the process exit loudly).

Typical use
-----------
Wrap any function that must succeed for the process to boot — clock sync,
config loading, path setup, Logger construction::

    from startup_error_log import startup_error_log

    @startup_error_log()
    def init_logger(log_dir):
        ...

    @startup_error_log(log_dir=Path("./my_app/logs"))
    def init_core_paths(env_file):
        ...

Traceback propagation
---------------------
The decorator re-raises the ORIGINAL exception so the traceback is preserved.
Inside the decorated function, follow these conventions so ``from e`` chains
correctly:

    * When using ``try/except``, do ``raise RuntimeError(...) from e`` so the
      original cause is preserved in the chain.
    * When not using ``try/except``, a raw ``raise`` inside the function
      propagates naturally with the current line's traceback.
"""

from __future__ import annotations

import functools
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


def startup_error_log(
    log_dir: Optional[Path] = None,
    *,
    file_date_format: str = "%Y%m%d",
    file_suffix: str = "_log.txt",
    line_time_format: str = "%H:%M:%SUTC",
    encoding: str = "utf-8",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Build the startup-error decorator.

    Parameters
    ----------
    log_dir :
        Directory to write the emergency log file into. If ``None``, defaults
        to ``<current working directory>/logs``. The directory is created if
        missing on the first failure (not at import time — so a successful
        startup leaves no empty ``logs/`` folder behind).
    file_date_format :
        ``strftime`` format for the daily file-name date portion.
    file_suffix :
        Suffix appended to the date portion to form the file name
        (``20260423_log.txt`` by default).
    line_time_format :
        ``strftime`` format for the per-line UTC timestamp prefix.
    encoding :
        Text encoding of the emergency log file.

    Returns
    -------
    A decorator that wraps a function so that any exception it raises is
    persisted to the emergency log file, then re-raised with its original
    traceback preserved.

    Raises (from the wrapper, when the decorated function has already failed)
    ------------------------------------------------------------------------
    RuntimeError :
        Raised only when the decorated function raised AND writing the
        emergency log also failed (e.g. disk full, permission denied on the
        log directory). The new ``RuntimeError`` is chained from the original
        exception via ``from e`` so the root cause is not lost.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                emergency_log_path = log_dir if log_dir is not None else Path.cwd() / "logs"
                log_filename = datetime.now(timezone.utc).strftime(file_date_format) + file_suffix
                error = f"Startup Error: {e}: {traceback.format_exc()}"
                try:
                    emergency_log_path.mkdir(parents=True, exist_ok=True)
                    with open(emergency_log_path / log_filename, "a", encoding=encoding) as log_file:
                        log_file.write(
                            f"{datetime.now(timezone.utc).strftime(line_time_format)}: "
                            f"[ ERROR ]: {error}\n"
                        )
                except Exception as exc:
                    raise RuntimeError(
                        f"Logging failed to write startup error log: {exc}: {traceback.format_exc()}: "
                    ) from e
                raise  # Re-raise the original exception so the caller still sees it.

        return wrapper

    return decorator
