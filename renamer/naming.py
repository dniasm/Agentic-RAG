"""Titles, file names, and how closely a phrase matches a document's name."""
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from renamer import config

KNOWN_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_BAD_FILE_CHARS = re.compile(r'[<>:"/\\|?*]')
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
# Words people use to point at a document that are not part of its name.
_FILLER = {"the", "a", "an", "my", "this", "that", "one", "doc", "docs", "document", "documents", "file", "files",
           "named", "called", "titled", "pdf", "docx", "txt", "md"}


class TitleError(ValueError):
    pass


def strip_extension(name: str) -> str:
    p = Path(name)
    return p.stem if p.suffix.lower() in KNOWN_EXTENSIONS else name


def clean_title(raw: str) -> str:
    """A title as given by the user or the model, tidied. Raises TitleError if nothing usable is left."""
    title = unicodedata.normalize("NFKC", raw or "")
    title = _CONTROL.sub(" ", title).strip()
    title = title.strip("\"'`“”‘’ ").strip()
    title = strip_extension(title).strip()
    title = re.sub(r"\s+", " ", title).replace("/", "-").replace("\\", "-")
    if not title or not re.search(r"\w", title):
        raise TitleError("The new name is empty.")
    if len(title) > config.TITLE_MAX_CHARS:
        raise TitleError(f"The new name is longer than {config.TITLE_MAX_CHARS} characters.")
    return title


def file_name_for(title: str, ext: str, used: set[str]) -> str:
    """A safe, unique file name for the library folder (lower-cased names in `used` are taken)."""
    base = _BAD_FILE_CHARS.sub("-", title)
    base = re.sub(r"\s+", " ", base).strip().rstrip(". ")[:100].rstrip(". ") or "document"
    if base.lower() in _WINDOWS_RESERVED:
        base += "_"
    name, n = f"{base}{ext}", 2
    while name.lower() in used:
        name, n = f"{base} ({n}){ext}", n + 1
    return name


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = strip_extension(text)
    return " ".join(re.findall(r"[a-z0-9]+", text))


def same_name(a: str, b: str) -> bool:
    return normalize(a) == normalize(b)


def _tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t not in _FILLER]


def name_similarity(query: str, name: str) -> float:
    """0-1: how well `query` names `name`. 1.0 = the same name; >= 0.9 = clearly this name (typos, word order,
    the name written inside a longer phrase); lower = partly."""
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    qt, nt = _tokens(query), _tokens(name)
    if qt == nt and qt:
        return 1.0
    score = SequenceMatcher(None, " ".join(qt) or q, " ".join(nt) or n).ratio()
    qs, ns = set(qt), set(nt)
    if qs and ns:
        if ns <= qs:      # the whole name appears in the phrase: "the GDPR Regulation 2016 one"
            coverage = len(ns) / len(qs)
            score = max(score, 0.95 if sorted(qs) == sorted(ns) else 0.7 + 0.25 * coverage)
        elif qs <= ns:    # the phrase is part of the name: "word2vec" for "Word2vec Model Overview"
            score = max(score, 0.55 + 0.35 * len(qs) / len(ns))
    return round(score, 3)
