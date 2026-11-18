"""Download the test corpus into test_corpus/files (nothing is ingested or embedded).

    .venv\\Scripts\\python -I scripts\\fetch_test_corpus.py

Sources: WHO Disease Outbreak News (API) and fact sheets (web pages), Wikipedia articles (API), two arXiv papers.
Each document is saved under a file name picked for a test scenario (see MANIFEST below and test_corpus/README.md):
meaningless names to test titling, the same name twice in different folders, near-identical WHO titles, and
clusters of related documents for vague descriptions. test_corpus/manifest.json records every file's source.
"""
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "test_corpus"
FILES = ROOT / "files"
UA = "RenamingPipeline-test-corpus/0.1 (local onboarding project; one-off download)"

# (saved as, kind, source id, scenarios)
MANIFEST = [
    # ---------------- health: WHO Disease Outbreak News
    ("health/report.md", "don", "2025-DON560", "same title: MERS Saudi Arabia (Mar 2025); same file name as outbreaks/report.md"),
    ("outbreaks/report.md", "don", "2025-DON569", "same title: MERS Saudi Arabia (May 2025); same file name as health/report.md"),
    ("health/marburg_rwanda.md", "don", "2024-DON544", "same title: Marburg Rwanda (Nov 2024)"),
    ("health/marburg_rwanda_update.docx", "don", "2024-DON548", "same title: Marburg Rwanda (Dec 2024)"),
    ("health/download (3).md", "don", "2025-DON575", "bird flu cluster: H5N1 Cambodia; meaningless name"),
    ("health/H5N1_mx.txt", "don", "2025-DON564", "bird flu cluster: H5N1 Mexico"),
    ("health/doc1.md", "don", "2025-DON590", "bird flu cluster: H5N5 USA; meaningless name"),
    ("health/measles_morocco.md", "don", "2025-DON568", "measles cluster: Morocco"),
    ("health/untitled.txt", "don", "2025-DON561", "measles cluster: USA; meaningless name"),
    ("health/nipah_india.md", "don", "2025-DON577", "Nipah cluster: India"),
    ("health/nipah_bangladesh.docx", "don", "2025-DON582", "Nipah cluster: Bangladesh"),
    ("health/final_v2_FINAL.md", "don", "2025-DON576", "unique content: rabies Timor-Leste; meaningless name"),
    ("health/anthrax_thailand.md", "don", "2025-DON573", "unique content: anthrax Thailand"),
    ("health/cholera_angola.txt", "don", "2025-DON562", "unique content: cholera Angola"),
    ("health/malaria_ethiopia.md", "don", "2024-DON542", "malaria cluster: outbreak in Ethiopia"),
    # ---------------- health: WHO fact sheets
    ("health/malaria_fact_sheet.md", "factsheet", "malaria", "malaria cluster: global fact sheet"),
    ("health/measles.md", "factsheet", "measles", "measles cluster: global fact sheet"),
    ("health/tb.md", "factsheet", "tuberculosis", "unique content: tuberculosis"),
    ("health/New Document.docx", "factsheet", "diabetes", "unique content: diabetes; meaningless name"),
    ("health/hypertension.md", "factsheet", "hypertension", "unique content: hypertension"),
    # ---------------- AI/ML: Wikipedia
    ("ml/cnn.md", "wiki", "Convolutional neural network", "neural network cluster"),
    ("ml/rnn.md", "wiki", "Recurrent neural network", "neural network cluster; sequence models with LSTM"),
    ("ml/notes.txt", "wiki", "Long short-term memory", "neural network cluster; same file name as rl/notes.txt"),
    ("ml/transformer_wiki.md", "wiki", "Transformer (deep learning architecture)", "transformer cluster with 1706.03762.pdf"),
    ("ml/decision_tree.md", "wiki", "Decision tree learning", "tree cluster"),
    ("ml/random_forest.md", "wiki", "Random forest", "tree cluster; ensembles with gradient boosting"),
    ("ml/ml_stuff.txt", "wiki", "Gradient boosting", "tree cluster; meaningless name"),
    ("rl/notes.txt", "wiki", "Reinforcement learning", "RL cluster; same file name as ml/notes.txt"),
    ("rl/q_learning.docx", "wiki", "Q-learning", "RL cluster"),
    ("ml/gan.md", "wiki", "Generative adversarial network", "generative cluster with autoencoder"),
    ("ml/Document (2).md", "wiki", "Autoencoder", "generative cluster; meaningless name"),
    ("ml/kmeans.txt", "wiki", "K-means clustering", "unique content: clustering"),
    ("ml/llm.md", "wiki", "Large language model", "unique-ish: LLMs (touches transformers)"),
    ("ml/overfitting.md", "wiki", "Overfitting", "unique content: overfitting"),
    # ---------------- AI/ML: arXiv papers (real PDFs)
    ("ml/1706.03762.pdf", "arxiv", "1706.03762", "transformer cluster: 'Attention Is All You Need'; number-only name"),
    ("ml/1502.03167.pdf", "arxiv", "1502.03167", "unique content: batch normalization; number-only name"),
]

