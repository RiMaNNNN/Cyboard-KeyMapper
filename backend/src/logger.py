"""
Standalone, monolithic multiprocessing-safe Logger.

Dependencies:
    - Python standard library only (multiprocessing, logging, logging.handlers,
      threading, datetime, pathlib, signal).
    - ``ultralytics`` is NOT imported by this file. The bridge method
      ``bridge_ultralytics_logs`` attaches to the ``ultralytics`` Python logger
      only if ultralytics has been imported elsewhere in the process; if nobody
      has imported it, ``logging.getLogger("ultralytics")`` returns an inert
      logger and the bridge is a harmless no-op.

This file is intentionally a single, monolithic module:
    - One public class (``Logger``).
    - One nested handler class (``Logger._UltralyticsForwardHandler``) that
      forwards records from the Ultralytics logger into the Logger's queue.
    - No private module-level helper functions. Everything the class needs to
      work lives inside the class.

Copy this file into any project to get:
    - A multiprocessing-safe logger with a dedicated daemon listener process
      that serialises writes to a daily log file (UTC-named).
    - Automatic attachment of producer-side handlers in worker processes (the
      ``Logger`` instance pickles cleanly — the listener process handle is
      dropped on pickle so children inherit only the shared queue).
    - Built-in forwarding of Ultralytics ``logging`` records (e.g. BoT-SORT
      tracker warnings) into the same log file.

See :meth:`Logger.init_logger` — the classmethod factory that creates a Logger
and wires the Ultralytics bridge in one call. You can lift that classmethod
into your project's ``services.py`` as a free function and decorate it with
``startup_error_log`` from ``Reusable Services/startup_error_log.py`` so any
failure during construction is persisted to an emergency log before the real
logger is up.
"""

from __future__ import annotations

from pathlib import Path
import logging
import logging.handlers
import multiprocessing as mp
import signal
from datetime import datetime, timezone
from typing import Any, List, Optional


