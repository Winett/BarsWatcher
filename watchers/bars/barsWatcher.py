from asyncio import sleep
from typing import Optional, Callable, Awaitable
from datetime import datetime
import re
import json

from aiohttp import ConnectionTimeoutError, ClientSession, ClientTimeout, ClientConnectorError
from bs4 import BeautifulSoup
from loguru import logger

from watchers.base import BaseAuth
from watchers.exceptions import LoginError
from watchers.bars.barsmodel import DisciplineWatcher, DisciplineSkip
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
        if await self._load_session():
            async with session.get(self.list_student_url, allow_redirects=False) as response:
                logger.debug(f"{self.__class__.__name__} Проверка авторизации: {response.status=}")
                if response.status == 200:
                    logger.debug(f"{self.__class__.__name__} -- Авторизация прошла успешно({self.username}) с помощью cookies --")
                    return True
                logger.warning(f"{self.__class__.__name__} -- Ошибка авторизации -- Обновляю куки...")
        session.cookie_jar.clear()
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
            logger.debug(f"{self.__class__.__name__} -- Авторизация прошла успешно({self.username}) с помощью пароля --")
            self._session = session
            await self._save_session()
            return True

        logger.error(f"{self.__class__.__name__} -- Ошибка авторизации -- Неверные учетные данные")
        raise LoginError("-- Ошибка авторизации --")

    async def get_student_id(self):
        session = await self.get_session()
        async with session.get(self.list_student_url, allow_redirects=False) as response:
            content = await response.read()
        #-----------------
        soup = BeautifulSoup(content, 'html.parser')
        # -----------------
        student_id = soup.find('table', id='tbl__PartialListStudent').find('tbody').find('tr').find('a')['href'].split('?')[-1].split('=')[1]
        return student_id

    def stop(self):
        self.watching = False
        self._cleanup()

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

    @logger.catch(reraise=True)
    async def watch(self, callback=None):
        self.watching = True
        last_data: dict[str, DisciplineWatcher] = {}
        last_skip_data: dict[str, DisciplineSkip] = {}
        session = await self.get_session()

        while self.watching:
            if session.closed:
                logger.warning(f"-- Сессия закрылась, пеероткрываю ({self.username}) --")
                session = await self.get_session()
                continue
            try:
                async with session.get(self.summary_url, params={'studentID': self.student_id}, allow_redirects=False) as response:
                    content = await response.read()
                soup = BeautifulSoup(content, 'html.parser')
                # response = open(r'E:\Documents\PYTHON\BarsCheckerLessons\test.html', 'rb').read()
                # soup = BeautifulSoup(response, 'html.parser')

                current_data: dict[str, DisciplineWatcher] = self.get_new_data_marks(soup)
                for change in self.find_changes(last_data, current_data):
                    logger.info(f"{self.__class__.__name__} ({self.username}) -- {change}")
                    if callback:
                        await callback(change)
                        continue
                    # logger.info(change)

                last_data = current_data

                new_skip_data: dict[str, DisciplineSkip] = self.get_new_data_skip(soup)

                for change in self.find_changes(last_skip_data, new_skip_data):
                    logger.info(f"{self.__class__.__name__} ({self.username}) -- {change}")
                    if callback:
                        await callback(change)
                        continue
                    # logger.info(change)

                    last_skip_data = new_skip_data
            except ConnectionTimeoutError:
                logger.error(f"{self.__class__.__name__} Ошибка соединения TimeoutError ({self.username})")
                await callback(f"{self.__class__.__name__} Ошибка соединения TimeoutError ({self.username})", user_id=settings.admins[0])
                raise

            except Exception as e:
                logger.error(f"Ошибка: {e.__class__.__name__}: {e.args}")

            finally:
                await sleep(self.timeout)


    @staticmethod
    def get_new_data_marks(soup) -> dict[str, DisciplineWatcher]:
        current_data: dict[str, DisciplineWatcher] = {}

        for tr in soup.find('table', id='tableMarkSummary').find('tbody').find_all('tr'):
            if tr.get('class') and tr['class'][0] == "summary-header-min":
                continue

            discipline = tr.find('td', {'class': 'summary-td-row-header'}).text.strip().split("\r\n")[0]
            marks = [
                td.text.strip()
                # int(td.text)
                for td in tr.find_all('span', {'class': 'summary-mark'})
                # if td.text.strip()
            ]
            mark_PA, mark_final = tr.find_all('td')[-2:]
            mark_PA = mark_PA.text.strip()
            mark_final = mark_final.text.strip()
            current_data[discipline] = DisciplineWatcher(discipline=discipline, marks=marks, mark_PA=mark_PA, mark_final=mark_final)

        return current_data

    @staticmethod
    def get_new_data_skip(soup) -> dict[str, DisciplineSkip]:
        current_data: dict[str, DisciplineSkip] = {}

        for tr in soup.find('table', id='tableSkipSummary').find('tbody').find_all('tr')[:-1]:
            if tr.get('class') and tr['class'][0] == "summary-header-min":
                continue

            discipline = tr.find('td', {'class': 'summary-td-row-header'}).text.strip()
            try:
                lessons_in_journal = int(tr.find_all('td')[1].text.strip())
            except ValueError:
                lessons_in_journal = 0
            try:
                skips = int(tr.find_all('td')[2].text.strip())
            except ValueError:
                skips = 0
            try:
                skip_for_good_reasons = int(tr.find_all('td')[3].text.strip())
            except ValueError:
                skip_for_good_reasons = 0
            try:
                skip_without_reason_percent = float(tr.find_all('td')[4].text.strip().replace(',', '.'))
            except ValueError:
                skip_without_reason_percent = 0

            try:
                lessons_in_shedule = int(tr.find_all('td')[5].text.strip())
            except ValueError:
                lessons_in_shedule = 0
            try:
                skip_without_reason_in_shedule_percent = float(tr.find_all('td')[6].text.strip().replace(',', '.'))
            except ValueError:
                skip_without_reason_in_shedule_percent = 0

            current_data[discipline] = DisciplineSkip(discipline=discipline, skips=skips, lessons_in_journal=lessons_in_journal, skip_for_good_reasons=skip_for_good_reasons, skip_without_reason_percent=skip_without_reason_percent, lessons_in_shedule=lessons_in_shedule, skip_without_reason_in_shedule_percent=skip_without_reason_in_shedule_percent)

        return current_data

    @staticmethod
    def find_changes(old_data, new_data):
        for discipline, new_discipline_data in new_data.items():
            if not old_data:
                break
            changes = old_data[discipline].find_changes(new_discipline_data)
            for change in changes:
                yield change

    @staticmethod
    def get_marks_for_automat(current_marks, weights, count_of_marks):
        """

        :param current_marks: Текущие оценки ученика
        :param weights: Все вес оценок, как новых, так и уже имеющихся
        :param count_of_marks: Общее количество оценок
        :return: Все возможные комбинации оценок, при которых возможен автомат
        """
        from itertools import product

        new_marks = []

        for marks in product([0, 2, 3, 4, 5], repeat=count_of_marks - len(current_marks)):
            probably_marks = current_marks + list(marks)
            average_mark = sum([mark * weight for mark, weight in zip(probably_marks, weights)]) / sum(weights)
            if round(average_mark, 2) >= 4.2:
                new_marks.append((marks, average_mark))

        return new_marks
    @staticmethod
    def generate_message_for_marks_to_automat(content):
        message = ""
        soup = BeautifulSoup(content, 'html.parser')
        for lesson in soup.find_all('div', class_='my-2'):
            discipline = ", ".join(lesson.text.strip().split(', ')[:3])
            KMs = []
            for tr in soup.find("div", id=lesson.find('a').get('href')[1:]).find_all('tr')[1:]:
                KM = []
                if re.search(r'\d+\.', tr.find('td').text):
                    for td in tr.find_all('td'):
                        KM.append(td.text.strip())
                    KMs.append(KM)

            message += f"{discipline}\n"

            marks_now = []
            weights_existing = []
            weights_missing = []

            for KM in KMs:
                weight = int(KM[1])
                mark = KM[-1].split()[0] if KM[-1] else None

                if mark:
                    marks_now.append(int(mark))
                    weights_existing.append(weight)
                else:
                    weights_missing.append(weight)

            weights = weights_existing + weights_missing

            message += f"Текущие оценки: {', '.join(map(str, marks_now))}\n" if marks_now else ""

            if len(marks_now) == len(weights_existing + weights_missing):
                if sum(marks_now) / len(marks_now) >= 4.2:
                    message += 'Поздравляю с получением автомата!!!\n'
                message += '\n\n'
                continue

            message += 'Для получения автомата нужно получить любую из следующих оценок: \n'
            message += '<blockquote expandable>'
            for marks, average_mark in WatcherKM.get_marks_for_automat(marks_now, weights, len(weights)):
                message += f"{', '.join([str(mark) for mark in marks])} ({average_mark})\n"
            message += '</blockquote>\n'
            message += '\n\n'
        return message
    @logger.catch
    async def marks_automat(self, callback):
        session = await self.get_session()
        params = {
                'studentId': await self.get_student_id(),
                'query': json.dumps({
                    "ID": await self.get_student_id(),
                    "FilterSemester": {
                        "Value": str(datetime.now().year % 100) if datetime.now().month < 9 else str((datetime.now().year + 1) % 100),
                    }
                })
            }

        async with session.get("https://bars.mpei.ru/bars_web/ST_Study/Student_SemesterSheet/_PartialListStudent_SemesterSheet__Mark", allow_redirects=False, params=params) as response:
            content = await response.read()
        message = self.generate_message_for_marks_to_automat(content)
        await callback(message)

