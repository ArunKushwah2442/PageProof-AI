from datetime import datetime
from typing import List
from pydantic import BaseModel


class UserCreate(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class DocumentOut(BaseModel):
    id: int
    filename: str
    pages: int
    chunks: int
    uploaded_at: datetime

    class Config:
        from_attributes = True


class AskRequest(BaseModel):
    document_id: int
    question: str
    use_history: bool = True


class Source(BaseModel):
    page: int
    snippet: str


class AskResponse(BaseModel):
    answer: str
    sources: List[Source]


class ChatOut(BaseModel):
    question: str
    answer: str
    source_pages: str
    created_at: datetime

    class Config:
        from_attributes = True
