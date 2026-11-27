from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Conversation(_message.Message):
    __slots__ = ("id", "user_id", "title", "created_at", "updated_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str
    def __init__(self, id: _Optional[str] = ..., user_id: _Optional[str] = ..., title: _Optional[str] = ..., created_at: _Optional[str] = ..., updated_at: _Optional[str] = ...) -> None: ...

class Message(_message.Message):
    __slots__ = ("id", "conversation_id", "role", "content", "tool", "meta_json", "created_at", "latency_ms", "rewritten", "sufficient")
    ID_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    META_JSON_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    LATENCY_MS_FIELD_NUMBER: _ClassVar[int]
    REWRITTEN_FIELD_NUMBER: _ClassVar[int]
    SUFFICIENT_FIELD_NUMBER: _ClassVar[int]
    id: str
    conversation_id: str
    role: str
    content: str
    tool: str
    meta_json: str
    created_at: str
    latency_ms: int
    rewritten: bool
    sufficient: bool
    def __init__(self, id: _Optional[str] = ..., conversation_id: _Optional[str] = ..., role: _Optional[str] = ..., content: _Optional[str] = ..., tool: _Optional[str] = ..., meta_json: _Optional[str] = ..., created_at: _Optional[str] = ..., latency_ms: _Optional[int] = ..., rewritten: _Optional[bool] = ..., sufficient: _Optional[bool] = ...) -> None: ...

class CreateConversationRequest(_message.Message):
    __slots__ = ("user_id", "title")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    title: str
    def __init__(self, user_id: _Optional[str] = ..., title: _Optional[str] = ...) -> None: ...

class ConversationRef(_message.Message):
    __slots__ = ("user_id", "conversation_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    conversation_id: str
    def __init__(self, user_id: _Optional[str] = ..., conversation_id: _Optional[str] = ...) -> None: ...

class ListConversationsRequest(_message.Message):
    __slots__ = ("user_id",)
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    def __init__(self, user_id: _Optional[str] = ...) -> None: ...

class ListConversationsReply(_message.Message):
    __slots__ = ("conversations",)
    CONVERSATIONS_FIELD_NUMBER: _ClassVar[int]
    conversations: _containers.RepeatedCompositeFieldContainer[Conversation]
    def __init__(self, conversations: _Optional[_Iterable[_Union[Conversation, _Mapping]]] = ...) -> None: ...

class RenameConversationRequest(_message.Message):
    __slots__ = ("user_id", "conversation_id", "title")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    conversation_id: str
    title: str
    def __init__(self, user_id: _Optional[str] = ..., conversation_id: _Optional[str] = ..., title: _Optional[str] = ...) -> None: ...

class DeleteConversationReply(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class AddMessageRequest(_message.Message):
    __slots__ = ("conversation_id", "role", "content", "tool", "meta_json")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    META_JSON_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    role: str
    content: str
    tool: str
    meta_json: str
    def __init__(self, conversation_id: _Optional[str] = ..., role: _Optional[str] = ..., content: _Optional[str] = ..., tool: _Optional[str] = ..., meta_json: _Optional[str] = ...) -> None: ...

class ListMessagesRequest(_message.Message):
    __slots__ = ("conversation_id", "limit")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    limit: int
    def __init__(self, conversation_id: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class ListToolMessagesRequest(_message.Message):
    __slots__ = ("conversation_id", "tool")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    tool: str
    def __init__(self, conversation_id: _Optional[str] = ..., tool: _Optional[str] = ...) -> None: ...

class ListMessagesReply(_message.Message):
    __slots__ = ("messages",)
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    messages: _containers.RepeatedCompositeFieldContainer[Message]
    def __init__(self, messages: _Optional[_Iterable[_Union[Message, _Mapping]]] = ...) -> None: ...

class GetStatsRequest(_message.Message):
    __slots__ = ("user_id",)
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    def __init__(self, user_id: _Optional[str] = ...) -> None: ...

class Stats(_message.Message):
    __slots__ = ("conversations", "queries", "by_tool", "queries_rewritten", "answers_evaluated", "answers_sufficient", "avg_latency_ms")
    class ByToolEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    CONVERSATIONS_FIELD_NUMBER: _ClassVar[int]
    QUERIES_FIELD_NUMBER: _ClassVar[int]
    BY_TOOL_FIELD_NUMBER: _ClassVar[int]
    QUERIES_REWRITTEN_FIELD_NUMBER: _ClassVar[int]
    ANSWERS_EVALUATED_FIELD_NUMBER: _ClassVar[int]
    ANSWERS_SUFFICIENT_FIELD_NUMBER: _ClassVar[int]
    AVG_LATENCY_MS_FIELD_NUMBER: _ClassVar[int]
    conversations: int
    queries: int
    by_tool: _containers.ScalarMap[str, int]
    queries_rewritten: int
    answers_evaluated: int
    answers_sufficient: int
    avg_latency_ms: int
    def __init__(self, conversations: _Optional[int] = ..., queries: _Optional[int] = ..., by_tool: _Optional[_Mapping[str, int]] = ..., queries_rewritten: _Optional[int] = ..., answers_evaluated: _Optional[int] = ..., answers_sufficient: _Optional[int] = ..., avg_latency_ms: _Optional[int] = ...) -> None: ...

class ImportConversationRequest(_message.Message):
    __slots__ = ("conversation", "messages")
    CONVERSATION_FIELD_NUMBER: _ClassVar[int]
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    conversation: Conversation
    messages: _containers.RepeatedCompositeFieldContainer[Message]
    def __init__(self, conversation: _Optional[_Union[Conversation, _Mapping]] = ..., messages: _Optional[_Iterable[_Union[Message, _Mapping]]] = ...) -> None: ...

class ImportConversationReply(_message.Message):
    __slots__ = ("conversation_created", "messages_created")
    CONVERSATION_CREATED_FIELD_NUMBER: _ClassVar[int]
    MESSAGES_CREATED_FIELD_NUMBER: _ClassVar[int]
    conversation_created: bool
    messages_created: int
    def __init__(self, conversation_created: _Optional[bool] = ..., messages_created: _Optional[int] = ...) -> None: ...
