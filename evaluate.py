
import csv
import time
from pathlib import Path

from app.rag import VectorStore, QAEngine

# ---------------- CONFIG ----------------

DOC_ID = 2  # Deep Learning PDF document ID

BASE_DIR = Path(__file__).resolve().parent

QUESTIONS_FILE = (
    BASE_DIR / "evaluation_questions.csv"
)

RESULTS_FILE = BASE_DIR / "evaluation_results.csv"

DELAY_SECONDS = 2

# ---------------- INITIALIZE ----------------

if not QUESTIONS_FILE.exists():
    raise FileNotFoundError(
        f"Questions CSV not found: {QUESTIONS_FILE}"
    )

store = VectorStore()
qa = QAEngine(store)

with QUESTIONS_FILE.open(
    "r", encoding="utf-8-sig", newline=""
) as file:
    questions = list(csv.DictReader(file))

if not questions:
    raise ValueError("Questions CSV is empty.")

required_columns = {"group", "question", "expected"}

missing = required_columns - set(questions[0].keys())

if missing:
    raise ValueError(
        f"Missing CSV columns: {sorted(missing)}"
    )

print("=" * 60)
print("PAGEPROOF - AUTOMATED RAG EVALUATION")
print("=" * 60)
print(f"Document ID: {DOC_ID}")
print(f"Total Questions: {len(questions)}")
print("=" * 60)

# ---------------- EVALUATION ----------------

results = []

fieldnames = [
    "group",
    "question",
    "expected",
    "bot_answer",
    "source_pages",
    "error",
    "result",
]

for i, item in enumerate(questions, start=1):

    group = item["group"].strip()
    question = item["question"].strip()
    expected = item["expected"].strip()

    print(f"\n[{i}/{len(questions)}] [{group}]")
    print("Question:", question)

    answer = ""
    pages = []
    error = ""
    result = "PENDING_REVIEW"

    try:
        # Independent question: no chat history
        answer, sources = qa.ask(
            question=question,
            doc_id=DOC_ID,
            history=[],
        )

        answer = str(answer)

        for source in sources or []:
            if isinstance(source, dict):
                page = source.get("page")
                if page is not None:
                    pages.append(str(page))

        pages = sorted(set(pages))

        # Automatic grading for out-of-document questions
        if group == "not_in_doc":
            refusal = (
                "i could not find this in the document"
            )

            if refusal in answer.lower():
                result = "PASS"
            else:
                result = "FAIL"

        print("Answer:", answer[:300])
        print("Sources:", ", ".join(pages) or "None")
        print("Result:", result)

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result = "ERROR"

        print("ERROR:", error)

    results.append({
        "group": group,
        "question": question,
        "expected": expected,
        "bot_answer": answer,
        "source_pages": ", ".join(pages),
        "error": error,
        "result": result,
    })

    # Save progress after every question
    with RESULTS_FILE.open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(results)

    if i < len(questions):
        time.sleep(DELAY_SECONDS)

# ---------------- SUMMARY ----------------

passed = sum(
    r["result"] == "PASS" for r in results
)

failed = sum(
    r["result"] == "FAIL" for r in results
)

errors = sum(
    r["result"] == "ERROR" for r in results
)

pending = sum(
    r["result"] == "PENDING_REVIEW"
    for r in results
)

print("\n" + "=" * 60)
print("EVALUATION SUMMARY")
print("=" * 60)

print("Total:", len(results))
print("Auto-PASS:", passed)
print("Auto-FAIL:", failed)
print("Errors:", errors)
print("Pending manual/semantic review:", pending)

print("\nResults saved to:")
print(RESULTS_FILE.resolve())

print("\nNOTE:")
print("Final accuracy cannot be calculated until")
print("all PENDING_REVIEW answers are graded.")
