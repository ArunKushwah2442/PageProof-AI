
"""Retest previously incorrect/partial PageProof answers."""

import csv
import time
from pathlib import Path

from app.rag import VectorStore, QAEngine

DOC_ID = 2
BASE_DIR = Path(__file__).resolve().parent
OLD_RESULTS = BASE_DIR / "evaluation_results.csv"
NEW_RESULTS = BASE_DIR / "evaluation_failed_results.csv"

# Questions marked incorrect or partial in previous manual review.
RETEST_QUESTIONS = {
    "Who is credited as a contributor?",
    "When was the second edition published?",
    "What is Chapter 1 titled?",
    "What is Chapter 2 titled?",
    "What is Chapter 7 titled?",
    "What is Chapter 13 titled?",
    "What is ReLU?",
    "What is overfitting?",
    "What is a convolutional filter?",
}

if not OLD_RESULTS.exists():
    raise FileNotFoundError(OLD_RESULTS)

with OLD_RESULTS.open(
    "r", encoding="utf-8-sig", newline=""
) as file:
    old_rows = list(csv.DictReader(file))

questions = [
    row for row in old_rows
    if row["question"].strip() in RETEST_QUESTIONS
]

if len(questions) != len(RETEST_QUESTIONS):
    found = {row["question"].strip() for row in questions}
    missing = RETEST_QUESTIONS - found
    raise ValueError(f"Missing questions: {missing}")

store = VectorStore()
qa = QAEngine(store)

fieldnames = [
    "group",
    "question",
    "expected",
    "bot_answer",
    "source_pages",
    "error",
    "result",
]

results = []

print("=" * 55)
print("PAGEPROOF - TARGETED RETEST")
print(f"Document ID: {DOC_ID}")
print(f"Questions: {len(questions)}")
print("=" * 55)

for i, row in enumerate(questions, 1):
    question = row["question"].strip()

    print(f"\n[{i}/{len(questions)}] {question}")

    answer = ""
    pages = []
    error = ""
    status = "PENDING_REVIEW"

    try:
        answer, sources = qa.ask(
            question=question,
            doc_id=DOC_ID,
            history=[],
        )

        answer = str(answer)

        pages = sorted({
            str(source["page"])
            for source in (sources or [])
            if isinstance(source, dict)
            and source.get("page") is not None
        })

        print("Answer:", answer[:500])
        print("Sources:", ", ".join(pages) or "None")

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        status = "ERROR"
        print("ERROR:", error)

    results.append({
        "group": row["group"],
        "question": question,
        "expected": row["expected"],
        "bot_answer": answer,
        "source_pages": ", ".join(pages),
        "error": error,
        "result": status,
    })

    # Save after each question to preserve progress.
    with NEW_RESULTS.open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:
        writer = csv.DictWriter(
            file, fieldnames=fieldnames
        )
        writer.writeheader()
        writer.writerows(results)

    if i < len(questions):
        time.sleep(3)

print("\n" + "=" * 55)
print("RETEST COMPLETE")
print("Total:", len(results))
print("Errors:", sum(r["result"] == "ERROR" for r in results))
print("Pending review:", sum(
    r["result"] == "PENDING_REVIEW" for r in results
))
print("Saved:", NEW_RESULTS)
