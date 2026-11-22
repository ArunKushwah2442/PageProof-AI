
import csv
import time
from pathlib import Path

from app.rag import VectorStore, QAEngine

BASE_DIR = Path(__file__).resolve().parent
RESULTS_FILE = BASE_DIR / "evaluation_results.csv"
DOC_ID = 2

with RESULTS_FILE.open("r", encoding="utf-8-sig", newline="") as f:
    results = list(csv.DictReader(f))

errors = [
    i for i, row in enumerate(results)
    if row["result"] == "ERROR"
]

print(f"Total questions: {len(results)}")
print(f"Questions to retry: {len(errors)}")

if not errors:
    print("No errors to retry.")
    raise SystemExit

store = VectorStore()
qa = QAEngine(store)

fieldnames = list(results[0].keys())

for count, i in enumerate(errors, 1):
    row = results[i]
    question = row["question"]

    print(f"\n[{count}/{len(errors)}] {question}")

    try:
        answer, sources = qa.ask(
            question=question,
            doc_id=DOC_ID,
            history=[]
        )

        pages = []
        for source in sources or []:
            if isinstance(source, dict):
                page = source.get("page")
                if page is not None:
                    pages.append(str(page))

        row["bot_answer"] = str(answer)
        row["source_pages"] = ", ".join(sorted(set(pages)))
        row["error"] = ""

        refusal = "i could not find this in the document"

        if row["group"] == "not_in_doc":
            row["result"] = (
                "PASS"
                if refusal in str(answer).lower()
                else "FAIL"
            )
        else:
            row["result"] = "PENDING_REVIEW"

        print("Result:", row["result"])

    except Exception as e:
        print("ERROR:", e)
        row["error"] = f"{type(e).__name__}: {e}"

        if "rate limit" in str(e).lower() or "429" in str(e):
            print("Rate limit reached. Stopping retries.")
            break

    finally:
        with RESULTS_FILE.open(
            "w", encoding="utf-8-sig", newline=""
        ) as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

    time.sleep(5)

print("\nRETRY SUMMARY")
for status in ["PASS", "FAIL", "ERROR", "PENDING_REVIEW"]:
    print(status, sum(r["result"] == status for r in results))

print("Saved:", RESULTS_FILE)
