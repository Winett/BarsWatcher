from .base import BaseFetcher
from loguru import logger

class BarsFetcher(BaseFetcher):

    async def fetch(self, url: str, **kwargs) -> bytes | str:
        '''

        :param url:
        :param kwargs:
        :return:
        :exception AuthError, ServerError
        '''

        # with open("watchers\\tests\\example_bars_response.html", encoding="utf-8") as response:
        #     return response.read()
        answer, response = await self.fetch_raw(url, **kwargs)

        if response.status != 200:
            logger.warning(self._logger_template + f"Необычный статус ответа {url}: {response.status}")
        return answer