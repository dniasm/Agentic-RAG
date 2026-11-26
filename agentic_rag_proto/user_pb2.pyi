from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class User(_message.Message):
    __slots__ = ("id", "username", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    username: str
    created_at: str
    def __init__(self, id: _Optional[str] = ..., username: _Optional[str] = ..., created_at: _Optional[str] = ...) -> None: ...

class CountUsersRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class CountUsersReply(_message.Message):
    __slots__ = ("count",)
    COUNT_FIELD_NUMBER: _ClassVar[int]
    count: int
    def __init__(self, count: _Optional[int] = ...) -> None: ...

class SignUpRequest(_message.Message):
    __slots__ = ("username", "password")
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    username: str
    password: str
    def __init__(self, username: _Optional[str] = ..., password: _Optional[str] = ...) -> None: ...

class LogInRequest(_message.Message):
    __slots__ = ("username", "password")
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    username: str
    password: str
    def __init__(self, username: _Optional[str] = ..., password: _Optional[str] = ...) -> None: ...

class LoginReply(_message.Message):
    __slots__ = ("user", "token", "first_account", "max_age_seconds")
    USER_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    FIRST_ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    MAX_AGE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    user: User
    token: str
    first_account: bool
    max_age_seconds: int
    def __init__(self, user: _Optional[_Union[User, _Mapping]] = ..., token: _Optional[str] = ..., first_account: _Optional[bool] = ..., max_age_seconds: _Optional[int] = ...) -> None: ...

class LogOutRequest(_message.Message):
    __slots__ = ("token",)
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    token: str
    def __init__(self, token: _Optional[str] = ...) -> None: ...

class LogOutReply(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ValidateTokenRequest(_message.Message):
    __slots__ = ("token",)
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    token: str
    def __init__(self, token: _Optional[str] = ...) -> None: ...

class ValidateTokenReply(_message.Message):
    __slots__ = ("found", "user")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    USER_FIELD_NUMBER: _ClassVar[int]
    found: bool
    user: User
    def __init__(self, found: _Optional[bool] = ..., user: _Optional[_Union[User, _Mapping]] = ...) -> None: ...

class ListUsersRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListUsersReply(_message.Message):
    __slots__ = ("users",)
    USERS_FIELD_NUMBER: _ClassVar[int]
    users: _containers.RepeatedCompositeFieldContainer[User]
    def __init__(self, users: _Optional[_Iterable[_Union[User, _Mapping]]] = ...) -> None: ...

class GetEmbedUsageRequest(_message.Message):
    __slots__ = ("day", "user_id")
    DAY_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    day: str
    user_id: str
    def __init__(self, day: _Optional[str] = ..., user_id: _Optional[str] = ...) -> None: ...

class AddEmbedUsageRequest(_message.Message):
    __slots__ = ("day", "user_id", "chunks")
    DAY_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_FIELD_NUMBER: _ClassVar[int]
    day: str
    user_id: str
    chunks: int
    def __init__(self, day: _Optional[str] = ..., user_id: _Optional[str] = ..., chunks: _Optional[int] = ...) -> None: ...

class EmbedUsage(_message.Message):
    __slots__ = ("chunks",)
    CHUNKS_FIELD_NUMBER: _ClassVar[int]
    chunks: int
    def __init__(self, chunks: _Optional[int] = ...) -> None: ...

class ListEmbedUsageRequest(_message.Message):
    __slots__ = ("day",)
    DAY_FIELD_NUMBER: _ClassVar[int]
    day: str
    def __init__(self, day: _Optional[str] = ...) -> None: ...

class EmbedUsageRow(_message.Message):
    __slots__ = ("user_id", "chunks")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    chunks: int
    def __init__(self, user_id: _Optional[str] = ..., chunks: _Optional[int] = ...) -> None: ...

class ListEmbedUsageReply(_message.Message):
    __slots__ = ("rows",)
    ROWS_FIELD_NUMBER: _ClassVar[int]
    rows: _containers.RepeatedCompositeFieldContainer[EmbedUsageRow]
    def __init__(self, rows: _Optional[_Iterable[_Union[EmbedUsageRow, _Mapping]]] = ...) -> None: ...

class ApiUsageRow(_message.Message):
    __slots__ = ("hour", "provider", "key_index", "model", "calls", "tokens", "limited")
    HOUR_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    KEY_INDEX_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    CALLS_FIELD_NUMBER: _ClassVar[int]
    TOKENS_FIELD_NUMBER: _ClassVar[int]
    LIMITED_FIELD_NUMBER: _ClassVar[int]
    hour: str
    provider: str
    key_index: int
    model: str
    calls: int
    tokens: int
    limited: int
    def __init__(self, hour: _Optional[str] = ..., provider: _Optional[str] = ..., key_index: _Optional[int] = ..., model: _Optional[str] = ..., calls: _Optional[int] = ..., tokens: _Optional[int] = ..., limited: _Optional[int] = ...) -> None: ...

class AddApiUsageRequest(_message.Message):
    __slots__ = ("rows",)
    ROWS_FIELD_NUMBER: _ClassVar[int]
    rows: _containers.RepeatedCompositeFieldContainer[ApiUsageRow]
    def __init__(self, rows: _Optional[_Iterable[_Union[ApiUsageRow, _Mapping]]] = ...) -> None: ...

class AddApiUsageReply(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListApiUsageRequest(_message.Message):
    __slots__ = ("since_hour",)
    SINCE_HOUR_FIELD_NUMBER: _ClassVar[int]
    since_hour: str
    def __init__(self, since_hour: _Optional[str] = ...) -> None: ...

class ListApiUsageReply(_message.Message):
    __slots__ = ("rows",)
    ROWS_FIELD_NUMBER: _ClassVar[int]
    rows: _containers.RepeatedCompositeFieldContainer[ApiUsageRow]
    def __init__(self, rows: _Optional[_Iterable[_Union[ApiUsageRow, _Mapping]]] = ...) -> None: ...

class CallsByKeyRequest(_message.Message):
    __slots__ = ("provider", "since_hour")
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    SINCE_HOUR_FIELD_NUMBER: _ClassVar[int]
    provider: str
    since_hour: str
    def __init__(self, provider: _Optional[str] = ..., since_hour: _Optional[str] = ...) -> None: ...

class CallsByKeyReply(_message.Message):
    __slots__ = ("calls",)
    class CallsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: int
        value: int
        def __init__(self, key: _Optional[int] = ..., value: _Optional[int] = ...) -> None: ...
    CALLS_FIELD_NUMBER: _ClassVar[int]
    calls: _containers.ScalarMap[int, int]
    def __init__(self, calls: _Optional[_Mapping[int, int]] = ...) -> None: ...

class ImportedUser(_message.Message):
    __slots__ = ("id", "username", "password_hash", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_HASH_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    username: str
    password_hash: str
    created_at: str
    def __init__(self, id: _Optional[str] = ..., username: _Optional[str] = ..., password_hash: _Optional[str] = ..., created_at: _Optional[str] = ...) -> None: ...

class ImportedToken(_message.Message):
    __slots__ = ("token_hash", "user_id", "created_at", "expires_at")
    TOKEN_HASH_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    token_hash: str
    user_id: str
    created_at: str
    expires_at: str
    def __init__(self, token_hash: _Optional[str] = ..., user_id: _Optional[str] = ..., created_at: _Optional[str] = ..., expires_at: _Optional[str] = ...) -> None: ...

class ImportedEmbedUsage(_message.Message):
    __slots__ = ("day", "user_id", "chunks")
    DAY_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_FIELD_NUMBER: _ClassVar[int]
    day: str
    user_id: str
    chunks: int
    def __init__(self, day: _Optional[str] = ..., user_id: _Optional[str] = ..., chunks: _Optional[int] = ...) -> None: ...

class ImportUsersRequest(_message.Message):
    __slots__ = ("users", "tokens", "embed_usage", "api_usage")
    USERS_FIELD_NUMBER: _ClassVar[int]
    TOKENS_FIELD_NUMBER: _ClassVar[int]
    EMBED_USAGE_FIELD_NUMBER: _ClassVar[int]
    API_USAGE_FIELD_NUMBER: _ClassVar[int]
    users: _containers.RepeatedCompositeFieldContainer[ImportedUser]
    tokens: _containers.RepeatedCompositeFieldContainer[ImportedToken]
    embed_usage: _containers.RepeatedCompositeFieldContainer[ImportedEmbedUsage]
    api_usage: _containers.RepeatedCompositeFieldContainer[ApiUsageRow]
    def __init__(self, users: _Optional[_Iterable[_Union[ImportedUser, _Mapping]]] = ..., tokens: _Optional[_Iterable[_Union[ImportedToken, _Mapping]]] = ..., embed_usage: _Optional[_Iterable[_Union[ImportedEmbedUsage, _Mapping]]] = ..., api_usage: _Optional[_Iterable[_Union[ApiUsageRow, _Mapping]]] = ...) -> None: ...

class ImportUsersReply(_message.Message):
    __slots__ = ("users_created", "tokens_created", "embed_usage_created", "api_usage_created", "skipped_users")
    USERS_CREATED_FIELD_NUMBER: _ClassVar[int]
    TOKENS_CREATED_FIELD_NUMBER: _ClassVar[int]
    EMBED_USAGE_CREATED_FIELD_NUMBER: _ClassVar[int]
    API_USAGE_CREATED_FIELD_NUMBER: _ClassVar[int]
    SKIPPED_USERS_FIELD_NUMBER: _ClassVar[int]
    users_created: int
    tokens_created: int
    embed_usage_created: int
    api_usage_created: int
    skipped_users: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, users_created: _Optional[int] = ..., tokens_created: _Optional[int] = ..., embed_usage_created: _Optional[int] = ..., api_usage_created: _Optional[int] = ..., skipped_users: _Optional[_Iterable[str]] = ...) -> None: ...
