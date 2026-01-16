import aiofiles.os as aios
from pathlib import Path
import asyncio
import time


async def cleanup_cache_documents():
    path = Path(__file__).parent.parent.parent / "cashed_files"
    current_time = time.time()

    try:
        # Получаем список файлов
        files = await aios.listdir(str(path))

        for filename in files:
            filepath = path / filename

            try:
                stat = await aios.stat(str(filepath))

                if current_time - stat.st_atime > 3600:  # 1 час
                    await aios.remove(str(filepath))
                    print(f"Удален: {filename}")

            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"Ошибка с {filename}: {e}")

    except Exception as e:
        print(f"Ошибка чтения директории: {e}")

async def timer_cleanup_cache_documents():
    while True:
        await cleanup_cache_documents()
        await asyncio.sleep(3600)  # 1 час
