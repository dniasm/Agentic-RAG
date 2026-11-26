from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ReadMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    READ_ALL: _ClassVar[ReadMode]
    READ_SCANNED_CACHED: _ClassVar[ReadMode]
READ_ALL: ReadMode
READ_SCANNED_CACHED: ReadMode

class IngestStart(_message.Message):
    __slots__ = ("user_id", "doc_id", "filename", "file_name", "ext", "max_passages")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    FILE_NAME_FIELD_NUMBER: _ClassVar[int]
    EXT_FIELD_NUMBER: _ClassVar[int]
    MAX_PASSAGES_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    doc_id: str
    filename: str
    file_name: str
    ext: str
    max_passages: int
    def __init__(self, user_id: _Optional[str] = ..., doc_id: _Optional[str] = ..., filename: _Optional[str] = ..., file_name: _Optional[str] = ..., ext: _Optional[str] = ..., max_passages: _Optional[int] = ...) -> None: ...

class IngestRequest(_message.Message):
    __slots__ = ("start", "chunk")
    START_FIELD_NUMBER: _ClassVar[int]
    CHUNK_FIELD_NUMBER: _ClassVar[int]
    start: IngestStart
    chunk: bytes
    def __init__(self, start: _Optional[_Union[IngestStart, _Mapping]] = ..., chunk: _Optional[bytes] = ...) -> None: ...

class IngestProgress(_message.Message):
    __slots__ = ("stage", "done", "total")
    STAGE_FIELD_NUMBER: _ClassVar[int]
    DONE_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    stage: str
    done: int
    total: int
    def __init__(self, stage: _Optional[str] = ..., done: _Optional[int] = ..., total: _Optional[int] = ...) -> None: ...

class IngestDone(_message.Message):
    __slots__ = ("pages", "chunks", "embedded")
    PAGES_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_FIELD_NUMBER: _ClassVar[int]
    EMBEDDED_FIELD_NUMBER: _ClassVar[int]
    pages: int
    chunks: int
    embedded: int
    def __init__(self, pages: _Optional[int] = ..., chunks: _Optional[int] = ..., embedded: _Optional[int] = ...) -> None: ...

class Failure(_message.Message):
    __slots__ = ("kind", "message", "needed", "for_day")
    KIND_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    NEEDED_FIELD_NUMBER: _ClassVar[int]
    FOR_DAY_FIELD_NUMBER: _ClassVar[int]
    kind: str
    message: str
    needed: int
    for_day: bool
    def __init__(self, kind: _Optional[str] = ..., message: _Optional[str] = ..., needed: _Optional[int] = ..., for_day: _Optional[bool] = ...) -> None: ...

class IngestEvent(_message.Message):
    __slots__ = ("progress", "done", "failure")
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    DONE_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    progress: IngestProgress
    done: IngestDone
    failure: Failure
    def __init__(self, progress: _Optional[_Union[IngestProgress, _Mapping]] = ..., done: _Optional[_Union[IngestDone, _Mapping]] = ..., failure: _Optional[_Union[Failure, _Mapping]] = ...) -> None: ...

class ReadStart(_message.Message):
    __slots__ = ("ext", "mode")
    EXT_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    ext: str
    mode: ReadMode
    def __init__(self, ext: _Optional[str] = ..., mode: _Optional[_Union[ReadMode, str]] = ...) -> None: ...

class ReadRequest(_message.Message):
    __slots__ = ("start", "chunk")
    START_FIELD_NUMBER: _ClassVar[int]
    CHUNK_FIELD_NUMBER: _ClassVar[int]
    start: ReadStart
    chunk: bytes
    def __init__(self, start: _Optional[_Union[ReadStart, _Mapping]] = ..., chunk: _Optional[bytes] = ...) -> None: ...

