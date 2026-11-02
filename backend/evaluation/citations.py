"""Citation checks in code: no model, no tokens.

For an answer written from numbered evidence ([1], [2], ...), check that:
    1. it is cited      every factual sentence is covered by a citation in its sentence or its paragraph
    2. citations exist  every [n] points at a passage the writer was given
    3. content matches  the sentence's key words appear in the passages it cites, and its numbers appear exactly

A sentence's words are compared with the cited passages after the same tokenizer the keyword search uses
(lowercase, stop words removed, "tf-idf" == "tfidf"), with a light stem match ("classified" ~ "classification"),
so rewording is tolerated but a claim about something the passage doesn't mention is not.

This catches invented or misattributed claims. It cannot judge whether an answer is complete, or catch a claim
that uses the passage's words but reverses their meaning: those still need a model.
"""
import re
from dataclasses import dataclass, field

from backend.rag.sparse import tokenize

CITATION = re.compile(r"\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(*])")
_MARKDOWN = re.compile(r"^\s*(?:[-*+]|\d+[.)]|#+)\s*|[*_`>|]")
# A number: thousands separators only before exactly 3 digits ("1,000", but "1.2, 2.0" is two numbers). Ordinals
# ("20th"), decades ("1970s") and the second half of a range ("1970-80s") are skipped: they are written too many
# ways to compare exactly.
_NUMBER = re.compile(r"(?<![\w\-‐‑–—.])\d+(?:,\d{3})*(?:\.\d+)?(?![\d.,]*\d)(?!\s?(?:st|nd|rd|th|s)\b)")

MIN_CONTENT_TOKENS = 4      # shorter sentences ("Yes.", "In short:") don't need a citation
# Share of a sentence's key words that must appear in its cited passages. Measured on 23 real answers
# (benchmark/run_checker.py): invented claims scored 0-0.14 (two outliers, 0.33 and 0.5); real cited sentences had
# a median of 0.79, and the few below 0.4 were mostly real problems (outside knowledge, an unsupported opinion).
SUPPORT_THRESHOLD = 0.4
STEM = 5                    # "classified" and "classification" match on their first 5 letters
# Sentences that don't need their own citation: lead-ins to a list ("The stack consists of:"), headings, and
# closing summaries of what was already cited ("Together, these…", "In short, …").
_LEAD_IN = re.compile(r":\s*$")
_HEADING = re.compile(r"^\s*(?:\*\*[^*]+\*\*|__[^_]+__)\s*:?\s*$|^\s*#+\s")
_FORMULA = re.compile(r"\\(?:text|frac|times|log|sum|mathrm|cdot)\b|\\\[|\\\(|\$\$")  # LaTeX formula lines
# A name with a number in it, after tokenizing: "bm25", "covid19" (from COVID-19), "gpt4". Not "20th" or "k1".
_IDENTIFIER = re.compile(r"^[a-z]{2,}\d+[a-z\d]*$")
# Numbers in the passages are matched loosely (any form), so only numbers the passage really lacks are flagged.
_ANY_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_SUMMARY = re.compile(r"^\W*(?:thus|so|overall|together|therefore|hence|in (?:short|summary|sum|conclusion)|"
                      r"in other words|taken together|altogether|all in all)\b", re.I)
# Sentences that talk about the evidence instead of making a claim from it.
_META = re.compile(r"\b(documents?|excerpts?|sources?|passages?|provided (text|material))\b.*\b(do not|don't|does not|doesn't|"
                   r"not (cover|mention|say|include|contain|specify|state)|no (information|mention|details?))\b", re.I)


@dataclass
class Sentence:
    text: str
    citations: list[int]
    support: float | None = None
    missing_numbers: list[str] = field(default_factory=list)


@dataclass
class CitationReport:
    ok: bool
    sentences: int                      # sentences that make a claim (need a citation)
    uncited: list[str]                  # claims with no citation in their sentence or paragraph
    invalid: list[int]                  # [n] with no passage n
    unsupported: list[Sentence]         # cited, but the cited passages don't contain the claim
    support: float                      # mean support over cited claims (1.0 = every key word found)

    @property
    def problems(self) -> list[str]:
        out = []
        if self.invalid:
            out.append(f"citations to passages that don't exist: {sorted(set(self.invalid))}")
        if self.uncited:
            out.append(f"{len(self.uncited)} claim(s) without a citation")
        if self.unsupported:
            out.append(f"{len(self.unsupported)} cited claim(s) not found in the cited passages")
        return out


def _cited(text: str) -> list[int]:
    return [int(n) for group in CITATION.findall(text) for n in group.split(",")]


