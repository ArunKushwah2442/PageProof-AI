
"""FastAPI backend for Smart Document Q&A Assistant."""

import os
import uuid
import shutil
import logging
from typing import List

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    UploadFile,
    File,
)
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import (
    hash_password,
    verify_password,
    create_token,
    get_current_user,
)
from app.config import UPLOAD_DIR
from app.database import Base, engine, get_db
from app.rag import VectorStore, DocumentProcessor, QAEngine

logger = logging.getLogger(__name__)


# --------------------------------------------------
# DATABASE AND DIRECTORY INITIALIZATION
# --------------------------------------------------

Base.metadata.create_all(bind=engine)
os.makedirs(UPLOAD_DIR, exist_ok=True)


# --------------------------------------------------
# FASTAPI APPLICATION
# --------------------------------------------------

app = FastAPI(title="Smart Document Q&A Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)


# --------------------------------------------------
# RAG COMPONENTS
# --------------------------------------------------

store = VectorStore()
processor = DocumentProcessor(store)
qa_engine = QAEngine(store)


# --------------------------------------------------
# HELPER FUNCTIONS
# --------------------------------------------------

def get_owned_document(
    doc_id: int,
    db: Session,
    user: models.User,
):
    """Return a document only if it belongs to the user."""

    doc = (
        db.query(models.Document)
        .filter_by(
            id=doc_id,
            user_id=user.id,
        )
        .first()
    )

    if not doc:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    return doc


def get_upload_path(doc_id: int):
    """Find a file uploaded using the document-ID prefix."""

    prefix = f"{doc_id}_"

    for filename in os.listdir(UPLOAD_DIR):
        if filename.startswith(prefix):
            return os.path.join(UPLOAD_DIR, filename)

    return None


# --------------------------------------------------
# FRONTEND
# --------------------------------------------------

@app.get("/", include_in_schema=False)
def home():
    return FileResponse("static/index.html")


# --------------------------------------------------
# USER REGISTRATION
# --------------------------------------------------

@app.post("/register", status_code=201)
def register(
    data: schemas.UserCreate,
    db: Session = Depends(get_db),
):
    existing_user = (
        db.query(models.User)
        .filter(
            models.User.username == data.username
        )
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Username already exists",
        )

    user = models.User(
        username=data.username,
        password_hash=hash_password(data.password),
    )

    db.add(user)
    db.commit()

    return {
        "message": "User registered successfully"
    }


# --------------------------------------------------
# USER LOGIN
# --------------------------------------------------

@app.post(
    "/login",
    response_model=schemas.Token,
)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = (
        db.query(models.User)
        .filter(
            models.User.username == form.username
        )
        .first()
    )

    if not user or not verify_password(
        form.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
        )

    return {
        "access_token": create_token(user.username),
        "token_type": "bearer",
    }


# --------------------------------------------------
# UPLOAD PDF
# --------------------------------------------------

@app.post(
    "/upload",
    response_model=schemas.DocumentOut,
    status_code=201,
)
def upload(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    if (
        not file.filename
        or not file.filename.lower().endswith(".pdf")
    ):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed",
        )

    # First create document record to obtain its ID.
    doc = models.Document(
        filename=file.filename,
        user_id=user.id,
    )

    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Use document ID in the stored filename.
    safe_filename = os.path.basename(
        file.filename.replace("\\", "/")
    )

    stored_filename = (
        f"{doc.id}_{uuid.uuid4().hex}_{safe_filename}"
    )

    path = os.path.join(
        UPLOAD_DIR,
        stored_filename,
    )

    try:
        # Save uploaded PDF.
        with open(path, "wb") as output:
            shutil.copyfileobj(file.file, output)

        # Extract PDF text and generate embeddings.
        doc.pages, doc.chunks = processor.process(
            path,
            doc.id,
        )

        # A document with no searchable chunks must not be marked successful.
        if not doc.chunks or int(doc.chunks) <= 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No readable text was found in this PDF. "
                    "Please upload a text-based PDF; scanned PDFs may require OCR."
                ),
            )

        db.commit()
        db.refresh(doc)

        return doc

    except Exception as exc:
        # Best-effort cleanup of any partial vector index and uploaded file.
        try:
            processor.delete(doc.id)
        except Exception:
            logger.exception("Could not clean up vectors for document %s", doc.id)

        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            logger.exception("Could not remove failed upload for document %s", doc.id)

        # Roll back any uncommitted state before removing the document record.
        db.rollback()
        try:
            db.delete(doc)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not remove failed document record %s", doc.id)
            raise HTTPException(
                status_code=500,
                detail="Upload failed and cleanup was incomplete.",
            ) from None

        if isinstance(exc, HTTPException):
            raise exc

        logger.exception("PDF processing failed for document %s", doc.id,
                         exc_info=(type(exc), exc, exc.__traceback__))
        raise HTTPException(
            status_code=500,
            detail="Could not process PDF. Please try a valid text-based PDF.",
        ) from None

    finally:
        file.file.close()


