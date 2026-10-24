
"""Create the database tables from the SQLAlchemy models."""

from app.database import engine
from app.models import Base


def init_db():
    """Create tables that do not already exist."""
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully.")


if __name__ == "__main__":
    init_db()