class Page(_message.Message):
    __slots__ = ("page", "text", "ocr")
    PAGE_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    OCR_FIELD_NUMBER: _ClassVar[int]
    page: int
    text: str
    ocr: bool
    def __init__(self, page: _Optional[int] = ..., text: _Optional[str] = ..., ocr: _Optional[bool] = ...) -> None: ...

class ReadResult(_message.Message):
    __slots__ = ("pages", "page_count")
    PAGES_FIELD_NUMBER: _ClassVar[int]
    PAGE_COUNT_FIELD_NUMBER: _ClassVar[int]
    pages: _containers.RepeatedCompositeFieldContainer[Page]
    page_count: int
    def __init__(self, pages: _Optional[_Iterable[_Union[Page, _Mapping]]] = ..., page_count: _Optional[int] = ...) -> None: ...

class ReadEvent(_message.Message):
    __slots__ = ("progress", "result", "failure")
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    progress: IngestProgress
    result: ReadResult
    failure: Failure
    def __init__(self, progress: _Optional[_Union[IngestProgress, _Mapping]] = ..., result: _Optional[_Union[ReadResult, _Mapping]] = ..., failure: _Optional[_Union[Failure, _Mapping]] = ...) -> None: ...

class SearchRequest(_message.Message):
    __slots__ = ("queries", "user_id", "k", "min_score", "doc_id", "mode", "rerank_query", "mmr")
    QUERIES_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    K_FIELD_NUMBER: _ClassVar[int]
    MIN_SCORE_FIELD_NUMBER: _ClassVar[int]
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    RERANK_QUERY_FIELD_NUMBER: _ClassVar[int]
    MMR_FIELD_NUMBER: _ClassVar[int]
    queries: _containers.RepeatedScalarFieldContainer[str]
    user_id: str
    k: int
    min_score: float
    doc_id: str
    mode: str
    rerank_query: str
    mmr: bool
    def __init__(self, queries: _Optional[_Iterable[str]] = ..., user_id: _Optional[str] = ..., k: _Optional[int] = ..., min_score: _Optional[float] = ..., doc_id: _Optional[str] = ..., mode: _Optional[str] = ..., rerank_query: _Optional[str] = ..., mmr: _Optional[bool] = ...) -> None: ...

class Passage(_message.Message):
    __slots__ = ("id", "content", "source", "page", "ocr", "doc_id", "chunk_index", "matched_query", "score", "rrf", "dense_rank", "bm25_rank", "bm25", "relevance", "scorer", "selected")
    ID_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    OCR_FIELD_NUMBER: _ClassVar[int]
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    CHUNK_INDEX_FIELD_NUMBER: _ClassVar[int]
    MATCHED_QUERY_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    RRF_FIELD_NUMBER: _ClassVar[int]
    DENSE_RANK_FIELD_NUMBER: _ClassVar[int]
    BM25_RANK_FIELD_NUMBER: _ClassVar[int]
    BM25_FIELD_NUMBER: _ClassVar[int]
    RELEVANCE_FIELD_NUMBER: _ClassVar[int]
    SCORER_FIELD_NUMBER: _ClassVar[int]
    SELECTED_FIELD_NUMBER: _ClassVar[int]
    id: str
    content: str
    source: str
    page: int
    ocr: bool
    doc_id: str
    chunk_index: int
    matched_query: str
    score: float
    rrf: float
    dense_rank: int
    bm25_rank: int
    bm25: float
    relevance: float
    scorer: str
    selected: bool
    def __init__(self, id: _Optional[str] = ..., content: _Optional[str] = ..., source: _Optional[str] = ..., page: _Optional[int] = ..., ocr: _Optional[bool] = ..., doc_id: _Optional[str] = ..., chunk_index: _Optional[int] = ..., matched_query: _Optional[str] = ..., score: _Optional[float] = ..., rrf: _Optional[float] = ..., dense_rank: _Optional[int] = ..., bm25_rank: _Optional[int] = ..., bm25: _Optional[float] = ..., relevance: _Optional[float] = ..., scorer: _Optional[str] = ..., selected: _Optional[bool] = ...) -> None: ...

