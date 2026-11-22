from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    """Har request ke liye ek DB session deta hai aur baad mein band kar deta hai."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
