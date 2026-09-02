from sqlalchemy import Integer, BigInteger, String, Boolean, ForeignKey, select
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bars import BarsCredentials, BarsProfile, BarsState, BarsSettings
    from .osep import OsepCredentials, OsepState, OsepSettings

from .base import Base
from .mixin import TimestampMixin

__all__ = ["User", "Admin"]


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)

    admin_record: Mapped["Admin | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    bars_credentials: Mapped["BarsCredentials"] = relationship(
        cascade="all, delete-orphan"
    )
    bars_profile: Mapped["BarsProfile"] = relationship(cascade="all, delete-orphan")
    bars_state: Mapped["BarsState"] = relationship(cascade="all, delete-orphan")
    bars_settings: Mapped["BarsSettings"] = relationship(cascade="all, delete-orphan")

    osep_credentials: Mapped["OsepCredentials"] = relationship(
        cascade="all, delete-orphan"
    )
    osep_state: Mapped["OsepState"] = relationship(cascade="all, delete-orphan")
    osep_settings: Mapped["OsepSettings"] = relationship(cascade="all, delete-orphan")

    @hybrid_property
    def is_admin(self):
        return self.admin_record is not None

    @is_admin.inplace.expression
    @classmethod
    def _is_admin_expression(cls):
        return select(Admin.id).where(Admin.user_id == cls.id).exists()


class Admin(Base, TimestampMixin):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )

    user: Mapped["User"] = relationship("User", back_populates="admin_record")