class SearchReply(_message.Message):
    __slots__ = ("passages", "reranked", "zone", "best_relevance")
    PASSAGES_FIELD_NUMBER: _ClassVar[int]
    RERANKED_FIELD_NUMBER: _ClassVar[int]
    ZONE_FIELD_NUMBER: _ClassVar[int]
    BEST_RELEVANCE_FIELD_NUMBER: _ClassVar[int]
    passages: _containers.RepeatedCompositeFieldContainer[Passage]
    reranked: bool
    zone: str
    best_relevance: float
    def __init__(self, passages: _Optional[_Iterable[_Union[Passage, _Mapping]]] = ..., reranked: _Optional[bool] = ..., zone: _Optional[str] = ..., best_relevance: _Optional[float] = ...) -> None: ...

class RerankItem(_message.Message):
    __slots__ = ("content",)
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    content: str
    def __init__(self, content: _Optional[str] = ...) -> None: ...

class RerankRequest(_message.Message):
    __slots__ = ("query", "items", "priority")
    QUERY_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    query: str
    items: _containers.RepeatedCompositeFieldContainer[RerankItem]
    priority: str
    def __init__(self, query: _Optional[str] = ..., items: _Optional[_Iterable[_Union[RerankItem, _Mapping]]] = ..., priority: _Optional[str] = ...) -> None: ...

class RerankReply(_message.Message):
    __slots__ = ("reranked", "relevance", "scorer")
    RERANKED_FIELD_NUMBER: _ClassVar[int]
    RELEVANCE_FIELD_NUMBER: _ClassVar[int]
    SCORER_FIELD_NUMBER: _ClassVar[int]
    reranked: bool
    relevance: _containers.RepeatedScalarFieldContainer[float]
    scorer: str
    def __init__(self, reranked: _Optional[bool] = ..., relevance: _Optional[_Iterable[float]] = ..., scorer: _Optional[str] = ...) -> None: ...

class GetDenseVectorsRequest(_message.Message):
    __slots__ = ("ids",)
    IDS_FIELD_NUMBER: _ClassVar[int]
    ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, ids: _Optional[_Iterable[str]] = ...) -> None: ...

class Vector(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, values: _Optional[_Iterable[float]] = ...) -> None: ...

class GetDenseVectorsReply(_message.Message):
    __slots__ = ("vectors",)
    VECTORS_FIELD_NUMBER: _ClassVar[int]
    vectors: _containers.RepeatedCompositeFieldContainer[Vector]
    def __init__(self, vectors: _Optional[_Iterable[_Union[Vector, _Mapping]]] = ...) -> None: ...

