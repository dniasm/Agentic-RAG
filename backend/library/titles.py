"""Gives each ingested document a title, a short summary and keywords, written by the model from its opening text.

The summary and keywords also help the agent find a document from a vague description later.
If the model can't be reached, the title comes from the file name and the summary from the first lines.
"""
import logging
import re

from pydantic import BaseModel, Field

from backend.library import config, naming

log = logging.getLogger(__name__)


class DocProfile(BaseModel):
    title: str = Field(description="3-10 word descriptive title in Title Case, no file extension")
    summary: str = Field(description="2-3 sentences: what the document is and what it covers")
    keywords: list[str] = Field(description="up to 8 key topics, names or terms")


PROMPT = """You name documents in a library. Read the opening of this document and return:
- title: a clear, specific title of 3-10 words in Title Case that tells the document apart from others
  (use the document's own title if it has a good one; include an organisation, year or version only when the
  document states it). No file extension, no quotes.
- summary: 2-3 sentences on what the document is and what it covers.
- keywords: up to 8 key topics, names or terms from it.

The title must differ from these titles already in the library: {existing}

File name: {filename}
Opening text:
<<<
{text}
>>>"""


def fallback_profile(filename: str, text: str) -> DocProfile:
    stem = naming.strip_extension(filename)
    title = re.sub(r"[_\-.]+", " ", stem).strip() or "Untitled Document"
    title = " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in title.split())
    first = re.sub(r"\s+", " ", text).strip()[:300]
    return DocProfile(title=title[: config.TITLE_MAX_CHARS], summary=first, keywords=[])


def _ask_model(prompt: str) -> DocProfile:
    from backend.library import day7  # noqa: F401  (sets day7's environment first)
    from backend.config.llm import structured

    from backend.library import limits

    return structured(DocProfile, fast=False, backup=True).invoke(prompt, config={"callbacks": [limits.counter()]})


def profile_document(filename: str, text: str, existing_titles: list[str], ask=None) -> tuple[DocProfile, bool]:
    """Returns (profile, written_by_model)."""
    ask = ask or _ask_model
    prompt = PROMPT.format(existing=", ".join(f'"{t}"' for t in existing_titles) or "(none)",
                           filename=filename, text=text[: config.TITLE_SOURCE_CHARS])
    try:
        profile = ask(prompt)
        profile.title = naming.clean_title(profile.title)
        profile.keywords = [k.strip() for k in profile.keywords if k.strip()][:8]
        return profile, True
    except Exception as e:
        log.warning("Title model unavailable for %s, using the file name: %s", filename, str(e)[:200])
        return fallback_profile(filename, text), False