# --------------------------------------------------
# LIST USER DOCUMENTS
# --------------------------------------------------

@app.get(
    "/documents",
    response_model=List[schemas.DocumentOut],
)
def list_documents(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    return (
        db.query(models.Document)
        .filter(
            models.Document.user_id == user.id
        )
        .all()
    )


# --------------------------------------------------
# DELETE DOCUMENT
# --------------------------------------------------

@app.delete("/documents/{doc_id}")
def delete_document(
    doc_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    doc = get_owned_document(
        doc_id,
        db,
        user,
    )

    # Remove document embeddings.
    try:
        processor.delete(doc.id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not delete embeddings: {e}",
        )

    # Remove associated chat history.
    db.query(models.ChatHistory).filter_by(
        user_id=user.id,
        document_id=doc.id,
    ).delete(synchronize_session=False)

    # Remove document database record.
    db.delete(doc)
    db.commit()

    # Remove physical file for uploads using ID prefix.
    path = get_upload_path(doc_id)

    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            # Database and vector deletion already succeeded.
            # Do not report the entire operation as failed.
            pass

    return {
        "message": "Document deleted successfully"
    }


# --------------------------------------------------
# ASK QUESTIONS
# --------------------------------------------------

@app.post(
    "/ask",
    response_model=schemas.AskResponse,
)
def ask(
    req: schemas.AskRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    # Verify that the document belongs to the user.
    doc = get_owned_document(
        req.document_id,
        db,
        user,
    )

    # Retrieve recent conversation history.
    recent = (
        db.query(models.ChatHistory)
        .filter_by(
            user_id=user.id,
            document_id=doc.id,
        )
        .order_by(
            models.ChatHistory.id.desc()
        )
        .limit(5)
        .all()
    )

    history = (
        [
            (item.question, item.answer)
            for item in reversed(recent)
        ]
        if req.use_history
        else []
    )

    try:
        answer, sources = qa_engine.ask(
            req.question,
            doc.id,
            history,
        )

    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM error: {e}",
        )

    # Store unique source page numbers.
    pages = ",".join(
        str(page)
        for page in sorted(
            {
                source["page"]
                for source in sources
                if source.get("page") is not None
            }
        )
    )

    db.add(
        models.ChatHistory(
            user_id=user.id,
            document_id=doc.id,
            question=req.question,
            answer=answer,
            source_pages=pages,
        )
    )

    db.commit()

    return {
        "answer": answer,
        "sources": sources,
    }


# --------------------------------------------------
# CHAT HISTORY
# --------------------------------------------------

@app.get(
    "/history/{doc_id}",
    response_model=List[schemas.ChatOut],
)
def history(
    doc_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    # Explicit document ownership check.
    doc = get_owned_document(
        doc_id,
        db,
        user,
    )

    return (
        db.query(models.ChatHistory)
        .filter_by(
            user_id=user.id,
            document_id=doc.id,
        )
        .order_by(
            models.ChatHistory.id
        )
        .all()
    )
