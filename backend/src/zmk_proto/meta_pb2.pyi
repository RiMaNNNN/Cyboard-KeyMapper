from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ErrorConditions(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    GENERIC: _ClassVar[ErrorConditions]
    UNLOCK_REQUIRED: _ClassVar[ErrorConditions]
    RPC_NOT_FOUND: _ClassVar[ErrorConditions]
    MSG_DECODE_FAILED: _ClassVar[ErrorConditions]
    MSG_ENCODE_FAILED: _ClassVar[ErrorConditions]
GENERIC: ErrorConditions
UNLOCK_REQUIRED: ErrorConditions
RPC_NOT_FOUND: ErrorConditions
MSG_DECODE_FAILED: ErrorConditions
MSG_ENCODE_FAILED: ErrorConditions

class Response(_message.Message):
    __slots__ = ("no_response", "simple_error")
    NO_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    SIMPLE_ERROR_FIELD_NUMBER: _ClassVar[int]
    no_response: bool
    simple_error: ErrorConditions
    def __init__(self, no_response: _Optional[bool] = ..., simple_error: _Optional[_Union[ErrorConditions, str]] = ...) -> None: ...
