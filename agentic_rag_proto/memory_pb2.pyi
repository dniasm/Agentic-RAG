from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class FactGroup(_message.Message):
    __slots__ = ("memory_key", "values", "updated_at")
    MEMORY_KEY_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    memory_key: str
    values: _containers.RepeatedScalarFieldContainer[str]
    updated_at: str
    def __init__(self, memory_key: _Optional[str] = ..., values: _Optional[_Iterable[str]] = ..., updated_at: _Optional[str] = ...) -> None: ...

class ListFactsRequest(_message.Message):
    __slots__ = ("user_id",)
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    def __init__(self, user_id: _Optional[str] = ...) -> None: ...

class ListFactsReply(_message.Message):
    __slots__ = ("groups",)
    GROUPS_FIELD_NUMBER: _ClassVar[int]
    groups: _containers.RepeatedCompositeFieldContainer[FactGroup]
    def __init__(self, groups: _Optional[_Iterable[_Union[FactGroup, _Mapping]]] = ...) -> None: ...

class FactRequest(_message.Message):
    __slots__ = ("user_id", "memory_key", "value")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    MEMORY_KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    memory_key: str
    value: str
    def __init__(self, user_id: _Optional[str] = ..., memory_key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class FactReply(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class DeleteFactRequest(_message.Message):
    __slots__ = ("user_id", "memory_key", "value")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    MEMORY_KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    memory_key: str
    value: str
    def __init__(self, user_id: _Optional[str] = ..., memory_key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class DeleteFactReply(_message.Message):
    __slots__ = ("deleted",)
    DELETED_FIELD_NUMBER: _ClassVar[int]
    deleted: int
    def __init__(self, deleted: _Optional[int] = ...) -> None: ...

class StoreEpisodeRequest(_message.Message):
    __slots__ = ("user_id", "text", "kind", "conversation_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    text: str
    kind: str
    conversation_id: str
    def __init__(self, user_id: _Optional[str] = ..., text: _Optional[str] = ..., kind: _Optional[str] = ..., conversation_id: _Optional[str] = ...) -> None: ...

class StoreEpisodeReply(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class SearchEpisodesRequest(_message.Message):
    __slots__ = ("user_id", "query", "limit", "score_threshold")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    SCORE_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    query: str
    limit: int
    score_threshold: float
    def __init__(self, user_id: _Optional[str] = ..., query: _Optional[str] = ..., limit: _Optional[int] = ..., score_threshold: _Optional[float] = ...) -> None: ...

class Episode(_message.Message):
    __slots__ = ("id", "text", "kind", "conversation_id", "updated_at", "score")
    ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    id: str
    text: str
    kind: str
    conversation_id: str
    updated_at: str
    score: float
    def __init__(self, id: _Optional[str] = ..., text: _Optional[str] = ..., kind: _Optional[str] = ..., conversation_id: _Optional[str] = ..., updated_at: _Optional[str] = ..., score: _Optional[float] = ...) -> None: ...

class SearchEpisodesReply(_message.Message):
    __slots__ = ("episodes",)
    EPISODES_FIELD_NUMBER: _ClassVar[int]
    episodes: _containers.RepeatedCompositeFieldContainer[Episode]
    def __init__(self, episodes: _Optional[_Iterable[_Union[Episode, _Mapping]]] = ...) -> None: ...

class DeleteConversationEpisodesRequest(_message.Message):
    __slots__ = ("user_id", "conversation_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    conversation_id: str
    def __init__(self, user_id: _Optional[str] = ..., conversation_id: _Optional[str] = ...) -> None: ...

class DeleteConversationEpisodesReply(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ImportedFact(_message.Message):
    __slots__ = ("id", "user_id", "memory_key", "value", "created_at", "updated_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    MEMORY_KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    user_id: str
    memory_key: str
    value: str
    created_at: str
    updated_at: str
    def __init__(self, id: _Optional[str] = ..., user_id: _Optional[str] = ..., memory_key: _Optional[str] = ..., value: _Optional[str] = ..., created_at: _Optional[str] = ..., updated_at: _Optional[str] = ...) -> None: ...

class ImportFactsRequest(_message.Message):
    __slots__ = ("facts",)
    FACTS_FIELD_NUMBER: _ClassVar[int]
    facts: _containers.RepeatedCompositeFieldContainer[ImportedFact]
    def __init__(self, facts: _Optional[_Iterable[_Union[ImportedFact, _Mapping]]] = ...) -> None: ...

class ImportedEpisode(_message.Message):
    __slots__ = ("id", "vector", "user_id", "text", "kind", "conversation_id", "updated_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    VECTOR_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    vector: _containers.RepeatedScalarFieldContainer[float]
    user_id: str
    text: str
    kind: str
    conversation_id: str
    updated_at: str
    def __init__(self, id: _Optional[str] = ..., vector: _Optional[_Iterable[float]] = ..., user_id: _Optional[str] = ..., text: _Optional[str] = ..., kind: _Optional[str] = ..., conversation_id: _Optional[str] = ..., updated_at: _Optional[str] = ...) -> None: ...

class ImportEpisodesRequest(_message.Message):
    __slots__ = ("episodes",)
    EPISODES_FIELD_NUMBER: _ClassVar[int]
    episodes: _containers.RepeatedCompositeFieldContainer[ImportedEpisode]
    def __init__(self, episodes: _Optional[_Iterable[_Union[ImportedEpisode, _Mapping]]] = ...) -> None: ...

class ImportReply(_message.Message):
    __slots__ = ("created",)
    CREATED_FIELD_NUMBER: _ClassVar[int]
    created: int
    def __init__(self, created: _Optional[int] = ...) -> None: ...

class CountMemoryRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class CountMemoryReply(_message.Message):
    __slots__ = ("facts", "episodes")
    FACTS_FIELD_NUMBER: _ClassVar[int]
    EPISODES_FIELD_NUMBER: _ClassVar[int]
    facts: int
    episodes: int
    def __init__(self, facts: _Optional[int] = ..., episodes: _Optional[int] = ...) -> None: ...
