import asyncio

from aiogram import Bot
from aiogram.types import Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio

from settings import settings


def _build_input_media(msg: Message):
    if msg.photo:
        return InputMediaPhoto(media=msg.photo[-1].file_id, caption=msg.caption)
    elif msg.video:
        return InputMediaVideo(media=msg.video.file_id, caption=msg.caption)
    elif msg.document:
        return InputMediaDocument(media=msg.document.file_id, caption=msg.caption)
    elif msg.audio:
        return InputMediaAudio(media=msg.audio.file_id, caption=msg.caption)
    return None


def _has_media(msg: Message) -> bool:
    return bool(msg.photo or msg.video or msg.document or msg.audio)


async def forward_to_admins(
    bot: Bot,
    msg: Message,
    header: str,
    media_messages: list[Message] | None = None,
):
    for admin_id in settings.admins:
        sent = await bot.send_message(admin_id, header)

        source_messages = media_messages if media_messages else [msg]

        if len(source_messages) > 1:
            media = [item for m in source_messages if (item := _build_input_media(m))]
            if media:
                await bot.send_media_group(admin_id, media, reply_to_message_id=sent.message_id)
                await asyncio.sleep(0.2)
                continue

        if _has_media(msg):
            item = _build_input_media(msg)
            if item:
                await bot.send_media_group(admin_id, [item], reply_to_message_id=sent.message_id)
                await asyncio.sleep(0.2)
                continue

        await bot.copy_message(
            chat_id=admin_id,
            from_chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_to_message_id=sent.message_id,
        )
        await asyncio.sleep(0.2)
