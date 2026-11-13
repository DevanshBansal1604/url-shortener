"""SQLAlchemy schema definitions; importing this module does not create tables."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Sequence, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# The application will allocate an ID from this sequence before encoding it.
# Alembic must explicitly create this sequence in the first migration.
url_id_sequence = Sequence(
    "url_id_seq", start=1, data_type=BigInteger(), metadata=Base.metadata
)


class URL(Base):
    __tablename__ = "urls"

    id: Mapped[int] = mapped_column(
        BigInteger,
        url_id_sequence,
        server_default=url_id_sequence.next_value(),
        primary_key=True,
    )
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    # Eleven Base62 characters can represent any positive signed BIGINT ID.
    short_code: Mapped[str] = mapped_column(String(11), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
