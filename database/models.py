from sqlalchemy import String, BigInteger, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String, default=None, nullable=True)

    used_osep: Mapped[bool] = mapped_column(Boolean, default=False)
    used_bars: Mapped[bool] = mapped_column(Boolean, default=False)

    bars_login: Mapped[str] = mapped_column(String, nullable=True)
    bars_password: Mapped[str] = mapped_column(String, nullable=True)

    osep_login: Mapped[str] = mapped_column(String, nullable=True)
    osep_password: Mapped[str] = mapped_column(String, nullable=True)



