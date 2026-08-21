from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SaveChangesErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SAVE_CHANGES_ERR_OK: _ClassVar[SaveChangesErrorCode]
    SAVE_CHANGES_ERR_GENERIC: _ClassVar[SaveChangesErrorCode]
    SAVE_CHANGES_ERR_NOT_SUPPORTED: _ClassVar[SaveChangesErrorCode]
    SAVE_CHANGES_ERR_NO_SPACE: _ClassVar[SaveChangesErrorCode]

class SetLayerBindingResponse(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SET_LAYER_BINDING_RESP_OK: _ClassVar[SetLayerBindingResponse]
    SET_LAYER_BINDING_RESP_INVALID_LOCATION: _ClassVar[SetLayerBindingResponse]
    SET_LAYER_BINDING_RESP_INVALID_BEHAVIOR: _ClassVar[SetLayerBindingResponse]
    SET_LAYER_BINDING_RESP_INVALID_PARAMETERS: _ClassVar[SetLayerBindingResponse]

class MoveLayerErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MOVE_LAYER_ERR_OK: _ClassVar[MoveLayerErrorCode]
    MOVE_LAYER_ERR_GENERIC: _ClassVar[MoveLayerErrorCode]
    MOVE_LAYER_ERR_INVALID_LAYER: _ClassVar[MoveLayerErrorCode]
    MOVE_LAYER_ERR_INVALID_DESTINATION: _ClassVar[MoveLayerErrorCode]

class AddLayerErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ADD_LAYER_ERR_OK: _ClassVar[AddLayerErrorCode]
    ADD_LAYER_ERR_GENERIC: _ClassVar[AddLayerErrorCode]
    ADD_LAYER_ERR_NO_SPACE: _ClassVar[AddLayerErrorCode]

class RemoveLayerErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REMOVE_LAYER_ERR_OK: _ClassVar[RemoveLayerErrorCode]
    REMOVE_LAYER_ERR_GENERIC: _ClassVar[RemoveLayerErrorCode]
    REMOVE_LAYER_ERR_INVALID_INDEX: _ClassVar[RemoveLayerErrorCode]

class RestoreLayerErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RESTORE_LAYER_ERR_OK: _ClassVar[RestoreLayerErrorCode]
    RESTORE_LAYER_ERR_GENERIC: _ClassVar[RestoreLayerErrorCode]
    RESTORE_LAYER_ERR_INVALID_ID: _ClassVar[RestoreLayerErrorCode]
    RESTORE_LAYER_ERR_INVALID_INDEX: _ClassVar[RestoreLayerErrorCode]

class SetLayerPropsResponse(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SET_LAYER_PROPS_RESP_OK: _ClassVar[SetLayerPropsResponse]
    SET_LAYER_PROPS_RESP_ERR_GENERIC: _ClassVar[SetLayerPropsResponse]
    SET_LAYER_PROPS_RESP_ERR_INVALID_ID: _ClassVar[SetLayerPropsResponse]

class SetActivePhysicalLayoutErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SET_ACTIVE_PHYSICAL_LAYOUT_ERR_OK: _ClassVar[SetActivePhysicalLayoutErrorCode]
    SET_ACTIVE_PHYSICAL_LAYOUT_ERR_GENERIC: _ClassVar[SetActivePhysicalLayoutErrorCode]
    SET_ACTIVE_PHYSICAL_LAYOUT_ERR_INVALID_LAYOUT_INDEX: _ClassVar[SetActivePhysicalLayoutErrorCode]
SAVE_CHANGES_ERR_OK: SaveChangesErrorCode
SAVE_CHANGES_ERR_GENERIC: SaveChangesErrorCode
SAVE_CHANGES_ERR_NOT_SUPPORTED: SaveChangesErrorCode
SAVE_CHANGES_ERR_NO_SPACE: SaveChangesErrorCode
SET_LAYER_BINDING_RESP_OK: SetLayerBindingResponse
SET_LAYER_BINDING_RESP_INVALID_LOCATION: SetLayerBindingResponse
SET_LAYER_BINDING_RESP_INVALID_BEHAVIOR: SetLayerBindingResponse
SET_LAYER_BINDING_RESP_INVALID_PARAMETERS: SetLayerBindingResponse
MOVE_LAYER_ERR_OK: MoveLayerErrorCode
MOVE_LAYER_ERR_GENERIC: MoveLayerErrorCode
MOVE_LAYER_ERR_INVALID_LAYER: MoveLayerErrorCode
MOVE_LAYER_ERR_INVALID_DESTINATION: MoveLayerErrorCode
ADD_LAYER_ERR_OK: AddLayerErrorCode
ADD_LAYER_ERR_GENERIC: AddLayerErrorCode
ADD_LAYER_ERR_NO_SPACE: AddLayerErrorCode
REMOVE_LAYER_ERR_OK: RemoveLayerErrorCode
REMOVE_LAYER_ERR_GENERIC: RemoveLayerErrorCode
REMOVE_LAYER_ERR_INVALID_INDEX: RemoveLayerErrorCode
RESTORE_LAYER_ERR_OK: RestoreLayerErrorCode
RESTORE_LAYER_ERR_GENERIC: RestoreLayerErrorCode
RESTORE_LAYER_ERR_INVALID_ID: RestoreLayerErrorCode
RESTORE_LAYER_ERR_INVALID_INDEX: RestoreLayerErrorCode
SET_LAYER_PROPS_RESP_OK: SetLayerPropsResponse
SET_LAYER_PROPS_RESP_ERR_GENERIC: SetLayerPropsResponse
SET_LAYER_PROPS_RESP_ERR_INVALID_ID: SetLayerPropsResponse
SET_ACTIVE_PHYSICAL_LAYOUT_ERR_OK: SetActivePhysicalLayoutErrorCode
SET_ACTIVE_PHYSICAL_LAYOUT_ERR_GENERIC: SetActivePhysicalLayoutErrorCode
SET_ACTIVE_PHYSICAL_LAYOUT_ERR_INVALID_LAYOUT_INDEX: SetActivePhysicalLayoutErrorCode

class Request(_message.Message):
    __slots__ = ("get_keymap", "set_layer_binding", "check_unsaved_changes", "save_changes", "discard_changes", "get_physical_layouts", "set_active_physical_layout", "move_layer", "add_layer", "remove_layer", "restore_layer", "set_layer_props")
    GET_KEYMAP_FIELD_NUMBER: _ClassVar[int]
    SET_LAYER_BINDING_FIELD_NUMBER: _ClassVar[int]
    CHECK_UNSAVED_CHANGES_FIELD_NUMBER: _ClassVar[int]
    SAVE_CHANGES_FIELD_NUMBER: _ClassVar[int]
    DISCARD_CHANGES_FIELD_NUMBER: _ClassVar[int]
    GET_PHYSICAL_LAYOUTS_FIELD_NUMBER: _ClassVar[int]
    SET_ACTIVE_PHYSICAL_LAYOUT_FIELD_NUMBER: _ClassVar[int]
    MOVE_LAYER_FIELD_NUMBER: _ClassVar[int]
    ADD_LAYER_FIELD_NUMBER: _ClassVar[int]
    REMOVE_LAYER_FIELD_NUMBER: _ClassVar[int]
    RESTORE_LAYER_FIELD_NUMBER: _ClassVar[int]
    SET_LAYER_PROPS_FIELD_NUMBER: _ClassVar[int]
    get_keymap: bool
    set_layer_binding: SetLayerBindingRequest
    check_unsaved_changes: bool
    save_changes: bool
    discard_changes: bool
    get_physical_layouts: bool
    set_active_physical_layout: int
    move_layer: MoveLayerRequest
    add_layer: AddLayerRequest
    remove_layer: RemoveLayerRequest
    restore_layer: RestoreLayerRequest
    set_layer_props: SetLayerPropsRequest
    def __init__(self, get_keymap: _Optional[bool] = ..., set_layer_binding: _Optional[_Union[SetLayerBindingRequest, _Mapping]] = ..., check_unsaved_changes: _Optional[bool] = ..., save_changes: _Optional[bool] = ..., discard_changes: _Optional[bool] = ..., get_physical_layouts: _Optional[bool] = ..., set_active_physical_layout: _Optional[int] = ..., move_layer: _Optional[_Union[MoveLayerRequest, _Mapping]] = ..., add_layer: _Optional[_Union[AddLayerRequest, _Mapping]] = ..., remove_layer: _Optional[_Union[RemoveLayerRequest, _Mapping]] = ..., restore_layer: _Optional[_Union[RestoreLayerRequest, _Mapping]] = ..., set_layer_props: _Optional[_Union[SetLayerPropsRequest, _Mapping]] = ...) -> None: ...

class Response(_message.Message):
    __slots__ = ("get_keymap", "set_layer_binding", "check_unsaved_changes", "save_changes", "discard_changes", "get_physical_layouts", "set_active_physical_layout", "move_layer", "add_layer", "remove_layer", "restore_layer", "set_layer_props")
    GET_KEYMAP_FIELD_NUMBER: _ClassVar[int]
    SET_LAYER_BINDING_FIELD_NUMBER: _ClassVar[int]
    CHECK_UNSAVED_CHANGES_FIELD_NUMBER: _ClassVar[int]
    SAVE_CHANGES_FIELD_NUMBER: _ClassVar[int]
    DISCARD_CHANGES_FIELD_NUMBER: _ClassVar[int]
    GET_PHYSICAL_LAYOUTS_FIELD_NUMBER: _ClassVar[int]
    SET_ACTIVE_PHYSICAL_LAYOUT_FIELD_NUMBER: _ClassVar[int]
    MOVE_LAYER_FIELD_NUMBER: _ClassVar[int]
    ADD_LAYER_FIELD_NUMBER: _ClassVar[int]
    REMOVE_LAYER_FIELD_NUMBER: _ClassVar[int]
    RESTORE_LAYER_FIELD_NUMBER: _ClassVar[int]
    SET_LAYER_PROPS_FIELD_NUMBER: _ClassVar[int]
    get_keymap: Keymap
    set_layer_binding: SetLayerBindingResponse
    check_unsaved_changes: bool
    save_changes: SaveChangesResponse
    discard_changes: bool
    get_physical_layouts: PhysicalLayouts
    set_active_physical_layout: SetActivePhysicalLayoutResponse
    move_layer: MoveLayerResponse
    add_layer: AddLayerResponse
    remove_layer: RemoveLayerResponse
    restore_layer: RestoreLayerResponse
    set_layer_props: SetLayerPropsResponse
    def __init__(self, get_keymap: _Optional[_Union[Keymap, _Mapping]] = ..., set_layer_binding: _Optional[_Union[SetLayerBindingResponse, str]] = ..., check_unsaved_changes: _Optional[bool] = ..., save_changes: _Optional[_Union[SaveChangesResponse, _Mapping]] = ..., discard_changes: _Optional[bool] = ..., get_physical_layouts: _Optional[_Union[PhysicalLayouts, _Mapping]] = ..., set_active_physical_layout: _Optional[_Union[SetActivePhysicalLayoutResponse, _Mapping]] = ..., move_layer: _Optional[_Union[MoveLayerResponse, _Mapping]] = ..., add_layer: _Optional[_Union[AddLayerResponse, _Mapping]] = ..., remove_layer: _Optional[_Union[RemoveLayerResponse, _Mapping]] = ..., restore_layer: _Optional[_Union[RestoreLayerResponse, _Mapping]] = ..., set_layer_props: _Optional[_Union[SetLayerPropsResponse, str]] = ...) -> None: ...

class Notification(_message.Message):
    __slots__ = ("unsaved_changes_status_changed",)
    UNSAVED_CHANGES_STATUS_CHANGED_FIELD_NUMBER: _ClassVar[int]
    unsaved_changes_status_changed: bool
    def __init__(self, unsaved_changes_status_changed: _Optional[bool] = ...) -> None: ...

class SaveChangesResponse(_message.Message):
    __slots__ = ("ok", "err")
    OK_FIELD_NUMBER: _ClassVar[int]
    ERR_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    err: SaveChangesErrorCode
    def __init__(self, ok: _Optional[bool] = ..., err: _Optional[_Union[SaveChangesErrorCode, str]] = ...) -> None: ...

class SetActivePhysicalLayoutResponse(_message.Message):
    __slots__ = ("ok", "err")
    OK_FIELD_NUMBER: _ClassVar[int]
    ERR_FIELD_NUMBER: _ClassVar[int]
    ok: Keymap
    err: SetActivePhysicalLayoutErrorCode
    def __init__(self, ok: _Optional[_Union[Keymap, _Mapping]] = ..., err: _Optional[_Union[SetActivePhysicalLayoutErrorCode, str]] = ...) -> None: ...

class MoveLayerResponse(_message.Message):
    __slots__ = ("ok", "err")
    OK_FIELD_NUMBER: _ClassVar[int]
    ERR_FIELD_NUMBER: _ClassVar[int]
    ok: Keymap
    err: MoveLayerErrorCode
    def __init__(self, ok: _Optional[_Union[Keymap, _Mapping]] = ..., err: _Optional[_Union[MoveLayerErrorCode, str]] = ...) -> None: ...

class AddLayerResponse(_message.Message):
    __slots__ = ("ok", "err")
    OK_FIELD_NUMBER: _ClassVar[int]
    ERR_FIELD_NUMBER: _ClassVar[int]
    ok: AddLayerResponseDetails
    err: AddLayerErrorCode
    def __init__(self, ok: _Optional[_Union[AddLayerResponseDetails, _Mapping]] = ..., err: _Optional[_Union[AddLayerErrorCode, str]] = ...) -> None: ...

class AddLayerResponseDetails(_message.Message):
    __slots__ = ("index", "layer")
    INDEX_FIELD_NUMBER: _ClassVar[int]
    LAYER_FIELD_NUMBER: _ClassVar[int]
    index: int
    layer: Layer
    def __init__(self, index: _Optional[int] = ..., layer: _Optional[_Union[Layer, _Mapping]] = ...) -> None: ...

class RemoveLayerResponse(_message.Message):
    __slots__ = ("ok", "err")
    OK_FIELD_NUMBER: _ClassVar[int]
    ERR_FIELD_NUMBER: _ClassVar[int]
    ok: RemoveLayerOk
    err: RemoveLayerErrorCode
    def __init__(self, ok: _Optional[_Union[RemoveLayerOk, _Mapping]] = ..., err: _Optional[_Union[RemoveLayerErrorCode, str]] = ...) -> None: ...

class RemoveLayerOk(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class RestoreLayerResponse(_message.Message):
    __slots__ = ("ok", "err")
    OK_FIELD_NUMBER: _ClassVar[int]
    ERR_FIELD_NUMBER: _ClassVar[int]
    ok: Layer
    err: RestoreLayerErrorCode
    def __init__(self, ok: _Optional[_Union[Layer, _Mapping]] = ..., err: _Optional[_Union[RestoreLayerErrorCode, str]] = ...) -> None: ...

class SetLayerBindingRequest(_message.Message):
    __slots__ = ("layer_id", "key_position", "binding")
    LAYER_ID_FIELD_NUMBER: _ClassVar[int]
    KEY_POSITION_FIELD_NUMBER: _ClassVar[int]
    BINDING_FIELD_NUMBER: _ClassVar[int]
    layer_id: int
    key_position: int
    binding: BehaviorBinding
    def __init__(self, layer_id: _Optional[int] = ..., key_position: _Optional[int] = ..., binding: _Optional[_Union[BehaviorBinding, _Mapping]] = ...) -> None: ...

class MoveLayerRequest(_message.Message):
    __slots__ = ("start_index", "dest_index")
    START_INDEX_FIELD_NUMBER: _ClassVar[int]
    DEST_INDEX_FIELD_NUMBER: _ClassVar[int]
    start_index: int
    dest_index: int
    def __init__(self, start_index: _Optional[int] = ..., dest_index: _Optional[int] = ...) -> None: ...

class AddLayerRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class RemoveLayerRequest(_message.Message):
    __slots__ = ("layer_index",)
    LAYER_INDEX_FIELD_NUMBER: _ClassVar[int]
    layer_index: int
    def __init__(self, layer_index: _Optional[int] = ...) -> None: ...

class RestoreLayerRequest(_message.Message):
    __slots__ = ("layer_id", "at_index")
    LAYER_ID_FIELD_NUMBER: _ClassVar[int]
    AT_INDEX_FIELD_NUMBER: _ClassVar[int]
    layer_id: int
    at_index: int
    def __init__(self, layer_id: _Optional[int] = ..., at_index: _Optional[int] = ...) -> None: ...

class SetLayerPropsRequest(_message.Message):
    __slots__ = ("layer_id", "name")
    LAYER_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    layer_id: int
    name: str
    def __init__(self, layer_id: _Optional[int] = ..., name: _Optional[str] = ...) -> None: ...

class Keymap(_message.Message):
    __slots__ = ("layers", "available_layers", "max_layer_name_length")
    LAYERS_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_LAYERS_FIELD_NUMBER: _ClassVar[int]
    MAX_LAYER_NAME_LENGTH_FIELD_NUMBER: _ClassVar[int]
    layers: _containers.RepeatedCompositeFieldContainer[Layer]
    available_layers: int
    max_layer_name_length: int
    def __init__(self, layers: _Optional[_Iterable[_Union[Layer, _Mapping]]] = ..., available_layers: _Optional[int] = ..., max_layer_name_length: _Optional[int] = ...) -> None: ...

class Layer(_message.Message):
    __slots__ = ("id", "name", "bindings")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    BINDINGS_FIELD_NUMBER: _ClassVar[int]
    id: int
    name: str
    bindings: _containers.RepeatedCompositeFieldContainer[BehaviorBinding]
    def __init__(self, id: _Optional[int] = ..., name: _Optional[str] = ..., bindings: _Optional[_Iterable[_Union[BehaviorBinding, _Mapping]]] = ...) -> None: ...

class BehaviorBinding(_message.Message):
    __slots__ = ("behavior_id", "param1", "param2")
    BEHAVIOR_ID_FIELD_NUMBER: _ClassVar[int]
    PARAM1_FIELD_NUMBER: _ClassVar[int]
    PARAM2_FIELD_NUMBER: _ClassVar[int]
    behavior_id: int
    param1: int
    param2: int
    def __init__(self, behavior_id: _Optional[int] = ..., param1: _Optional[int] = ..., param2: _Optional[int] = ...) -> None: ...

class PhysicalLayouts(_message.Message):
    __slots__ = ("active_layout_index", "layouts")
    ACTIVE_LAYOUT_INDEX_FIELD_NUMBER: _ClassVar[int]
    LAYOUTS_FIELD_NUMBER: _ClassVar[int]
    active_layout_index: int
    layouts: _containers.RepeatedCompositeFieldContainer[PhysicalLayout]
    def __init__(self, active_layout_index: _Optional[int] = ..., layouts: _Optional[_Iterable[_Union[PhysicalLayout, _Mapping]]] = ...) -> None: ...

class PhysicalLayout(_message.Message):
    __slots__ = ("name", "keys")
    NAME_FIELD_NUMBER: _ClassVar[int]
    KEYS_FIELD_NUMBER: _ClassVar[int]
    name: str
    keys: _containers.RepeatedCompositeFieldContainer[KeyPhysicalAttrs]
    def __init__(self, name: _Optional[str] = ..., keys: _Optional[_Iterable[_Union[KeyPhysicalAttrs, _Mapping]]] = ...) -> None: ...

class KeyPhysicalAttrs(_message.Message):
    __slots__ = ("width", "height", "x", "y", "r", "rx", "ry")
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    R_FIELD_NUMBER: _ClassVar[int]
    RX_FIELD_NUMBER: _ClassVar[int]
    RY_FIELD_NUMBER: _ClassVar[int]
    width: int
    height: int
    x: int
    y: int
    r: int
    rx: int
    ry: int
    def __init__(self, width: _Optional[int] = ..., height: _Optional[int] = ..., x: _Optional[int] = ..., y: _Optional[int] = ..., r: _Optional[int] = ..., rx: _Optional[int] = ..., ry: _Optional[int] = ...) -> None: ...
