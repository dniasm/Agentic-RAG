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
    selected: int
    def __init__(self, id: _Optional[str] = ..., content: _Optional[str] = ..., source: _Optional[str] = ..., page: _Optional[int] = ..., ocr: _Optional[bool] = ..., doc_id: _Optional[str] = ..., chunk_index: _Optional[int] = ..., matched_query: _Optional[str] = ..., score: _Optional[float] = ..., rrf: _Optional[float] = ..., dense_rank: _Optional[int] = ..., bm25_rank: _Optional[int] = ..., bm25: _Optional[float] = ..., relevance: _Optional[float] = ..., scorer: _Optional[str] = ..., selected: _Optional[int] = ...) -> None: ...

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
    __slots__ = ("user_id", "doc_id", "fields_json")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    FIELDS_JSON_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    doc_id: str
    fields_json: str
    def __init__(self, user_id: _Optional[str] = ..., doc_id: _Optional[str] = ..., fields_json: _Optional[str] = ...) -> None: ...

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

class CatalogOwnerlessRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class CountReply(_message.Message):
    __slots__ = ("count",)
    COUNT_FIELD_NUMBER: _ClassVar[int]
    count: int
    def __init__(self, count: _Optional[int] = ...) -> None: ...

class CatalogDocument(_message.Message):
    __slots__ = ("id", "title", "original_name", "file_name", "ext", "sha256", "original_sha256", "size_bytes", "pages", "chunks", "summary", "keywords_json", "owner", "status", "error", "created_at", "updated_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    ORIGINAL_NAME_FIELD_NUMBER: _ClassVar[int]
    FILE_NAME_FIELD_NUMBER: _ClassVar[int]
    EXT_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    ORIGINAL_SHA256_FIELD_NUMBER: _ClassVar[int]
    SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    PAGES_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    KEYWORDS_JSON_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    title: str
    original_name: str
    file_name: str
    ext: str
    sha256: str
    original_sha256: str
    size_bytes: int
    pages: int
    chunks: int
    summary: str
    keywords_json: str
    owner: str
    status: str
    error: str
    created_at: str
    updated_at: str
    def __init__(self, id: _Optional[str] = ..., title: _Optional[str] = ..., original_name: _Optional[str] = ..., file_name: _Optional[str] = ..., ext: _Optional[str] = ..., sha256: _Optional[str] = ..., original_sha256: _Optional[str] = ..., size_bytes: _Optional[int] = ..., pages: _Optional[int] = ..., chunks: _Optional[int] = ..., summary: _Optional[str] = ..., keywords_json: _Optional[str] = ..., owner: _Optional[str] = ..., status: _Optional[str] = ..., error: _Optional[str] = ..., created_at: _Optional[str] = ..., updated_at: _Optional[str] = ...) -> None: ...

class OptionalCatalogDocument(_message.Message):
    __slots__ = ("document",)
    DOCUMENT_FIELD_NUMBER: _ClassVar[int]
    document: CatalogDocument
    def __init__(self, document: _Optional[_Union[CatalogDocument, _Mapping]] = ...) -> None: ...

class CatalogDocumentList(_message.Message):
    __slots__ = ("documents",)
    DOCUMENTS_FIELD_NUMBER: _ClassVar[int]
    documents: _containers.RepeatedCompositeFieldContainer[CatalogDocument]
    def __init__(self, documents: _Optional[_Iterable[_Union[CatalogDocument, _Mapping]]] = ...) -> None: ...

class CatalogDocumentRef(_message.Message):
    __slots__ = ("doc_id",)
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    doc_id: str
    def __init__(self, doc_id: _Optional[str] = ...) -> None: ...

class AddCatalogDocumentRequest(_message.Message):
    __slots__ = ("title", "original_name", "file_name", "ext", "sha256", "size_bytes", "owner")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    ORIGINAL_NAME_FIELD_NUMBER: _ClassVar[int]
    FILE_NAME_FIELD_NUMBER: _ClassVar[int]
    EXT_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    title: str
    original_name: str
    file_name: str
    ext: str
    sha256: str
    size_bytes: int
    owner: str
    def __init__(self, title: _Optional[str] = ..., original_name: _Optional[str] = ..., file_name: _Optional[str] = ..., ext: _Optional[str] = ..., sha256: _Optional[str] = ..., size_bytes: _Optional[int] = ..., owner: _Optional[str] = ...) -> None: ...

class FindByHashRequest(_message.Message):
    __slots__ = ("sha256", "owner")
    SHA256_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    sha256: str
    owner: str
    def __init__(self, sha256: _Optional[str] = ..., owner: _Optional[str] = ...) -> None: ...

class ListCatalogDocumentsRequest(_message.Message):
    __slots__ = ("status", "owner")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    status: str
    owner: str
    def __init__(self, status: _Optional[str] = ..., owner: _Optional[str] = ...) -> None: ...

class RenameRecord(_message.Message):
    __slots__ = ("old_title", "new_title", "reason", "at")
    OLD_TITLE_FIELD_NUMBER: _ClassVar[int]
    NEW_TITLE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    old_title: str
    new_title: str
    reason: str
    at: str
    def __init__(self, old_title: _Optional[str] = ..., new_title: _Optional[str] = ..., reason: _Optional[str] = ..., at: _Optional[str] = ...) -> None: ...

class RecordRenameRequest(_message.Message):
    __slots__ = ("doc_id", "old_title", "new_title", "reason")
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    OLD_TITLE_FIELD_NUMBER: _ClassVar[int]
    NEW_TITLE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    doc_id: str
    old_title: str
    new_title: str
    reason: str
    def __init__(self, doc_id: _Optional[str] = ..., old_title: _Optional[str] = ..., new_title: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class UpdateCatalogDocumentRequest(_message.Message):
    __slots__ = ("doc_id", "fields_json", "rename")
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    FIELDS_JSON_FIELD_NUMBER: _ClassVar[int]
    RENAME_FIELD_NUMBER: _ClassVar[int]
    doc_id: str
    fields_json: str
    rename: RecordRenameRequest
    def __init__(self, doc_id: _Optional[str] = ..., fields_json: _Optional[str] = ..., rename: _Optional[_Union[RecordRenameRequest, _Mapping]] = ...) -> None: ...

class RenameHistoryReply(_message.Message):
    __slots__ = ("renames",)
    RENAMES_FIELD_NUMBER: _ClassVar[int]
    renames: _containers.RepeatedCompositeFieldContainer[RenameRecord]
    def __init__(self, renames: _Optional[_Iterable[_Union[RenameRecord, _Mapping]]] = ...) -> None: ...

class DocumentRenames(_message.Message):
    __slots__ = ("doc_id", "renames")
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    RENAMES_FIELD_NUMBER: _ClassVar[int]
    doc_id: str
    renames: _containers.RepeatedCompositeFieldContainer[RenameRecord]
    def __init__(self, doc_id: _Optional[str] = ..., renames: _Optional[_Iterable[_Union[RenameRecord, _Mapping]]] = ...) -> None: ...

class RenameHistoriesReply(_message.Message):
    __slots__ = ("documents",)
    DOCUMENTS_FIELD_NUMBER: _ClassVar[int]
    documents: _containers.RepeatedCompositeFieldContainer[DocumentRenames]
    def __init__(self, documents: _Optional[_Iterable[_Union[DocumentRenames, _Mapping]]] = ...) -> None: ...

class UsedFileNamesRequest(_message.Message):
    __slots__ = ("exclude_doc",)
    EXCLUDE_DOC_FIELD_NUMBER: _ClassVar[int]
    exclude_doc: str
    def __init__(self, exclude_doc: _Optional[str] = ...) -> None: ...

class UsedFileNamesReply(_message.Message):
    __slots__ = ("file_names",)
    FILE_NAMES_FIELD_NUMBER: _ClassVar[int]
    file_names: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, file_names: _Optional[_Iterable[str]] = ...) -> None: ...

class EditRecord(_message.Message):
    __slots__ = ("id", "doc_id", "operation", "detail", "backup", "at")
    ID_FIELD_NUMBER: _ClassVar[int]
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    BACKUP_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    id: int
    doc_id: str
    operation: str
    detail: str
    backup: str
    at: str
    def __init__(self, id: _Optional[int] = ..., doc_id: _Optional[str] = ..., operation: _Optional[str] = ..., detail: _Optional[str] = ..., backup: _Optional[str] = ..., at: _Optional[str] = ...) -> None: ...

class OptionalEdit(_message.Message):
    __slots__ = ("edit",)
    EDIT_FIELD_NUMBER: _ClassVar[int]
    edit: EditRecord
    def __init__(self, edit: _Optional[_Union[EditRecord, _Mapping]] = ...) -> None: ...

class EditRef(_message.Message):
    __slots__ = ("edit_id",)
    EDIT_ID_FIELD_NUMBER: _ClassVar[int]
    edit_id: int
    def __init__(self, edit_id: _Optional[int] = ...) -> None: ...

class EditExistsReply(_message.Message):
    __slots__ = ("exists",)
    EXISTS_FIELD_NUMBER: _ClassVar[int]
    exists: bool
    def __init__(self, exists: _Optional[bool] = ...) -> None: ...

class EditSummary(_message.Message):
    __slots__ = ("operation", "detail", "at")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    operation: str
    detail: str
    at: str
    def __init__(self, operation: _Optional[str] = ..., detail: _Optional[str] = ..., at: _Optional[str] = ...) -> None: ...

class EditHistoryReply(_message.Message):
    __slots__ = ("edits",)
    EDITS_FIELD_NUMBER: _ClassVar[int]
    edits: _containers.RepeatedCompositeFieldContainer[EditSummary]
    def __init__(self, edits: _Optional[_Iterable[_Union[EditSummary, _Mapping]]] = ...) -> None: ...

class RecordEditRequest(_message.Message):
    __slots__ = ("doc_id", "operation", "detail", "backup", "document_fields_json")
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    BACKUP_FIELD_NUMBER: _ClassVar[int]
    DOCUMENT_FIELDS_JSON_FIELD_NUMBER: _ClassVar[int]
    doc_id: str
    operation: str
    detail: str
    backup: str
    document_fields_json: str
    def __init__(self, doc_id: _Optional[str] = ..., operation: _Optional[str] = ..., detail: _Optional[str] = ..., backup: _Optional[str] = ..., document_fields_json: _Optional[str] = ...) -> None: ...

class RecordEditReply(_message.Message):
    __slots__ = ("edit_id",)
    EDIT_ID_FIELD_NUMBER: _ClassVar[int]
    edit_id: int
    def __init__(self, edit_id: _Optional[int] = ...) -> None: ...

class UndoneEdit(_message.Message):
    __slots__ = ("id", "doc_id", "operation", "detail", "backup", "redo", "ext", "at", "undone_at", "undo_order")
    ID_FIELD_NUMBER: _ClassVar[int]
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    BACKUP_FIELD_NUMBER: _ClassVar[int]
    REDO_FIELD_NUMBER: _ClassVar[int]
    EXT_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    UNDONE_AT_FIELD_NUMBER: _ClassVar[int]
    UNDO_ORDER_FIELD_NUMBER: _ClassVar[int]
    id: int
    doc_id: str
    operation: str
    detail: str
    backup: str
    redo: str
    ext: str
    at: str
    undone_at: str
    undo_order: int
    def __init__(self, id: _Optional[int] = ..., doc_id: _Optional[str] = ..., operation: _Optional[str] = ..., detail: _Optional[str] = ..., backup: _Optional[str] = ..., redo: _Optional[str] = ..., ext: _Optional[str] = ..., at: _Optional[str] = ..., undone_at: _Optional[str] = ..., undo_order: _Optional[int] = ...) -> None: ...

class OptionalUndoneEdit(_message.Message):
    __slots__ = ("undone",)
    UNDONE_FIELD_NUMBER: _ClassVar[int]
    undone: UndoneEdit
    def __init__(self, undone: _Optional[_Union[UndoneEdit, _Mapping]] = ...) -> None: ...

class UndoEditRequest(_message.Message):
    __slots__ = ("edit", "redo", "ext", "document_fields_json")
    EDIT_FIELD_NUMBER: _ClassVar[int]
    REDO_FIELD_NUMBER: _ClassVar[int]
    EXT_FIELD_NUMBER: _ClassVar[int]
    DOCUMENT_FIELDS_JSON_FIELD_NUMBER: _ClassVar[int]
    edit: EditRecord
    redo: str
    ext: str
    document_fields_json: str
    def __init__(self, edit: _Optional[_Union[EditRecord, _Mapping]] = ..., redo: _Optional[str] = ..., ext: _Optional[str] = ..., document_fields_json: _Optional[str] = ...) -> None: ...

class RedoEditRequest(_message.Message):
    __slots__ = ("undone_id", "document_fields_json")
    UNDONE_ID_FIELD_NUMBER: _ClassVar[int]
    DOCUMENT_FIELDS_JSON_FIELD_NUMBER: _ClassVar[int]
    undone_id: int
    document_fields_json: str
    def __init__(self, undone_id: _Optional[int] = ..., document_fields_json: _Optional[str] = ...) -> None: ...

class ClearUndoneReply(_message.Message):
    __slots__ = ("dropped",)
    DROPPED_FIELD_NUMBER: _ClassVar[int]
    dropped: _containers.RepeatedCompositeFieldContainer[UndoneEdit]
    def __init__(self, dropped: _Optional[_Iterable[_Union[UndoneEdit, _Mapping]]] = ...) -> None: ...

class LibrarySessionRef(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...

class LibrarySession(_message.Message):
    __slots__ = ("id", "turn", "llm_messages_json", "display_json", "guard_json", "owner")
    ID_FIELD_NUMBER: _ClassVar[int]
    TURN_FIELD_NUMBER: _ClassVar[int]
    LLM_MESSAGES_JSON_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_JSON_FIELD_NUMBER: _ClassVar[int]
    GUARD_JSON_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    id: str
    turn: int
    llm_messages_json: str
    display_json: str
    guard_json: str
    owner: str
    def __init__(self, id: _Optional[str] = ..., turn: _Optional[int] = ..., llm_messages_json: _Optional[str] = ..., display_json: _Optional[str] = ..., guard_json: _Optional[str] = ..., owner: _Optional[str] = ...) -> None: ...

class BatchItem(_message.Message):
    __slots__ = ("instruction", "payload_json")
    INSTRUCTION_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_JSON_FIELD_NUMBER: _ClassVar[int]
    instruction: str
    payload_json: str
    def __init__(self, instruction: _Optional[str] = ..., payload_json: _Optional[str] = ...) -> None: ...

class AddBatchRequest(_message.Message):
    __slots__ = ("session_id", "items", "owner")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    items: _containers.RepeatedCompositeFieldContainer[BatchItem]
    owner: str
    def __init__(self, session_id: _Optional[str] = ..., items: _Optional[_Iterable[_Union[BatchItem, _Mapping]]] = ..., owner: _Optional[str] = ...) -> None: ...

class BatchRef(_message.Message):
    __slots__ = ("batch_id",)
    BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    batch_id: str
    def __init__(self, batch_id: _Optional[str] = ...) -> None: ...

class TaskRef(_message.Message):
    __slots__ = ("task_id",)
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: int
    def __init__(self, task_id: _Optional[int] = ...) -> None: ...

class CatalogTask(_message.Message):
    __slots__ = ("id", "batch_id", "position", "instruction", "payload_json", "checkpoint_json", "status", "reply", "actions_json", "tokens", "started_at", "finished_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTION_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_JSON_FIELD_NUMBER: _ClassVar[int]
    CHECKPOINT_JSON_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    REPLY_FIELD_NUMBER: _ClassVar[int]
    ACTIONS_JSON_FIELD_NUMBER: _ClassVar[int]
    TOKENS_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    id: int
    batch_id: str
    position: int
    instruction: str
    payload_json: str
    checkpoint_json: str
    status: str
    reply: str
    actions_json: str
    tokens: int
    started_at: str
    finished_at: str
    def __init__(self, id: _Optional[int] = ..., batch_id: _Optional[str] = ..., position: _Optional[int] = ..., instruction: _Optional[str] = ..., payload_json: _Optional[str] = ..., checkpoint_json: _Optional[str] = ..., status: _Optional[str] = ..., reply: _Optional[str] = ..., actions_json: _Optional[str] = ..., tokens: _Optional[int] = ..., started_at: _Optional[str] = ..., finished_at: _Optional[str] = ...) -> None: ...

class OptionalTask(_message.Message):
    __slots__ = ("task",)
    TASK_FIELD_NUMBER: _ClassVar[int]
    task: CatalogTask
    def __init__(self, task: _Optional[_Union[CatalogTask, _Mapping]] = ...) -> None: ...

class TaskList(_message.Message):
    __slots__ = ("tasks",)
    TASKS_FIELD_NUMBER: _ClassVar[int]
    tasks: _containers.RepeatedCompositeFieldContainer[CatalogTask]
    def __init__(self, tasks: _Optional[_Iterable[_Union[CatalogTask, _Mapping]]] = ...) -> None: ...

class CatalogBatch(_message.Message):
    __slots__ = ("id", "session_id", "owner", "created_at", "tasks")
    ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    TASKS_FIELD_NUMBER: _ClassVar[int]
    id: str
    session_id: str
    owner: str
    created_at: str
    tasks: _containers.RepeatedCompositeFieldContainer[CatalogTask]
    def __init__(self, id: _Optional[str] = ..., session_id: _Optional[str] = ..., owner: _Optional[str] = ..., created_at: _Optional[str] = ..., tasks: _Optional[_Iterable[_Union[CatalogTask, _Mapping]]] = ...) -> None: ...

class OptionalBatch(_message.Message):
    __slots__ = ("batch",)
    BATCH_FIELD_NUMBER: _ClassVar[int]
    batch: CatalogBatch
    def __init__(self, batch: _Optional[_Union[CatalogBatch, _Mapping]] = ...) -> None: ...

class BatchList(_message.Message):
    __slots__ = ("batches",)
    BATCHES_FIELD_NUMBER: _ClassVar[int]
    batches: _containers.RepeatedCompositeFieldContainer[CatalogBatch]
    def __init__(self, batches: _Optional[_Iterable[_Union[CatalogBatch, _Mapping]]] = ...) -> None: ...

class ActiveBatchesRequest(_message.Message):
    __slots__ = ("hours", "owner")
    HOURS_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    hours: float
    owner: str
    def __init__(self, hours: _Optional[float] = ..., owner: _Optional[str] = ...) -> None: ...

class ListBatchesRequest(_message.Message):
    __slots__ = ("limit", "owner")
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    limit: int
    owner: str
    def __init__(self, limit: _Optional[int] = ..., owner: _Optional[str] = ...) -> None: ...

class TaskRun(_message.Message):
    __slots__ = ("actions_json", "finished_at")
    ACTIONS_JSON_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    actions_json: str
    finished_at: str
    def __init__(self, actions_json: _Optional[str] = ..., finished_at: _Optional[str] = ...) -> None: ...

class TasksRunReply(_message.Message):
    __slots__ = ("tasks",)
    TASKS_FIELD_NUMBER: _ClassVar[int]
    tasks: _containers.RepeatedCompositeFieldContainer[TaskRun]
    def __init__(self, tasks: _Optional[_Iterable[_Union[TaskRun, _Mapping]]] = ...) -> None: ...

class UpdateTaskRequest(_message.Message):
    __slots__ = ("task_id", "fields_json")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    FIELDS_JSON_FIELD_NUMBER: _ClassVar[int]
    task_id: int
    fields_json: str
    def __init__(self, task_id: _Optional[int] = ..., fields_json: _Optional[str] = ...) -> None: ...

class TaskOwner(_message.Message):
    __slots__ = ("batch_id", "owner")
    BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    batch_id: str
    owner: str
    def __init__(self, batch_id: _Optional[str] = ..., owner: _Optional[str] = ...) -> None: ...

class CancelBatchReply(_message.Message):
    __slots__ = ("cancelled", "owner")
    CANCELLED_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    cancelled: int
    owner: str
    def __init__(self, cancelled: _Optional[int] = ..., owner: _Optional[str] = ...) -> None: ...

class CountQueuedRequest(_message.Message):
    __slots__ = ("owner",)
    OWNER_FIELD_NUMBER: _ClassVar[int]
    owner: str
    def __init__(self, owner: _Optional[str] = ...) -> None: ...

class TasksWithStatusRequest(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: str
    def __init__(self, status: _Optional[str] = ...) -> None: ...

class GiveLegacyRequest(_message.Message):
    __slots__ = ("user_id",)
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    def __init__(self, user_id: _Optional[str] = ...) -> None: ...

class CatalogDump(_message.Message):
    __slots__ = ("documents", "renames", "edits", "undone_edits", "batches", "sessions")
    DOCUMENTS_FIELD_NUMBER: _ClassVar[int]
    RENAMES_FIELD_NUMBER: _ClassVar[int]
    EDITS_FIELD_NUMBER: _ClassVar[int]
    UNDONE_EDITS_FIELD_NUMBER: _ClassVar[int]
    BATCHES_FIELD_NUMBER: _ClassVar[int]
    SESSIONS_FIELD_NUMBER: _ClassVar[int]
    documents: _containers.RepeatedCompositeFieldContainer[CatalogDocument]
    renames: _containers.RepeatedCompositeFieldContainer[RenameRow]
    edits: _containers.RepeatedCompositeFieldContainer[EditRecord]
    undone_edits: _containers.RepeatedCompositeFieldContainer[UndoneEdit]
    batches: _containers.RepeatedCompositeFieldContainer[CatalogBatch]
    sessions: _containers.RepeatedCompositeFieldContainer[StoredSession]
    def __init__(self, documents: _Optional[_Iterable[_Union[CatalogDocument, _Mapping]]] = ..., renames: _Optional[_Iterable[_Union[RenameRow, _Mapping]]] = ..., edits: _Optional[_Iterable[_Union[EditRecord, _Mapping]]] = ..., undone_edits: _Optional[_Iterable[_Union[UndoneEdit, _Mapping]]] = ..., batches: _Optional[_Iterable[_Union[CatalogBatch, _Mapping]]] = ..., sessions: _Optional[_Iterable[_Union[StoredSession, _Mapping]]] = ...) -> None: ...

class RenameRow(_message.Message):
    __slots__ = ("id", "doc_id", "old_title", "new_title", "reason", "at")
    ID_FIELD_NUMBER: _ClassVar[int]
    DOC_ID_FIELD_NUMBER: _ClassVar[int]
    OLD_TITLE_FIELD_NUMBER: _ClassVar[int]
    NEW_TITLE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    id: int
    doc_id: str
    old_title: str
    new_title: str
    reason: str
    at: str
    def __init__(self, id: _Optional[int] = ..., doc_id: _Optional[str] = ..., old_title: _Optional[str] = ..., new_title: _Optional[str] = ..., reason: _Optional[str] = ..., at: _Optional[str] = ...) -> None: ...

class StoredSession(_message.Message):
    __slots__ = ("session", "created_at", "updated_at")
    SESSION_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    session: LibrarySession
    created_at: str
    updated_at: str
    def __init__(self, session: _Optional[_Union[LibrarySession, _Mapping]] = ..., created_at: _Optional[str] = ..., updated_at: _Optional[str] = ...) -> None: ...

class ImportCatalogRequest(_message.Message):
    __slots__ = ("dump",)
    DUMP_FIELD_NUMBER: _ClassVar[int]
    dump: CatalogDump
    def __init__(self, dump: _Optional[_Union[CatalogDump, _Mapping]] = ...) -> None: ...

class CatalogCounts(_message.Message):
    __slots__ = ("documents", "renames", "edits", "undone_edits", "batches", "batch_tasks", "sessions")
    DOCUMENTS_FIELD_NUMBER: _ClassVar[int]
    RENAMES_FIELD_NUMBER: _ClassVar[int]
    EDITS_FIELD_NUMBER: _ClassVar[int]
    UNDONE_EDITS_FIELD_NUMBER: _ClassVar[int]
    BATCHES_FIELD_NUMBER: _ClassVar[int]
    BATCH_TASKS_FIELD_NUMBER: _ClassVar[int]
    SESSIONS_FIELD_NUMBER: _ClassVar[int]
    documents: int
    renames: int
    edits: int
    undone_edits: int
    batches: int
    batch_tasks: int
    sessions: int
    def __init__(self, documents: _Optional[int] = ..., renames: _Optional[int] = ..., edits: _Optional[int] = ..., undone_edits: _Optional[int] = ..., batches: _Optional[int] = ..., batch_tasks: _Optional[int] = ..., sessions: _Optional[int] = ...) -> None: ...

class ImportCatalogReply(_message.Message):
    __slots__ = ("created",)
    CREATED_FIELD_NUMBER: _ClassVar[int]
    created: CatalogCounts
    def __init__(self, created: _Optional[_Union[CatalogCounts, _Mapping]] = ...) -> None: ...

class CountCatalogReply(_message.Message):
    __slots__ = ("held",)
    HELD_FIELD_NUMBER: _ClassVar[int]
    held: CatalogCounts
    def __init__(self, held: _Optional[_Union[CatalogCounts, _Mapping]] = ...) -> None: ...
