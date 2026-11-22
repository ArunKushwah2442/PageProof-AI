
"""RAG logic: PDF -> chunks -> embeddings -> ChromaDB -> Groq LLM."""

import re
from typing import List, Tuple

from langchain_core.documents import Document
from langchain_community.document_loaders import PDFPlumberLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_groq import ChatGroq

from app.config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    CHROMA_DIR,
    EMBEDDING_MODEL,
)


class VectorStore:
    """Manages the persistent ChromaDB vector database."""

    def __init__(self):
        embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL
        )

        self.db = Chroma(
            collection_name="documents",
            embedding_function=embeddings,
            persist_directory=CHROMA_DIR,
        )


class DocumentProcessor:
    """Extracts PDF text, creates chunks and stores embeddings."""

    def __init__(
        self,
        store: VectorStore,
        chunk_size: int = 1500,
        chunk_overlap: int = 350,
    ):
        self.store = store

        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def process(self, pdf_path: str, doc_id: int) -> Tuple[int, int]:
        pages = PDFPlumberLoader(pdf_path).load()
        chunks = self.splitter.split_documents(pages)

        for chunk in chunks:
            chunk.metadata["doc_id"] = doc_id
            chunk.metadata["page"] = (
                int(chunk.metadata.get("page", 0)) + 1
            )

        if chunks:
            self.store.db.add_documents(chunks)

        return len(pages), len(chunks)

    def delete(self, doc_id: int):
        """Delete chunks belonging to a specific document."""

        result = self.store.db.get(
            where={"doc_id": doc_id}
        )

        ids = result.get("ids", [])

        if ids:
            self.store.db.delete(ids=ids)


class QAEngine:
    """Retrieves document context and generates grounded answers."""

    PROMPT = (
        "You are a document question-answering assistant.\n"
        "Answer the user's question ONLY using the document context.\n"
        "Do not invent facts or use outside knowledge.\n"
        "If the answer is not present in the provided context, "
        'reply exactly: "I could not find this in the document."\n'
        "Mention the supporting page number when possible.\n\n"
        "Previous conversation (for follow-up questions only):\n"
        "{history}\n\n"
        "Document context:\n"
        "{context}\n\n"
        "Question: {question}\n\n"
        "Answer:"
    )

    def __init__(self, store: VectorStore, k: int = 8):
        self.store = store
        self.k = k

        self.llm = ChatGroq(
            api_key=GROQ_API_KEY,
            model=GROQ_MODEL,
            temperature=0,
        )

    def ask(
        self,
        question: str,
        doc_id: int,
        history: List[Tuple[str, str]],
    ):
        # STEP 1: Understand simple follow-up questions.
        search_question = question
        resolved_chapter = None

        follow_up_phrases = [
            "that chapter",
            "this chapter",
            "the chapter",
            "explain it",
            "tell me more",
        ]

        is_follow_up = any(
            phrase in question.lower()
            for phrase in follow_up_phrases
        )

        if is_follow_up and history:
            # Search recent conversation for the last chapter number.
            for previous_q, previous_a in reversed(history[-5:]):
                chapter_match = re.search(
                    r"\bchapter\s+(\d+)\b",
                    previous_q,
                    re.IGNORECASE,
                )

                if not chapter_match:
                    chapter_match = re.search(
                        r"\bchapter\s+(\d+)\b",
                        previous_a,
                        re.IGNORECASE,
                    )

                if chapter_match:
                    resolved_chapter = chapter_match.group(1)

                    search_question = (
                        f"Chapter {resolved_chapter} "
                        f"{previous_a} "
                        f"{question}"
                    )
                    break

        # STEP 2: Normal semantic retrieval.
        docs = self.store.db.similarity_search(
            search_question,
            k=self.k,
            filter={"doc_id": doc_id},
        )

        # STEP 3: Chapter-title fallback.
        chapter_match = re.search(
            r"\bchapter\s+(\d+)\b",
            question,
            re.IGNORECASE,
        )

        title_question = bool(
            re.search(
                r"\b(title|name|called)\b",
                question,
                re.IGNORECASE,
            )
        )

        # Also handle a follow-up referring to a chapter.
        chapter_number = (
            chapter_match.group(1)
            if chapter_match
            else resolved_chapter
        )

        if chapter_number and (title_question or resolved_chapter):
            stored = self.store.db.get(
                where={"doc_id": doc_id},
                include=["documents", "metadatas"],
            )

            matching_chunks = []

            heading_pattern = re.compile(
                rf"^\s*(?:Chapter\s+{chapter_number}\s*:"
                rf"|Chapter\s+{chapter_number}\s*$)",
                re.IGNORECASE,
            )

            fallback_pattern = re.compile(
                rf"\bChapter\s+{chapter_number}\s*:",
                re.IGNORECASE,
            )

            for text, metadata in zip(
                stored["documents"],
                stored["metadatas"],
            ):
                lines = text.splitlines()

                heading_found = any(
                    heading_pattern.search(line)
                    for line in lines
                )

                fallback_found = any(
                    fallback_pattern.search(line)
                    for line in lines
                )

                if heading_found or fallback_found:
                    matching_chunks.append(
                        (
                            0 if heading_found else 1,
                            metadata.get("page", 999999),
                            Document(
                                page_content=text,
                                metadata=metadata,
                            ),
                        )
                    )

            if matching_chunks:
                matching_chunks.sort(
                    key=lambda item: (item[0], item[1])
                )

                docs = [matching_chunks[0][2]] + docs

        if not docs:
            return (
                "I could not find this in the document.",
                [],
            )

        # STEP 4: Retrieve all chunks from selected pages.
        pages = []

        for doc in docs:
            page = doc.metadata.get("page")

            if page is not None and page not in pages:
                pages.append(page)

        extra_docs = []

        for page in pages:
            result = self.store.db.get(
                where={
                    "$and": [
                        {"doc_id": doc_id},
                        {"page": page},
                    ]
                },
                include=["documents", "metadatas"],
            )

            for text, metadata in zip(
                result["documents"],
                result["metadatas"],
            ):
                extra_docs.append(
                    Document(
                        page_content=text,
                        metadata=metadata,
                    )
                )

        # STEP 5: Remove duplicate chunks.
        unique_docs = []
        seen = set()

        for doc in docs + extra_docs:
            key = (
                doc.metadata.get("page"),
                doc.page_content,
            )

            if key not in seen:
                seen.add(key)
                unique_docs.append(doc)

        # STEP 6: Prepare document context.
        context = "\n\n".join(
            f"[Page {doc.metadata.get('page', 'Unknown')}] "
            f"{doc.page_content}"
            for doc in unique_docs
        )

        # STEP 7: Prepare recent conversation history.
        recent_history = history[-5:] if history else []

        hist = "\n".join(
            f"Q: {q}\nA: {a}"
            for q, a in recent_history
        ) or "None"

        # STEP 8: Generate answer using Groq.
        prompt = self.PROMPT.format(
            history=hist,
            context=context,
            question=question,
        )

        response = self.llm.invoke(prompt)
        answer = str(response.content).strip()
        print("DEBUG RAG ANSWER:", repr(answer))

        # FIX: Never attach irrelevant citations to refusals.
        refusal = "I could not find this in the document."

        if refusal.lower() in answer.lower():
            return refusal, []

        # STEP 9: Return source snippets.
        sources = [
            {
                "page": doc.metadata.get("page"),
                "snippet": doc.page_content[:200],
            }
            for doc in unique_docs
        ]

        return answer, sources