def _keywords(text: str) -> list[str]:
    return [t for t in tokenize(CITATION.sub(" ", text)) if not t.isdigit()]


def _numbers(text: str) -> list[str]:
    """Numbers that state a fact (not citation markers, not list numbering), normalised: 10,000,000 -> 10000000."""
    body = CITATION.sub(" ", _MARKDOWN.sub(" ", text))
    return [n.replace(",", "").replace(" ", "") for n in _NUMBER.findall(body) if len(n.replace(",", "")) >= 2]


def _found(token: str, words: set[str], stems: set[str]) -> bool:
    return token in words or (len(token) >= STEM and token[:STEM] in stems)


def split_sentences(answer: str, blocks: dict | None = None) -> list[tuple[int, str]]:
    """(paragraph number, sentence) for every sentence; list items and table rows count as paragraphs.
    If `blocks` is given, it is filled with paragraph number -> block number (text between blank lines)."""
    out = []
    paragraph = 0
    for b, block in enumerate(re.split(r"\n\s*\n", answer.strip())):
        lines = block.split("\n")
        # a list or table: every line is its own paragraph (a citation on one item doesn't cover the next)
        per_line = len(lines) > 1 and all(_MARKDOWN.match(line) or not line.strip() for line in lines if line.strip())
        units = lines if per_line else [" ".join(lines)]
        for unit in units:
            if not unit.strip():
                continue
            paragraph += 1
            if blocks is not None:
                blocks[paragraph] = b
            for s in _SENTENCE.split(unit.strip()):
                if s.strip():
                    out.append((paragraph, s.strip()))
    return out


def check(answer: str, evidence: list[str], threshold: float = SUPPORT_THRESHOLD) -> CitationReport:
    """evidence[i] is the text of passage [i + 1]."""
    passage_words = [set(tokenize(text)) for text in evidence]
    passage_numbers = [set(n.replace(",", "") for n in _ANY_NUMBER.findall(text)) | {
        n.replace(" ", "") for n in re.findall(r"\d{1,3}(?: \d{3})+", text)} for text in evidence]
    block_of: dict[int, int] = {}
    sentences = split_sentences(answer, block_of)
    by_paragraph: dict[int, list[int]] = {}
    by_block: dict[int, list[int]] = {}
    for p, s in sentences:
        by_paragraph.setdefault(p, []).extend(_cited(s))
        by_block.setdefault(block_of[p], []).extend(_cited(s))

    all_cited = sorted({n for ns in by_paragraph.values() for n in ns})
    claims, uncited, invalid, unsupported, supports = 0, [], [], [], []
    for p, s in sentences:
        own = _cited(s)
        invalid += [n for n in own if not 1 <= n <= len(evidence)]
        keywords = _keywords(s)
        if len(keywords) < MIN_CONTENT_TOKENS or _META.search(s):
            continue
        if _SUMMARY.match(s) or _FORMULA.search(s) or (not own and (_LEAD_IN.search(s) or _HEADING.match(s))):
            continue  # a closing summary, formula, lead-in or heading: no claim of its own to check
        claims += 1
        # Its own citation, else its paragraph's, else its block's (a list cited once covers its items).
        inherited = by_paragraph.get(p) or by_block.get(block_of[p], [])
        if not own and not inherited and (p, s) in (sentences[0], sentences[-1]):
            # An uncited opening or closing sentence usually introduces or sums up the cited body: check it
            # against everything the answer cites (an invented sentence still doesn't match).
            inherited = all_cited
        cites = [n for n in (own or inherited) if 1 <= n <= len(evidence)]
        if not cites:
            if not own and not inherited:
                uncited.append(s)
            continue
        words = set().union(*(passage_words[n - 1] for n in cites))
        stems = {w[:STEM] for w in words if len(w) >= STEM}
        support = sum(_found(t, words, stems) for t in keywords) / len(keywords)
        numbers = set().union(*(passage_numbers[n - 1] for n in cites))
        missing = [n for n in _numbers(s) if n not in numbers]
        # Names with digits in them (BM25, COVID-19, GPT-4, ISO 27001) must appear exactly, like numbers.
        missing += [t for t in keywords if _IDENTIFIER.match(t) and t not in words]
        supports.append(support)
        if support < threshold or missing:
            unsupported.append(Sentence(s, cites, round(support, 2), missing))

    return CitationReport(
        ok=not (uncited or invalid or unsupported),
        sentences=claims,
        uncited=uncited,
        invalid=invalid,
        unsupported=unsupported,
        support=round(sum(supports) / len(supports), 3) if supports else 1.0,
    )
