from sqlalchemy import String, ForeignKey, BigInteger, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)

    used_osep: Mapped[bool] = mapped_column(Boolean, default=False)
    used_bars: Mapped[bool] = mapped_column(Boolean, default=False)

    bars_login: Mapped[str] = mapped_column(String, nullable=True)
    bars_password: Mapped[str] = mapped_column(String, nullable=True)

    osep_login: Mapped[str] = mapped_column(String, nullable=True)
    osep_password: Mapped[str] = mapped_column(String, nullable=True)



