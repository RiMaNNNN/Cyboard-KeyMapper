from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Request(_message.Message):
    __slots__ = ("list_all_behaviors", "get_behavior_details")
    LIST_ALL_BEHAVIORS_FIELD_NUMBER: _ClassVar[int]
    GET_BEHAVIOR_DETAILS_FIELD_NUMBER: _ClassVar[int]
    list_all_behaviors: bool
    get_behavior_details: GetBehaviorDetailsRequest
    def __init__(self, list_all_behaviors: _Optional[bool] = ..., get_behavior_details: _Optional[_Union[GetBehaviorDetailsRequest, _Mapping]] = ...) -> None: ...

class GetBehaviorDetailsRequest(_message.Message):
    __slots__ = ("behavior_id",)
    BEHAVIOR_ID_FIELD_NUMBER: _ClassVar[int]
    behavior_id: int
    def __init__(self, behavior_id: _Optional[int] = ...) -> None: ...

class Response(_message.Message):
    __slots__ = ("list_all_behaviors", "get_behavior_details")
    LIST_ALL_BEHAVIORS_FIELD_NUMBER: _ClassVar[int]
    GET_BEHAVIOR_DETAILS_FIELD_NUMBER: _ClassVar[int]
    list_all_behaviors: ListAllBehaviorsResponse
    get_behavior_details: GetBehaviorDetailsResponse
    def __init__(self, list_all_behaviors: _Optional[_Union[ListAllBehaviorsResponse, _Mapping]] = ..., get_behavior_details: _Optional[_Union[GetBehaviorDetailsResponse, _Mapping]] = ...) -> None: ...

class ListAllBehaviorsResponse(_message.Message):
    __slots__ = ("behaviors",)
    BEHAVIORS_FIELD_NUMBER: _ClassVar[int]
    behaviors: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, behaviors: _Optional[_Iterable[int]] = ...) -> None: ...

class GetBehaviorDetailsResponse(_message.Message):
    __slots__ = ("id", "display_name", "metadata")
    ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    id: int
    display_name: str
    metadata: _containers.RepeatedCompositeFieldContainer[BehaviorBindingParametersSet]
    def __init__(self, id: _Optional[int] = ..., display_name: _Optional[str] = ..., metadata: _Optional[_Iterable[_Union[BehaviorBindingParametersSet, _Mapping]]] = ...) -> None: ...

class BehaviorBindingParametersSet(_message.Message):
    __slots__ = ("param1", "param2")
    PARAM1_FIELD_NUMBER: _ClassVar[int]
    PARAM2_FIELD_NUMBER: _ClassVar[int]
    param1: _containers.RepeatedCompositeFieldContainer[BehaviorParameterValueDescription]
    param2: _containers.RepeatedCompositeFieldContainer[BehaviorParameterValueDescription]
    def __init__(self, param1: _Optional[_Iterable[_Union[BehaviorParameterValueDescription, _Mapping]]] = ..., param2: _Optional[_Iterable[_Union[BehaviorParameterValueDescription, _Mapping]]] = ...) -> None: ...

class BehaviorParameterValueDescriptionRange(_message.Message):
    __slots__ = ("min", "max")
    MIN_FIELD_NUMBER: _ClassVar[int]
    MAX_FIELD_NUMBER: _ClassVar[int]
    min: int
    max: int
    def __init__(self, min: _Optional[int] = ..., max: _Optional[int] = ...) -> None: ...

class BehaviorParameterNil(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class BehaviorParameterLayerId(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class BehaviorParameterHidUsage(_message.Message):
    __slots__ = ("keyboard_max", "consumer_max")
    KEYBOARD_MAX_FIELD_NUMBER: _ClassVar[int]
    CONSUMER_MAX_FIELD_NUMBER: _ClassVar[int]
    keyboard_max: int
    consumer_max: int
    def __init__(self, keyboard_max: _Optional[int] = ..., consumer_max: _Optional[int] = ...) -> None: ...

class BehaviorParameterValueDescription(_message.Message):
    __slots__ = ("name", "nil", "constant", "range", "hid_usage", "layer_id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    NIL_FIELD_NUMBER: _ClassVar[int]
    CONSTANT_FIELD_NUMBER: _ClassVar[int]
    RANGE_FIELD_NUMBER: _ClassVar[int]
    HID_USAGE_FIELD_NUMBER: _ClassVar[int]
    LAYER_ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    nil: BehaviorParameterNil
    constant: int
    range: BehaviorParameterValueDescriptionRange
    hid_usage: BehaviorParameterHidUsage
    layer_id: BehaviorParameterLayerId
    def __init__(self, name: _Optional[str] = ..., nil: _Optional[_Union[BehaviorParameterNil, _Mapping]] = ..., constant: _Optional[int] = ..., range: _Optional[_Union[BehaviorParameterValueDescriptionRange, _Mapping]] = ..., hid_usage: _Optional[_Union[BehaviorParameterHidUsage, _Mapping]] = ..., layer_id: _Optional[_Union[BehaviorParameterLayerId, _Mapping]] = ...) -> None: ...
