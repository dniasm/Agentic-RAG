"""Recount the pages of Word, text and Markdown documents (the loader counted each as one page): Word's own count,
else the pages of the PDF they are previewed and downloaded as. Their passages lose the "page 1" every citation
showed. PDFs are left alone. No model calls. Run once, with the server stopped (the vector store is opened here):
python -m backend.scripts.recount_pages"""
from backend.library import catalog, export, library
from backend.rag import vectorstore


def main() -> None:
    catalog.init()
    changed = 0
    for doc in catalog.list_documents():
        if doc["ext"] == ".pdf" or doc["status"] != "indexed":
            continue
        pages = export.page_count(doc, doc["pages"] or 1)
        if pages != doc["pages"]:
            catalog.update_document(doc["id"], pages=pages)
            changed += 1
        library._set_chunk_fields(doc["id"], doc["owner"], page=None)
    vectorstore.close_client()   # before the interpreter shuts down, or closing the local store fails
    print(f"{changed} documents recounted")


if __name__ == "__main__":
    main()
