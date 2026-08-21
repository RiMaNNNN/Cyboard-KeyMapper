"""Generated protobuf bindings for the ZMK Studio RPC protocol.

Generated from zmkfirmware/zmk-studio-messages (see ``backend/proto/PINNED_COMMIT.txt``)
with ``grpc_tools.protoc``. The upstream ``.proto`` files import each other by bare
filename (``import "meta.proto"``), so protoc emits absolute imports such as
``import meta_pb2``. This package prepends its own directory to ``sys.path`` so those
generated absolute imports resolve when the modules are used as package submodules
(``from zmk_proto import studio_pb2``).

Regenerate after changing the pinned protos::

    python -m grpc_tools.protoc --proto_path=backend/proto/zmk \
        --python_out=backend/src/zmk_proto --pyi_out=backend/src/zmk_proto \
        studio.proto core.proto keymap.proto behaviors.proto meta.proto
"""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_DIR: str = str(Path(__file__).resolve().parent)
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)

from zmk_proto import behaviors_pb2, core_pb2, keymap_pb2, meta_pb2, studio_pb2  # noqa: E402

__all__ = ["behaviors_pb2", "core_pb2", "keymap_pb2", "meta_pb2", "studio_pb2"]
