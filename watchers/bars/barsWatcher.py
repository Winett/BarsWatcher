from asyncio import sleep
from typing import Optional, Callable, Awaitable

from bs4 import BeautifulSoup
from loguru import logger

from watchers.base import BaseAuth
from watchers.exceptions import LoginError
from watchers.bars.barsmodel import DisciplineWatcher
from settings import settings


class WatcherKM(BaseAuth):
    login_url = 'https://bars.mpei.ru/bars_web/'
    summary_url = 'https://bars.mpei.ru/bars_web/ST_Study/Main/_PartialSummary'
    list_student_url = 'https://bars.mpei.ru/bars_web/ST/Student/ListStudent'

    if settings.DEBUG:
        timeout = 5
    else:
        timeout = 60


    def __init__(self, username: str, password: str):
        super().__init__(username, password)
        self.student_id = None
        self.watching = False

    async def login(self) -> bool:
        session = await self.get_session()
        async with session:
            if await self._load_session():
                async with session.get(self.list_student_url, allow_redirects=False) as response:
                # response = self.session.get(self.list_student_url, allow_redirects=False)  # Проверка авторизации
                    logger.debug(f"{self.__class__.__name__} Проверка авторизации: {response.status=}")
                    if response.status == 200:
                        logger.debug(f"{self.__class__.__name__} -- Авторизация прошла успешно({self.username}) с помощью cookies --")
                        return True
                    logger.warning(f"{self.__class__.__name__} -- Ошибка авторизации -- Обновляю куки...")

            async with session.get(self.login_url, allow_redirects=False) as response:
                content = await response.read()

            soup = BeautifulSoup(content, 'html.parser')
            RequestVerificationToken = soup.find('input', {'name': '__RequestVerificationToken'})['value']

            data = {
                "__RequestVerificationToken": RequestVerificationToken,
                "StopOpenDefault": False,
                "Account": self.username,
                "Password": self.password,
                "RememberMe": True
            }
            async with session.post(self.login_url, data=data) as response:
                pass

            if session.cookie_jar.filter_cookies(self.login_url).get('auth_bars'):
                await self._save_session()
                return True

            logger.error(f"{self.__class__.__name__} -- Ошибка авторизации -- Неверные учетные данные")
            raise LoginError("-- Ошибка авторизации --")

    async def get_student_id(self):
        session = await self.get_session()
        async with session:
            async with await session.get(self.list_student_url, allow_redirects=False) as response:
                content = await response.read()
            #-----------------
            soup = BeautifulSoup(content, 'html.parser')
            # -----------------
            student_id = soup.find('table', id='tbl__PartialListStudent').find('tbody').find('tr').find('a')['href'].split('?')[-1].split('=')[1]
            return student_id

    def stop(self):
        self.watching = False

    async def _authenticate(self, session) -> bool:
        try:
            async with session.get(self.list_student_url, allow_redirects=False) as resp:
                if resp.status == 200:
                    return True

            async with session.get(self.login_url) as resp:
                soup = BeautifulSoup(await resp.read(), 'html.parser')
                token = soup.find('input', {'name': '__RequestVerificationToken'})['value']

            auth_data = {
                "__RequestVerificationToken": token,
                "Account": self.username,
                "Password": self.password
            }

            async with session.post(self.login_url, data=auth_data) as resp:
                return 'auth_bars' in session.cookie_jar.filter_cookies(self.login_url)

        except Exception as e:
            logger.error(f"Ошибка авторизации: {e}")
            return False

    @logger.catch
    async def watch(self, callback: Optional[Callable[[str], Awaitable[None]]] = None):
        self.watching = True
        last_data: dict[str, DisciplineWatcher] = {}
        session = await self.get_session()


        async with session:
            while self.watching:
                try:
                    async with await session.get(self.summary_url, params={'studentID': self.student_id}, allow_redirects=False) as response:
                        content = await response.read()

                    soup = BeautifulSoup(content, 'html.parser')
                    # response = open(r'E:\Documents\PYTHON\BarsCheckerLessons\test.html', 'rb').read()
                    # soup = BeautifulSoup(response, 'html.parser')

                    current_data: dict[str, DisciplineWatcher] = {}

                    for tr in soup.find('table', id='tableMarkSummary').find('tbody').find_all('tr'):
                        if tr.get('class') and tr['class'][0] == "summary-header-min":
                            continue

                        discipline = tr.find('td', {'class': 'summary-td-row-header'}).text.strip()
                        marks = [
                            int(td.text)
                            for td in tr.find_all('span', {'class': 'summary-mark'})
                            if td.text.strip()
                        ]
                        mark_PA, mark_final = tr.find_all('td')[-2:]
                        mark_PA = mark_PA.text.strip()
                        mark_final = mark_final.text.strip()
                        current_data[discipline] = DisciplineWatcher(discipline=discipline, marks=marks, mark_PA=mark_PA, mark_final=mark_final)

                    for discipline, new_discipline_data in current_data.items():
                        if not last_data:
                            break
                        changes = last_data[discipline].find_changes(new_discipline_data)
                        for change in changes:
                            if callback:
                                await callback(f"Изменение в {discipline}: {change}")
                                continue
                            logger.info(f"Изменение в {discipline}: {change}")

                    last_data = current_data

                except Exception as e:
                    logger.error(f"Ошибка: {e.__class__.__name__}: {e.args}")

                finally:
                    await sleep(self.timeout)