WIKI_MAX_CHARS = 9000  # keep articles short: the budget favours many documents over long ones
WIKI_SKIP = {"see also", "references", "external links", "further reading", "notes", "bibliography", "sources",
             "citations", "footnotes"}
DON_FIELDS = ["Summary", "Overview", "Epidemiology", "Response", "Assessment", "Advice"]


def get(url: str, params: dict | None = None) -> bytes:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(20 * (attempt + 1) if getattr(e, "code", None) == 429 else 3)  # 429: rate-limited


class _Text(HTMLParser):
    """HTML -> plain text with markdown headings and list bullets. Keeps text inside `start_class` only, if given."""

    BLOCK = {"p", "div", "section", "table", "tr", "br"}

    def __init__(self, start_class: str | None = None):
        super().__init__(convert_charrefs=True)
        self.start_class, self.depth, self.inside = start_class, 0, start_class is None
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        if not self.inside and self.start_class and self.start_class in cls.split():
            self.inside, self.depth = True, 0
        if not self.inside:
            return
        if tag == "div":
            self.depth += 1
        if tag in ("script", "style", "figure"):
            self.skip += 1
        elif tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n" + "#" * min(int(tag[1]) + 1, 4) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n\n" if tag != "br" else "\n")

    def handle_endtag(self, tag):
        if not self.inside:
            return
        if tag in ("script", "style", "figure"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("h1", "h2", "h3", "h4", "p", "ul", "ol"):
            self.out.append("\n\n")
        if tag == "div" and self.start_class:
            self.depth -= 1
            if self.depth < 0:
                self.inside = False

    def handle_data(self, data):
        if self.inside and not self.skip:
            self.out.append(data)

    def text(self) -> str:
        t = "".join(self.out).replace("\xa0", " ")
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r" *\n *", "\n", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def html_to_text(raw: str, start_class: str | None = None) -> str:
    p = _Text(start_class)
    p.feed(raw)
    return p.text()


def fetch_don(don_id: str) -> tuple[str, str, str]:
    data = json.loads(get("https://www.who.int/api/news/diseaseoutbreaknews",
                          {"sf_culture": "en", "$filter": f"UrlName eq '{don_id}'"}))["value"][0]
    title = html.unescape(data["Title"]).replace("–", "-")
    date = data["PublicationDateAndTime"][:10]
    parts = [f"# {title}", f"Disease Outbreak News, World Health Organization, {date}"]
    for field in DON_FIELDS:
        body = (data.get(field) or "").strip()
        if body:
            heading = "Situation at a glance" if field == "Summary" else field
            parts.append(f"## {heading}\n\n{html_to_text(body)}")
    url = f"https://www.who.int/emergencies/disease-outbreak-news/item/{don_id}"
    return "\n\n".join(parts), title, url


def fetch_factsheet(slug: str) -> tuple[str, str, str]:
    url = f"https://www.who.int/news-room/fact-sheets/detail/{slug}"
    raw = get(url).decode("utf-8")
    title = html.unescape(re.search(r"<title>(.*?)</title>", raw, re.S).group(1)).split("|")[0].strip()
    body = html_to_text(raw, "sf-detail-body-wrapper")
    body = re.split(r"\n#+ *(?:References|Related)\b", body)[0]
    return f"# {title}\n\nWHO fact sheet\n\n{body}", title, url


def fetch_wiki(title: str) -> tuple[str, str, str]:
    data = json.loads(get("https://en.wikipedia.org/w/api.php", {
        "action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1, "titles": title, "format": "json"}))
    page = next(iter(data["query"]["pages"].values()))
    text = page["extract"]
    # Keep the lead and the first sections, whole, up to WIKI_MAX_CHARS.
    sections = re.split(r"\n(?=={2,} )", text)
    kept, size = [], 0
    for s in sections:
        heading = re.match(r"={2,} (.*?) ={2,}", s)
        if heading and heading.group(1).strip().lower() in WIKI_SKIP:
            continue
        s = re.sub(r"^(={2,}) (.*?) \1", lambda m: "#" * len(m.group(1)) + " " + m.group(2), s, flags=re.M).strip()
        if not s or (kept and size + len(s) > WIKI_MAX_CHARS):
            if kept:
                break
        kept.append(s)
        size += len(s)
    url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(page["title"].replace(" ", "_"))
    body = "\n\n".join(kept)[: WIKI_MAX_CHARS + 2000]
    return f"# {page['title']}\n\nFrom Wikipedia, the free encyclopedia (CC BY-SA 4.0): {url}\n\n{body}", page["title"], url


def save_text(path: Path, text: str) -> None:
    if path.suffix == ".docx":
        import docx
        d = docx.Document()
        for block in text.split("\n\n"):
            m = re.match(r"^(#+) (.*)", block)
            if m:
                d.add_heading(m.group(2), level=min(len(m.group(1)), 3))
            else:
                for line in block.splitlines():
                    if line.startswith("- "):
                        d.add_paragraph(line[2:], style="List Bullet")
                    elif line.strip():
                        d.add_paragraph(line)
        d.save(path)
    else:
        if path.suffix == ".txt":  # plain text: no markdown marks
            text = re.sub(r"^#+ ", "", text, flags=re.M)
        path.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    FILES.mkdir(parents=True, exist_ok=True)
    previous = {}
    if (ROOT / "manifest.json").exists():
        previous = {m["file"]: m for m in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))}
    manifest, failed = [], 0
    for saved_as, kind, source, scenario in MANIFEST:
        path = FILES / saved_as
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and saved_as in previous:  # downloaded on an earlier run
            manifest.append(previous[saved_as])
            continue
        try:
            if kind == "arxiv":
                url = f"https://arxiv.org/pdf/{source}"
                path.write_bytes(get(url))
                title = f"arXiv:{source}"
            else:
                text, title, url = {"don": fetch_don, "factsheet": fetch_factsheet, "wiki": fetch_wiki}[kind](source)
                save_text(path, text)
        except Exception as e:
            failed += 1
            print(f"FAILED {saved_as}: {e}")
            continue
        license_ = {"don": "WHO, CC BY-NC-SA 3.0 IGO", "factsheet": "WHO, CC BY-NC-SA 3.0 IGO",
                    "wiki": "Wikipedia, CC BY-SA 4.0", "arxiv": "arXiv (authors' licence)"}[kind]
        manifest.append({"file": saved_as, "source_title": title, "url": url, "license": license_, "scenario": scenario})
        print(f"ok  {saved_as:40} {path.stat().st_size:>8} bytes  {title}")
        time.sleep(0.5)
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(manifest)} files, {failed} failed -> {FILES}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
