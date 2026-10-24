
# app/database.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Store the database in the project root.
DATABASE_URL = "sqlite:///./ct200.db"

# Create the connection to SQLite.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

# Create database sessions for reading and writing data.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def get_db():
    """Provide a database session to an API request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
