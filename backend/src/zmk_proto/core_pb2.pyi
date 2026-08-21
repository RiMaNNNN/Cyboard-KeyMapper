from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class LockState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ZMK_STUDIO_CORE_LOCK_STATE_LOCKED: _ClassVar[LockState]
    ZMK_STUDIO_CORE_LOCK_STATE_UNLOCKED: _ClassVar[LockState]
ZMK_STUDIO_CORE_LOCK_STATE_LOCKED: LockState
ZMK_STUDIO_CORE_LOCK_STATE_UNLOCKED: LockState

class Request(_message.Message):
    __slots__ = ("get_device_info", "get_lock_state", "lock", "reset_settings")
    GET_DEVICE_INFO_FIELD_NUMBER: _ClassVar[int]
    GET_LOCK_STATE_FIELD_NUMBER: _ClassVar[int]
    LOCK_FIELD_NUMBER: _ClassVar[int]
    RESET_SETTINGS_FIELD_NUMBER: _ClassVar[int]
    get_device_info: bool
    get_lock_state: bool
    lock: bool
    reset_settings: bool
    def __init__(self, get_device_info: _Optional[bool] = ..., get_lock_state: _Optional[bool] = ..., lock: _Optional[bool] = ..., reset_settings: _Optional[bool] = ...) -> None: ...

class Response(_message.Message):
    __slots__ = ("get_device_info", "get_lock_state", "reset_settings")
    GET_DEVICE_INFO_FIELD_NUMBER: _ClassVar[int]
    GET_LOCK_STATE_FIELD_NUMBER: _ClassVar[int]
    RESET_SETTINGS_FIELD_NUMBER: _ClassVar[int]
    get_device_info: GetDeviceInfoResponse
    get_lock_state: LockState
    reset_settings: bool
    def __init__(self, get_device_info: _Optional[_Union[GetDeviceInfoResponse, _Mapping]] = ..., get_lock_state: _Optional[_Union[LockState, str]] = ..., reset_settings: _Optional[bool] = ...) -> None: ...

class GetDeviceInfoResponse(_message.Message):
    __slots__ = ("name", "serial_number")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SERIAL_NUMBER_FIELD_NUMBER: _ClassVar[int]
    name: str
    serial_number: bytes
    def __init__(self, name: _Optional[str] = ..., serial_number: _Optional[bytes] = ...) -> None: ...

class Notification(_message.Message):
    __slots__ = ("lock_state_changed",)
    LOCK_STATE_CHANGED_FIELD_NUMBER: _ClassVar[int]
    lock_state_changed: LockState
    def __init__(self, lock_state_changed: _Optional[_Union[LockState, str]] = ...) -> None: ...
