"""
Standalone, monolithic ``init_core_paths()`` .env → Paths loader.

Dependencies:
    - Python standard library (os, pathlib, types).
    - ``python-dotenv`` (``pip install python-dotenv``) — imported lazily inside
      the function so this file imports cleanly even when dotenv is absent;
      the import failure is only surfaced if the function is actually called.

Purpose
-------
Read a ``.env`` file listing path fragments and return a ``SimpleNamespace``
whose attributes are native ``pathlib.Path`` objects resolved against a chosen
base directory. OS-agnostic: both ``data/logs`` and ``data\\logs`` written in
the ``.env`` normalise to the correct native separator via ``pathlib``.

Example ``.env``::

    DATA_DIR=data
    LOGS_DIR=data/logs
    MODELS_DIR=data/models
    CONFIG_FILE=data/config.yaml

Example usage::

    paths = init_core_paths(env_file=".env", base_dir=".", create_missing=True)
    paths.DATA_DIR       # Path("/abs/project/data")
    paths.LOGS_DIR       # Path("/abs/project/data/logs")
    paths.CONFIG_FILE    # Path("/abs/project/data/config.yaml")

Intended companion::

    from startup_error_log import startup_error_log

    @startup_error_log()
    def load_paths():
        return init_core_paths(env_file=".env", create_missing=True)
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Optional, Union


def init_core_paths(
    env_file: Union[str, Path],
    *,
    base_dir: Optional[Union[str, Path]] = None,
    create_missing: bool = False,
    directory_keys: Optional[Iterable[str]] = None,
    file_keys: Optional[Iterable[str]] = None,
    require_all_exist: bool = True,
) -> SimpleNamespace:
    """Read a ``.env`` file and return a namespace of resolved ``Path`` objects.

    Parameters
    ----------
    env_file :
        Path to the ``.env`` file to load. Either absolute, or relative to the
        current working directory. The file's own parent directory is used as
        the default ``base_dir`` when ``base_dir`` is ``None``.
    base_dir :
        Directory that relative values in the ``.env`` are resolved against.
        If ``None``, defaults to ``Path(env_file).resolve().parent`` — the
        directory containing the ``.env`` file.
    create_missing :
        When ``True``, missing directories are created (``mkdir(parents=True,
        exist_ok=True)``). Has no effect on entries listed in ``file_keys``:
        files are never created by this function, only their parents.
    directory_keys :
        Iterable of ``.env`` keys that must resolve to directories. Used to
        decide what to ``mkdir`` when ``create_missing=True``. If ``None``,
        every key is assumed to be a directory unless it appears in
        ``file_keys``.
    file_keys :
        Iterable of ``.env`` keys that point to files (not directories). When
        ``create_missing=True``, the parent directory of each file is created
        but the file itself is not.
    require_all_exist :
        When ``True`` (the default), the function raises ``FileNotFoundError``
        if any resolved path does not exist after optional creation. Set to
        ``False`` to receive non-existent paths anyway (e.g. for paths that
        will be populated later in the session).

    Returns
    -------
    SimpleNamespace :
        Namespace where each attribute name matches a key from the ``.env``
        and each attribute value is an absolute ``pathlib.Path``. Use via
        attribute access: ``paths.DATA_DIR``, ``paths.LOGS_DIR``, ...

    Raises
    ------
    FileNotFoundError :
        If ``env_file`` itself does not exist, or if ``require_all_exist`` is
        ``True`` and any resolved path is missing after optional creation.
    ImportError :
        If ``python-dotenv`` is not installed.
    RuntimeError :
        If ``create_missing=True`` and a ``mkdir`` call fails.
    """
    # Lazy import so the module is importable even when dotenv is absent;
    # only the call site that actually wants env loading pays the cost.
    try:
        from dotenv import dotenv_values
    except ImportError as e:
        raise ImportError(
            "init_core_paths requires python-dotenv. Install with: pip install python-dotenv"
        ) from e

    env_path = Path(env_file)
    if not env_path.is_absolute():
        env_path = (Path.cwd() / env_path).resolve()
    else:
        env_path = env_path.resolve()

    if not env_path.exists():
        raise FileNotFoundError(f".env file not found at: {env_path}")

    # Anchor for relative entries in the .env. Defaults to the .env's own
    # parent directory so a project that ships an .env next to its root just
    # works without passing base_dir explicitly.
    if base_dir is None:
        root = env_path.parent
    else:
        root = Path(base_dir)
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
        else:
            root = root.resolve()

    values = dotenv_values(env_path)

    dir_key_set = set(directory_keys) if directory_keys is not None else None
    file_key_set = set(file_keys) if file_keys is not None else set()

    resolved: dict[str, Path] = {}
    missing: list[str] = []

    for key, raw in values.items():
        if raw is None or raw == "":
            # An empty value in a .env is almost always a mistake; skip it
            # rather than silently producing Path("") which would resolve to
            # the cwd and hide the problem.
            continue

        # pathlib normalises mixed separators: a value like "data\logs" written
        # on Windows will still behave correctly on Linux, and vice versa,
        # because Path("data\\logs") on POSIX treats the whole string as one
        # segment. To keep .env files portable, operators should use forward
        # slashes; we additionally normalise back-slashes to forward slashes
        # here so mixed-authorship .envs still resolve correctly on POSIX.
        normalised = raw.replace("\\", "/") if os.sep == "/" else raw
        candidate = Path(normalised)

        if candidate.is_absolute():
            resolved_path = candidate.resolve()
        else:
            resolved_path = (root / candidate).resolve()

        is_file = key in file_key_set
        is_directory = (dir_key_set is None and not is_file) or (
            dir_key_set is not None and key in dir_key_set
        )

        if create_missing:
            try:
                if is_directory:
                    resolved_path.mkdir(parents=True, exist_ok=True)
                elif is_file:
                    resolved_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise RuntimeError(
                    f"Failed to create path for '{key}' at {resolved_path}"
                ) from e

        if require_all_exist and not resolved_path.exists():
            missing.append(f"{key} -> {resolved_path}")

        resolved[key] = resolved_path

    if missing:
        raise FileNotFoundError(
            "init_core_paths: the following .env entries did not resolve to "
            "existing paths:\n  " + "\n  ".join(missing)
        )

    return SimpleNamespace(**resolved)
