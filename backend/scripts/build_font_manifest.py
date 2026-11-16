"""Write backend/library/fonts/manifest.json: the complete fonts the PDF editor may download when a PDF needs one the
machine doesn't have (backend/library/font_fetch.py), each with the one URL it comes from and its SHA-256.

Run it on copies downloaded by hand from the pinned sources below, then commit the manifest (never the fonts):

    python -m backend.scripts.build_font_manifest <folder>

<folder> holds cm-super.zip (CTAN, fonts/ps-type1/cm-super.zip), liberation-fonts-ttf-2.1.5.tar.gz (the GitHub
release) and the Carlito / Caladea .ttf files from the commits pinned below.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import zipfile
from pathlib import Path

from backend.library.pdf_edit import _ps_name, font_key

CM_SUPER = "https://mirrors.mit.edu/CTAN/fonts/ps-type1/cm-super/pfb/{file}"
LIBERATION = "https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz"
CARLITO = "https://raw.githubusercontent.com/google/fonts/cc39c4c2c304bc368c9b7c311ea2bcc6013299fe/ofl/carlito/{file}"
CALADEA = ("https://raw.githubusercontent.com/huertatipografica/Caladea/336a529cfad3d103d6527752686f8331d13e820a/"
           "fonts/ttf/{file}")
STYLES = {"Regular": "", "Bold": "bold", "Italic": "italic", "BoldItalic": "bolditalic"}
# Microsoft's fonts and their free metric-compatible copies (same letter widths, so lines set the same)
STAND_INS = {"LiberationSans": "arial", "LiberationSerif": "timesnewroman", "LiberationMono": "couriernew",
             "Carlito": "calibri", "Caladea": "cambria"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(folder: Path) -> dict:
    sources = {
        "cm-super": {"url": CM_SUPER, "license": "GPL-2.0-or-later with a font exception (embedding in documents)"},
        # stand_in: a look-alike of another font (same widths, different design): the user is told when it is used
        "liberation": {"url": LIBERATION, "redirect_host": "objects.githubusercontent.com", "license": "OFL-1.1",
                       "stand_in": True},
        "carlito": {"url": CARLITO, "license": "OFL-1.1", "stand_in": True},
        "caladea": {"url": CALADEA, "license": "OFL-1.1", "stand_in": True},
    }
    files: dict[str, dict] = {}
    names: dict[str, str] = {}

    with zipfile.ZipFile(folder / "cm-super.zip") as z:
        for info in z.infolist():
            if info.filename.startswith("cm-super/pfb/") and info.filename.endswith(".pfb"):
                name = info.filename.rsplit("/", 1)[1]
                files[name] = {"source": "cm-super", "sha256": _sha(z.read(info)), "size": info.file_size}
                names[font_key(name[:-4])] = name

    archive = folder / "liberation-fonts-ttf-2.1.5.tar.gz"
    sources["liberation"].update(sha256=_sha(archive.read_bytes()), size=archive.stat().st_size)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.endswith(".ttf"):
                name = member.name.rsplit("/", 1)[1]
                files[name] = {"source": "liberation", "member": member.name,
                               "sha256": _sha(tar.extractfile(member).read()), "size": member.size}

    for family, source in (("Carlito", "carlito"), ("Caladea", "caladea")):
        for style in STYLES:
            path = folder / f"{family}-{style}.ttf"
            files[path.name] = {"source": source, "sha256": _sha(path.read_bytes()), "size": path.stat().st_size}

    for name, entry in files.items():
        if entry["source"] == "cm-super":
            continue
        family, style = name[:-4].split("-")
        names[font_key(f"{family}-{style}")] = name
        names[STAND_INS[family] + STYLES[style]] = name
    return {"sources": sources, "files": dict(sorted(files.items())), "names": dict(sorted(names.items()))}


def main() -> None:
    folder = Path(sys.argv[1])
    manifest = build(folder)
    out = Path(__file__).resolve().parents[1] / "library" / "fonts" / "manifest.json"
    out.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"{len(manifest['files'])} files, {len(manifest['names'])} names -> {out}")


if __name__ == "__main__":
    main()
