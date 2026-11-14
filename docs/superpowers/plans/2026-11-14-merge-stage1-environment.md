# Merge stage 1: one environment. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** day7's venv can run both projects' code and tests under pytest.

**Architecture:** Add the renamer's three extra packages to day7's pinned `requirements.txt` (a full freeze), and pytest to a new `requirements-dev.txt`. No product code changes.

**Tech Stack:** Python 3.14.5, pip, pytest 9.2.0.

**Spec:** `docs/superpowers/specs/2026-11-14-merge-renamer-design.md` (stage 1)

## Global Constraints

- Pins come from the renamer's venv: `pymupdf==2.1`, `fpdf2==2.9.0`, `pyphen==0.18.1`, `pytest==9.2.0`.
- day7's pins win on any shared package, and so do versions day7's venv already has: Pillow stays `12.3.0`.
- `langchain-openai` is not added (nothing imports it).
- pytest goes only in `requirements-dev.txt`.
- Never commit `frontend/vite.config.js`. Commit only when the user asks.

## Review Focus

- A pymupdf transitive dependency that upgrades a day7 pin (numpy, PyYAML, packaging): after the install, `pip check` is clean and no line already in `requirements.txt` changed.
- day7's tests behave differently under pytest than under unittest (import order, cwd): pytest must report 129 passed.

---

### Task 1: Pin the new packages and run day7's suite under pytest

**Files:**
- Modify: `requirements.txt` (append new pins, keep alphabetical order)
- Create: `requirements-dev.txt` (`-r requirements.txt` + `pytest==9.2.0`)

- [ ] **Step 1:** Record the baseline: `.venv\Scripts\python -m pip freeze > <scratchpad>\freeze_before.txt`.
- [ ] **Step 2:** Install: `.venv\Scripts\python -m pip install pymupdf==2.1 fpdf2==2.9.0 pyphen==0.18.1 pytest==9.2.0 pillow==12.3.0`.
- [ ] **Step 3:** `pip check` is clean. Diff the freeze against the baseline: only additions, and no already-pinned version changed.
- [ ] **Step 4:** Add the added runtime packages (pytest and its deps go in requirements-dev.txt) to `requirements.txt` with exact pins.
- [ ] **Step 5:** `.venv\Scripts\python -m pytest -q backend/tests` gives 129 passed. `python -m unittest discover backend/tests -p "test_*.py"` still gives OK, 129.
- [ ] **Step 6:** In RenamingPipeline: `.venv\Scripts\python -m pytest -q` gives 165 passed (unchanged).
- [ ] **Step 7:** Importing `fitz`, `fpdf` and `pyphen` in day7's venv works.
