"""Build the Wikipedia part of the benchmark corpus.

Fetches plain-text article extracts through Wikipedia's official API (no scraping) and saves each as a
Markdown file under benchmark/corpus/wikipedia/. Articles are trimmed at a paragraph boundary so the
corpus stays small enough for the free-tier Groq/Gemini quotas used by the pipeline.

Content is from Wikipedia, licensed CC BY-SA 4.0; each file records its source URL.

    python benchmark/build_wiki_corpus.py
"""
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config.settings import settings  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "corpus" / "wikipedia"
MAX_CHARS = 9000
API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": f"AgenticRAG-benchmark/1.0 ({settings.bot_contact} - local onboarding project) python-httpx"}

ARTICLES = [
    "Tf–idf", "Okapi BM25", "Information retrieval", "Vector space model", "Inverted index",
    "Word embedding", "Word2vec", "Latent semantic analysis", "Cosine similarity", "Stemming",
    "Stop word", "N-gram", "Named-entity recognition", "Part-of-speech tagging",
    "Support vector machine", "Naive Bayes classifier", "Precision and recall", "Mean reciprocal rank",
]


def fetch(title: str) -> tuple[str, str]:
    data = httpx.get(API, headers=HEADERS, timeout=20, params={
        "action": "query", "prop": "extracts|info", "explaintext": 1, "inprop": "url",
        "titles": title, "redirects": 1, "format": "json",
    }).json()
    page = next(iter(data["query"]["pages"].values()))
    return page.get("extract", ""), page.get("fullurl", "")


def clean_math(text: str) -> str:
    """Wikipedia's plain-text export renders each formula as dozens of indented one-token lines followed
    by its LaTeX source in `{\\displaystyle ...}`. Keep only the LaTeX, inline, as $...$."""
    out = []
    for line in text.split("\n"):
        stripped = line.strip()
        if "{\\displaystyle" in stripped:
            latex = re.sub(r"^\{\\displaystyle\s*(.*)\}$", r"\1", stripped)
            if out and out[-1] and not out[-1].endswith("\n"):
                out[-1] = out[-1].rstrip() + f" ${latex}$"
            else:
                out.append(f"${latex}$")
            continue
        # Fragments of the spread-out rendering: short indented tokens, or deeply indented single words.
        if (line.startswith("  ") and len(stripped) <= 3) or (line.startswith("      ") and " " not in stripped):
            continue
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"\$\s*\n+\s*(?=[a-z,.;:)])", "$ ", text)  # re-join sentences broken after a formula
    return re.sub(r"\n{3,}", "\n\n", text)


def trim(text: str) -> str:
    # Drop reference-style tail sections and cut at a paragraph boundary.
    text = re.split(r"\n==\s*(See also|References|Notes|External links|Further reading)\s*==", text)[0]
    if len(text) <= MAX_CHARS:
        return text.strip()
    cut = text.rfind("\n\n", 0, MAX_CHARS)
    return text[: cut if cut > MAX_CHARS * 0.6 else MAX_CHARS].strip()


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for title in ARTICLES:
        text, url = fetch(title)
        if not text:
            print(f"  ! no text for {title}")
            continue
        body = trim(clean_math(text))
        body = re.sub(r"\n(=+)\s*(.*?)\s*=+\n", lambda m: f"\n{'#' * (len(m.group(1)) + 0)} {m.group(2)}\n", body)
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        (OUT_DIR / f"{slug}.md").write_text(
            f"# {title}\n\nSource: {url} (Wikipedia, CC BY-SA 4.0)\n\n{body}\n", encoding="utf-8")
        total += len(body)
        print(f"  {title:<28} {len(body):>6,} chars")
        time.sleep(0.5)  # be polite to the API
    print(f"Saved {len(ARTICLES)} articles, {total:,} chars, to {OUT_DIR}")


if __name__ == "__main__":
    main()
