# Test corpus

36 short documents chosen to exercise every renaming scenario: 20 health reports (WHO) and 16 AI/ML documents.
They are downloaded but **not ingested**. Many small files were chosen over a few big ones.

Recreate the files: `.venv\Scripts\python -I scripts\fetch_test_corpus.py` (sources and licences: `manifest.json`).

## Cost of ingesting all of it

649 chunks, so **649 Gemini embedding requests** (free tier: 100 a minute, 1,000 a day) plus **36 Groq requests**
for titles. That leaves ~350 Gemini requests that day for chat searches (1 per new description, repeats are cached).
At 100 a minute, ingesting takes about 7-8 minutes.

| Group | Files | Chunks |
|---|---|---|
| WHO Disease Outbreak News (15) + fact sheets (5) | `health/`, `outbreaks/` | 349 |
| Wikipedia articles (14, cut to ~9,000 characters) + 2 arXiv papers | `ml/`, `rl/` | 300 (the papers: 112) |

Formats: 21 Markdown, 7 TXT, 6 DOCX, 2 PDF.

```powershell
.venv\Scripts\python -m renamer ingest test_corpus\files
```

## What each scenario uses

### 1. Titles added on ingest
Files with names that say nothing about their content. Each should get a descriptive title:

| File | Content |
|---|---|
| `health/doc1.md` | Avian influenza A(H5N5), USA |
| `health/download (3).md` | Avian influenza A(H5N1), Cambodia |
| `health/untitled.txt` | Measles, USA |
| `health/final_v2_FINAL.md` | Rabies, Timor-Leste |
| `health/New Document.docx` | Diabetes fact sheet |
| `ml/Document (2).md` | Autoencoder |
| `ml/ml_stuff.txt` | Gradient boosting |
| `ml/1706.03762.pdf` | "Attention Is All You Need" (Transformer paper) |
| `ml/1502.03167.pdf` | Batch normalization paper |
| `ml/notes.txt`, `rl/notes.txt` | LSTM / reinforcement learning (same file name) |
| `health/report.md`, `outbreaks/report.md` | MERS, Saudi Arabia (same file name **and** same WHO title, Mar vs May 2025) |

Also check that the near-identical WHO reports end up with different titles (the title writer is told the existing
titles; the code adds "(2)" if it still repeats one): the two MERS reports, and the two Marburg Rwanda reports
(`marburg_rwanda.md`, `marburg_rwanda_update.docx`).

### 2. Rename by name
- "Rename tb.md to Global Tuberculosis Facts"
- "Rename the Random Forest document to Random Forests Explained"
- "Add '(WHO)' to the end of the hypertension fact sheet's name" (appending to a title)

### 3. Rename by content ("the document containing abc or xyz")
Each should find exactly one document:
- "Rename the document about rabies in Timor-Leste to Rabies Outbreak 2025"
- "Rename the doc that mentions anthrax or Thailand to Anthrax Report"
- "Rename the paper that introduced batch normalization to BatchNorm Paper"
- "Change the name of the document about k-means or centroid clustering to Clustering Notes"
- "Rename the cholera report from Angola to Cholera Angola 2025"

### 4. Ambiguous description: the agent must ask, not choose
| Ask about | Fits |
|---|---|
| "the bird flu report" | Cambodia H5N1, Mexico H5N1, USA H5N5 |
| "the measles document" | Morocco, USA, WHO fact sheet |
| "the malaria one" | Ethiopia outbreak, WHO fact sheet |
| "the Nipah report" | India, Bangladesh |
| "the transformer document" | Wikipedia article, the 1706.03762 paper (maybe the LLM article) |
| "the tree-based model doc" | decision tree, random forest, gradient boosting |
| "the recurrent network one" | RNN, LSTM |
| "the generative model doc" | GAN, autoencoder |

Then give more detail ("the one from Mexico", "the original paper", "the Bangladesh one") and the rename should go
through. More detail at the start should work straight away: "the H5N1 report from Cambodia".

### 5. Same names
- "Rename report.md to MERS Update" → two documents have that file name: it must ask which.
- "Rename notes.txt to LSTM Notes" → same.
- "Rename the MERS report" / "the Marburg Rwanda report" → two reports with near-identical titles.
- Rename one document to another's current title (e.g. the anthrax report to the cholera report's title) → it must
  ask for confirmation; a "yes" in the next message goes through and the file is stored as "Name (2).ext".

### 6. Nothing matches
- "Rename the document about quantum computing" → it should say nothing matches.
