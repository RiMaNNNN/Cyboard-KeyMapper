import meta_pb2 as _meta_pb2
import core_pb2 as _core_pb2
import behaviors_pb2 as _behaviors_pb2
import keymap_pb2 as _keymap_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Request(_message.Message):
    __slots__ = ("request_id", "core", "behaviors", "keymap")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CORE_FIELD_NUMBER: _ClassVar[int]
    BEHAVIORS_FIELD_NUMBER: _ClassVar[int]
    KEYMAP_FIELD_NUMBER: _ClassVar[int]
    request_id: int
    core: _core_pb2.Request
    behaviors: _behaviors_pb2.Request
    keymap: _keymap_pb2.Request
    def __init__(self, request_id: _Optional[int] = ..., core: _Optional[_Union[_core_pb2.Request, _Mapping]] = ..., behaviors: _Optional[_Union[_behaviors_pb2.Request, _Mapping]] = ..., keymap: _Optional[_Union[_keymap_pb2.Request, _Mapping]] = ...) -> None: ...

class Response(_message.Message):
    __slots__ = ("request_response", "notification")
    REQUEST_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    NOTIFICATION_FIELD_NUMBER: _ClassVar[int]
    request_response: RequestResponse
    notification: Notification
    def __init__(self, request_response: _Optional[_Union[RequestResponse, _Mapping]] = ..., notification: _Optional[_Union[Notification, _Mapping]] = ...) -> None: ...

class RequestResponse(_message.Message):
    __slots__ = ("request_id", "meta", "core", "behaviors", "keymap")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    META_FIELD_NUMBER: _ClassVar[int]
    CORE_FIELD_NUMBER: _ClassVar[int]
    BEHAVIORS_FIELD_NUMBER: _ClassVar[int]
    KEYMAP_FIELD_NUMBER: _ClassVar[int]
    request_id: int
    meta: _meta_pb2.Response
    core: _core_pb2.Response
    behaviors: _behaviors_pb2.Response
    keymap: _keymap_pb2.Response
    def __init__(self, request_id: _Optional[int] = ..., meta: _Optional[_Union[_meta_pb2.Response, _Mapping]] = ..., core: _Optional[_Union[_core_pb2.Response, _Mapping]] = ..., behaviors: _Optional[_Union[_behaviors_pb2.Response, _Mapping]] = ..., keymap: _Optional[_Union[_keymap_pb2.Response, _Mapping]] = ...) -> None: ...

class Notification(_message.Message):
    __slots__ = ("core", "keymap")
    CORE_FIELD_NUMBER: _ClassVar[int]
    KEYMAP_FIELD_NUMBER: _ClassVar[int]
    core: _core_pb2.Notification
    keymap: _keymap_pb2.Notification
    def __init__(self, core: _Optional[_Union[_core_pb2.Notification, _Mapping]] = ..., keymap: _Optional[_Union[_keymap_pb2.Notification, _Mapping]] = ...) -> None: ...