class DocumentRef(_message.Message):
    __slots__ = ("user_id", "doc_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    doc_id: str
    def __init__(self, user_id: _Optional[str] = ..., doc_id: _Optional[str] = ...) -> None: ...

class DeleteReply(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SetChunkFieldsRequest(_message.Message):
    __slots__ = ("user_id", "doc_id", "fields")
    class FieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    doc_id: str
    fields: _containers.ScalarMap[str, str]
    def __init__(self, user_id: _Optional[str] = ..., doc_id: _Optional[str] = ..., fields: _Optional[_Mapping[str, str]] = ...) -> None: ...

class CountPassagesRequest(_message.Message):
    __slots__ = ("user_id", "doc_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    doc_id: str
    def __init__(self, user_id: _Optional[str] = ..., doc_id: _Optional[str] = ...) -> None: ...

class CountPassagesReply(_message.Message):
    __slots__ = ("passages", "page_texts", "query_embeddings")
    PASSAGES_FIELD_NUMBER: _ClassVar[int]
    PAGE_TEXTS_FIELD_NUMBER: _ClassVar[int]
    QUERY_EMBEDDINGS_FIELD_NUMBER: _ClassVar[int]
    passages: int
    page_texts: int
    query_embeddings: int
    def __init__(self, passages: _Optional[int] = ..., page_texts: _Optional[int] = ..., query_embeddings: _Optional[int] = ...) -> None: ...

class KeyStatesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class KeyState(_message.Message):
    __slots__ = ("index", "out_for_day", "cooldown_seconds", "shares_quota", "rejected")
    INDEX_FIELD_NUMBER: _ClassVar[int]
    OUT_FOR_DAY_FIELD_NUMBER: _ClassVar[int]
    COOLDOWN_SECONDS_FIELD_NUMBER: _ClassVar[int]
    SHARES_QUOTA_FIELD_NUMBER: _ClassVar[int]
    REJECTED_FIELD_NUMBER: _ClassVar[int]
    index: int
    out_for_day: bool
    cooldown_seconds: float
    shares_quota: bool
    rejected: bool
    def __init__(self, index: _Optional[int] = ..., out_for_day: _Optional[bool] = ..., cooldown_seconds: _Optional[float] = ..., shares_quota: _Optional[bool] = ..., rejected: _Optional[bool] = ...) -> None: ...

class KeyStatesReply(_message.Message):
    __slots__ = ("keys",)
    KEYS_FIELD_NUMBER: _ClassVar[int]
    keys: _containers.RepeatedCompositeFieldContainer[KeyState]
    def __init__(self, keys: _Optional[_Iterable[_Union[KeyState, _Mapping]]] = ...) -> None: ...

class SparseVector(_message.Message):
    __slots__ = ("indices", "values")
    INDICES_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    indices: _containers.RepeatedScalarFieldContainer[int]
    values: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, indices: _Optional[_Iterable[int]] = ..., values: _Optional[_Iterable[float]] = ...) -> None: ...

class StoredPassage(_message.Message):
    __slots__ = ("id", "dense", "sparse", "payload_json")
    ID_FIELD_NUMBER: _ClassVar[int]
    DENSE_FIELD_NUMBER: _ClassVar[int]
    SPARSE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_JSON_FIELD_NUMBER: _ClassVar[int]
    id: str
    dense: _containers.RepeatedScalarFieldContainer[float]
    sparse: SparseVector
    payload_json: str
    def __init__(self, id: _Optional[str] = ..., dense: _Optional[_Iterable[float]] = ..., sparse: _Optional[_Union[SparseVector, _Mapping]] = ..., payload_json: _Optional[str] = ...) -> None: ...

class ImportPassagesRequest(_message.Message):
    __slots__ = ("passages",)
    PASSAGES_FIELD_NUMBER: _ClassVar[int]
    passages: _containers.RepeatedCompositeFieldContainer[StoredPassage]
    def __init__(self, passages: _Optional[_Iterable[_Union[StoredPassage, _Mapping]]] = ...) -> None: ...

class CacheRow(_message.Message):
    __slots__ = ("key", "value")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    key: str
    value: str
    def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class ImportCachesRequest(_message.Message):
    __slots__ = ("page_texts", "query_embeddings")
    PAGE_TEXTS_FIELD_NUMBER: _ClassVar[int]
    QUERY_EMBEDDINGS_FIELD_NUMBER: _ClassVar[int]
    page_texts: _containers.RepeatedCompositeFieldContainer[CacheRow]
    query_embeddings: _containers.RepeatedCompositeFieldContainer[CacheRow]
    def __init__(self, page_texts: _Optional[_Iterable[_Union[CacheRow, _Mapping]]] = ..., query_embeddings: _Optional[_Iterable[_Union[CacheRow, _Mapping]]] = ...) -> None: ...

class DocumentImportReply(_message.Message):
    __slots__ = ("created",)
    CREATED_FIELD_NUMBER: _ClassVar[int]
    created: int
    def __init__(self, created: _Optional[int] = ...) -> None: ...