class Logger:
    """Multiprocessing-safe queue logger with a dedicated listener process.

    Goal
    ----
    Multiple processes can call ``logger.info/warning/error`` concurrently while
    a single listener process serialises writes to the current daily log file.

    File naming
    -----------
    ``<log_dir_path>/<UTC-date formatted with log_name_time_format><log_file_suffix>``

    Message formatting
    ------------------
    ``<UTC-timestamp formatted with message_time_format>: [<TYPE centered>]: <message>``

    Usage
    -----
    Main process (recommended)::

        logger = Logger(log_dir_path=Path("./logs"), unit_id="UNIT01")
        logger.info("boot complete")
        ...
        logger.stop()  # optional; daemon listener exits with the process anyway

    One-shot factory with Ultralytics bridge wired up::

        logger = Logger.init_logger(log_dir_path=Path("./logs"), unit_id="UNIT01")

    Worker process::

        # Either pickle the existing ``logger`` to the child (the listener
        # handle is dropped on pickle; the child inherits the shared queue),
        # or build a client-only Logger from the shared queue:
        worker_logger = Logger(
            log_dir_path=Path("./logs"),
            unit_id="UNIT01",
            queue=shared_queue,
            start_listener=False,
        )
    """

    # ------------------------------------------------------------------ #
    # Nested handler: forwards ``ultralytics`` logger records into our   #
    # queue. Nested so this file stays a single class at module scope.   #
    # ------------------------------------------------------------------ #
    class _UltralyticsForwardHandler(logging.Handler):
        """``logging.Handler`` that forwards records into a parent ``Logger``.

        Dedicated class so duplicate-install detection (``bridge_ultralytics_logs``
        is idempotent) is a clean ``isinstance`` check.
        """

        def __init__(self, owner: "Logger", level: int = logging.WARNING) -> None:
            super().__init__(level=level)
            self._owner = owner

        def emit(self, record: logging.LogRecord) -> None:
            try:
                msg = record.getMessage()
            except (TypeError, ValueError):
                return
            prefix = "Ultralytics: "
            if record.levelno >= logging.ERROR:
                self._owner.error(prefix + msg)
            elif record.levelno >= logging.WARNING:
                self._owner.warning(prefix + msg)
            else:
                self._owner.info(prefix + msg)

    # ------------------------------------------------------------------ #
    # Construction                                                        #
    # ------------------------------------------------------------------ #
    def __init__(
        self,
        log_dir_path: Path,
        *,
        unit_id: str = "DEFAULT",
        message_time_format: str = "%Y-%m-%d %H:%M:%S UTC",
        log_name_time_format: str = "%Y%m%d",
        log_file_suffix: str = "_log.txt",
        encoding: str = "utf-8",
        log_types: Optional[List[str]] = None,
        queue: Optional[Any] = None,
        start_listener: bool = True,
    ) -> None:
        """Create a multiprocessing-safe logger.

        Parameters
        ----------
        log_dir_path :
            Directory where daily log files are written. Created on first write
            if it does not yet exist.
        unit_id :
            Short identifier used to namespace the producer-side ``logging``
            client name (``f"Unit{unit_id}.LoggerClient.<id>"``). Different
            ``Logger`` instances get disjoint client names so attaching a new
            ``QueueHandler`` cannot inadvertently reset another instance's
            handlers.
        message_time_format :
            ``strftime`` format for the per-line UTC timestamp prefix.
        log_name_time_format :
            ``strftime`` format for the daily log file's date portion.
        log_file_suffix :
            Suffix appended to the date portion to form the file name, e.g.
            ``"_log.txt"`` yields ``20260423_log.txt``.
        encoding :
            Text encoding of the log file.
        log_types :
            Allowed record type labels (e.g. ``["INFO", "WARNING", "ERROR"]``).
            Levels outside this list are rewritten to ``INFO`` in the prefix so
            unknown level names cannot break the fixed-width centering. If
            ``None``, defaults to ``["INFO", "WARNING", "ERROR"]``.
        queue :
            Optional shared ``multiprocessing.Queue``. Use this to fan-in logs
            from multiple processes into one listener. If ``None``, a new
            unbounded queue is created.
        start_listener :
            When ``True``, auto-starts the listener process — but only if the
            current process is ``MainProcess``. Workers are producer-only by
            design (starting a listener per worker would reintroduce file write
            races).
        """
        self.creation_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.log_dir_path = Path(log_dir_path)
        self.message_time_format = str(message_time_format)
        self.log_name_time_format = str(log_name_time_format)
        self.log_file_suffix = str(log_file_suffix)
        self.encoding = str(encoding)
        self.log_types = list(log_types) if log_types is not None else ["INFO", "WARNING", "ERROR"]
        self.log_type_max_char_length = max((len(t) for t in self.log_types), default=8)

        self.queue: mp.Queue = queue if queue is not None else mp.Queue(-1)
        self._listener: Optional[mp.Process] = None
        self._listener_started: bool = False

        self._client_logger = logging.getLogger(f"Unit{unit_id}.LoggerClient.{id(self)}")
        self._client_logger.setLevel(logging.INFO)
        self._client_logger.propagate = False
        self._reset_client_handlers()

        # Only the main process should spawn the listener automatically.
        if start_listener and (mp.current_process().name == "MainProcess"):
            self.start()

    # ------------------------------------------------------------------ #
    # Classmethod factory: Logger + Ultralytics bridge in one call        #
    # ------------------------------------------------------------------ #
    @classmethod
    def init_logger(
        cls,
        log_dir_path: Path,
        *,
        unit_id: str = "DEFAULT",
        message_time_format: str = "%Y-%m-%d %H:%M:%S UTC",
        log_name_time_format: str = "%Y%m%d",
        log_file_suffix: str = "_log.txt",
        encoding: str = "utf-8",
        log_types: Optional[List[str]] = None,
        queue: Optional[Any] = None,
        start_listener: bool = True,
    ) -> "Logger":
        """Construct a fully-wired ``Logger``.

        Identical to calling ``Logger(...)`` directly, plus a call to
        :meth:`bridge_ultralytics_logs` so any ``ultralytics`` warnings /
        errors emitted during model load or tracking land in the same log
        file as the rest of the application.

        This method is a thin convenience. If you want the decorated-startup
        behaviour (catch exceptions and persist them to a pre-logger emergency
        file before re-raising), lift this method out into a free function in
        your project's ``services.py`` and decorate it with
        ``@startup_error_log()`` from
        ``Reusable Services/startup_error_log.py``::

            # services.py
            from startup_error_log import startup_error_log
            from logger import Logger

            @startup_error_log()
            def init_logger(log_dir_path, **kwargs):
                return Logger.init_logger(log_dir_path, **kwargs)

        The decorator must wrap a free function; it cannot wrap a
        ``@classmethod`` transparently without extra glue, which is why this
        classmethod intentionally does not apply the decorator itself.
        """
        instance = cls(
            log_dir_path=log_dir_path,
            unit_id=unit_id,
            message_time_format=message_time_format,
            log_name_time_format=log_name_time_format,
            log_file_suffix=log_file_suffix,
            encoding=encoding,
            log_types=log_types,
            queue=queue,
            start_listener=start_listener,
        )
        instance.bridge_ultralytics_logs()
        return instance

    # ------------------------------------------------------------------ #
    # Ultralytics bridge                                                  #
    # ------------------------------------------------------------------ #
    def bridge_ultralytics_logs(self) -> None:
        """Forward ``logging.getLogger("ultralytics")`` records into this Logger.

        Captures warnings like "not enough matching points" from
        ``ultralytics.trackers.utils.gmc`` and routes them through the same
        queue / listener / daily-file pipeline as the rest of the app.

        Idempotent — if an ``_UltralyticsForwardHandler`` owned by any
        ``Logger`` instance is already attached, this call is a no-op.
        ``propagate=False`` is set so Ultralytics records never reach the root
        logger (which has no handler in this setup and would silently drop
        them).
        """
        ul_logger = logging.getLogger("ultralytics")

        for h in ul_logger.handlers:
            if isinstance(h, Logger._UltralyticsForwardHandler):
                return

        ul_logger.addHandler(Logger._UltralyticsForwardHandler(self, level=logging.WARNING))
        ul_logger.setLevel(min(ul_logger.level or logging.INFO, logging.WARNING))
        ul_logger.propagate = False

    # ------------------------------------------------------------------ #
    # Producer-side wiring                                                #
    # ------------------------------------------------------------------ #
    def _reset_client_handlers(self) -> None:
        """(Re)configure this process as a log *producer*.

        Attaches a ``QueueHandler`` so ``info/warning/error`` enqueue
        ``LogRecord`` instances instead of writing to the log file directly.
        Safe to call in any process, including workers.
        """
        for handler in list(self._client_logger.handlers):
            self._client_logger.removeHandler(handler)
        self._client_logger.addHandler(logging.handlers.QueueHandler(self.queue))

    # ------------------------------------------------------------------ #
    # Listener process                                                    #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _listener_loop(
        queue: mp.Queue,
        log_dir_path_str: str,
        message_time_format: str,
        log_name_time_format: str,
        log_file_suffix: str,
        encoding: str,
        log_types: List[str],
        log_type_max_char_length: int,
    ) -> None:
        """Listener process entrypoint.

        Runs in a dedicated process. Reads ``LogRecord`` items from the queue,
        formats each into the required line layout, and appends sequentially
        to the current daily log file. Rotates the file handler when the UTC
        date changes. Terminates on a ``None`` sentinel.

        On Windows, Ctrl+C is delivered to every process attached to the
        console; the listener ignores SIGINT so final log lines are flushed
        rather than lost.
        """
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except (ValueError, OSError):
            pass

        log_dir_path = Path(log_dir_path_str)
        log_dir_path.mkdir(parents=True, exist_ok=True)

        class _QueueFileFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
                timestamp = dt.strftime(message_time_format)

                entry_type = (record.levelname or "INFO").upper()
                if log_types and entry_type not in log_types:
                    entry_type = "INFO"

                # Fixed centered width so odd-length labels (WARNING) still
                # align with even-length ones (ERROR/INFO).
                prefix = f"{timestamp}: [{entry_type:^{log_type_max_char_length}}]: "
                return prefix + record.getMessage()

        root = logging.getLogger()
        root.handlers.clear()
        root.propagate = False
        root.setLevel(logging.INFO)

        current_ymd: Optional[str] = None
        handler: Optional[logging.Handler] = None
        formatter = _QueueFileFormatter()

        while True:
            try:
                record = queue.get()
            except KeyboardInterrupt:
                continue
            except (EOFError, OSError):
                break

            if record is None:
                break

            now_dt = datetime.now(timezone.utc)
            ymd = now_dt.strftime(log_name_time_format)

            # Rotate handler when the UTC date changes.
            if handler is None or current_ymd != ymd:
                if handler is not None:
                    root.removeHandler(handler)
                    handler.close()
                    handler = None

                log_file = log_dir_path / f"{ymd}{log_file_suffix}"
                file_handler = logging.FileHandler(log_file, mode="a", encoding=encoding)
                file_handler.setLevel(logging.INFO)
                file_handler.setFormatter(formatter)
                root.addHandler(file_handler)

                handler = file_handler
                current_ymd = ymd

            # A malformed record (e.g. unpicklable args) must not kill the
            # listener. Swallowing is deliberate — the producer is already gone.
            try:
                logging.getLogger(record.name).handle(record)
            except (TypeError, ValueError, OSError):
                continue

        if handler is not None:
            root.removeHandler(handler)
            handler.close()

    def start(self) -> None:
        """Start the listener process (main process only).

        Usually called by ``__init__`` automatically when ``start_listener=True``
        in the main process. In worker processes this is a no-op by design:
        starting multiple listeners would reintroduce file write races.
        """
        if self._listener_started:
            return
        if mp.current_process().name != "MainProcess":
            return

        self._listener = mp.Process(
            target=Logger._listener_loop,
            name="LogListener",
            args=(
                self.queue,
                str(self.log_dir_path),
                self.message_time_format,
                self.log_name_time_format,
                self.log_file_suffix,
                self.encoding,
                self.log_types,
                self.log_type_max_char_length,
            ),
        )
        self._listener.daemon = True
        self._listener.start()
        self._listener_started = True

    def stop(self, timeout_s: Optional[float] = 5.0) -> None:
        """Stop the listener process gracefully.

        Sends the ``None`` sentinel, joins with ``timeout_s``, and terminates
        the listener as a last resort if it's still alive after the join. Also
        closes the producer-side queue feeder thread so outstanding records
        are flushed.
        """
        if not self._listener_started or self._listener is None:
            return

        try:
            self.queue.put(None)
        except (ValueError, OSError):
            self._listener_started = False
            self._listener = None
            return

        try:
            self._listener.join(timeout=timeout_s)
        except KeyboardInterrupt:
            self._listener.join(timeout=timeout_s)

        if self._listener.is_alive():
            self._listener.terminate()
            self._listener.join(timeout=1.0)

        self.queue.close()
        self.queue.join_thread()

        self._listener_started = False
        self._listener = None

    # ------------------------------------------------------------------ #
    # Producer-side log methods                                           #
    # ------------------------------------------------------------------ #
    def error(self, message: str) -> None:
        """Log an ERROR line. Safe from any process."""
        self._client_logger.error("" if message is None else str(message))

    def warning(self, message: str) -> None:
        """Log a WARNING line. Safe from any process."""
        self._client_logger.warning("" if message is None else str(message))

    def info(self, message: str) -> None:
        """Log an INFO line. Safe from any process."""
        self._client_logger.info("" if message is None else str(message))

    # ------------------------------------------------------------------ #
    # Pickle protocol                                                     #
    # ------------------------------------------------------------------ #
    def __getstate__(self) -> dict:
        """Return pickle-safe state for worker processes.

        A live ``mp.Process`` instance is not pickleable, so we drop the
        listener process handle. In workers the unpickled ``Logger`` remains a
        producer (it keeps the shared queue and will enqueue log records) but
        it will not manage a listener — only the main-process owner does.
        """
        state = dict(self.__dict__)
        state["_listener"] = None
        state["_listener_started"] = False
        return state
