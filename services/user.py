from sqlalchemy.ext.asyncio import  AsyncSession

from sqlalchemy import select, delete

from database.models import User

__all__ = ['UserService']


class UserService:

    def __init__(self, session_maker: AsyncSession):
        self.session_maker = session_maker


    async def is_exists(self, user_id: int) -> bool:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return bool(user.scalar())

    async def create(self, user_id: int, username: str = None) -> None:
        async with self.session_maker as session:
            user = User(user_id=user_id, username=username)
            session.add(user)
            await session.commit()

    async def delete(self, user_id: int) -> None:
        async with self.session_maker as session:
            await session.execute(delete(User).where(User.user_id == user_id))
            await session.commit()

    async def check_osep(self, user_id: int) -> bool:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return user.scalar().used_osep

    async def check_bars(self, user_id: int) -> bool:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return user.scalar().used_bars

    async def set_osep(self, user_id: int, login: str, password: str) -> None:
        async with self.session_maker as session:
            user = (await session.execute(select(User).where(User.user_id == user_id))).scalar()
            user.osep_login = login
            user.osep_password = password
            await session.commit()

    async def set_bars(self, user_id: int, login: str, password: str) -> None:
        async with self.session_maker as session:
            user = (await session.execute(select(User).where(User.user_id == user_id))).scalar()
            user.bars_login = login
            user.bars_password = password
            await session.commit()

    async def exist_bars_login(self, user_id: int) -> bool:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return bool(user.scalar().bars_login)

    async def exist_osep_login(self, user_id: int) -> bool:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return bool(user.scalar().osep_login)

    async def exist_bars_password(self, user_id: int) -> bool:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return bool(user.scalar().bars_password)

    async def exist_osep_password(self, user_id: int) -> bool:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return bool(user.scalar().osep_password)

    async def exist_bars_credentials(self, user_id: int) -> bool:
        return await self.exist_bars_login(user_id) and await self.exist_bars_password(user_id)

    async def exist_osep_credentials(self, user_id: int) -> bool:
        return await self.exist_osep_login(user_id) and await self.exist_osep_password(user_id)

    async def get_bars_login(self, user_id: int) -> str:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return user.scalar().bars_login

    async def get_osep_login(self, user_id: int) -> str:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return user.scalar().osep_login

    async def get_bars_password(self, user_id: int) -> str:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return user.scalar().bars_password

    async def get_osep_password(self, user_id: int) -> str:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return user.scalar().osep_password

    async def set_bars_status_used(self, user_id: int, status: bool):
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            user.scalar().used_bars = status
            await session.commit()

    async def set_osep_status_used(self, user_id: int, status: bool):
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            user.scalar().used_osep = status
            await session.commit()

    async def find_all_users_used_bars(self) -> list[User]:
        async with self.session_maker as session:
            users = await session.execute(select(User).where(User.used_bars == True))
            return users.scalars().all()

    async def find_all_users_used_osep(self) -> list[User]:
        async with self.session_maker as session:
            users = await session.execute(select(User).where(User.used_osep == True))
            return users.scalars().all()


    async def find_all_users(self) -> list[User]:
        async with self.session_maker as session:
            users = await session.execute(select(User))
            return users.scalars().all()

    async def find_user(self, user_id: int) -> User:
        async with self.session_maker as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            return user.scalar()

    async def update_username(self, user_id: int, username: str) -> None:
        async with self.session_maker as session:
            user = (await session.execute(select(User).where(User.user_id == user_id))).scalar()
            user.username = username
            await session.commit()



