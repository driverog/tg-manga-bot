import enum
import shutil
from ast import arg
import asyncio
import re
from dataclasses import dataclass
import datetime as dt
import json

import pyrogram.errors
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputMediaDocument

from img2cbz.core import fld2cbz
from img2pdf.core import fld2pdf
from img2tph.core import img2tph
from img2html.core import img2html
from plugins import MangaClient, ManhuaKoClient, MangaCard, MangaChapter, ManhuaPlusClient, TMOClient, MangaDexClient, \
    MangaSeeClient, MangasInClient, McReaderClient, MangaKakalotClient, ManganeloClient, ManganatoClient, \
    KissMangaClient, MangatigreClient
import os

from pyrogram import Client, filters
from typing import Dict, Tuple, List, TypedDict

from models.db import DB, ChapterFile, Subscription, LastChapter, MangaName, MangaOutput
from pagination import Pagination
from plugins.client import clean

mangas: Dict[str, MangaCard] = dict()
chapters: Dict[str, MangaChapter] = dict()
pdfs: Dict[str, str] = dict()
paginations: Dict[int, Pagination] = dict()
queries: Dict[str, Tuple[MangaClient, str]] = dict()
full_pages: Dict[str, List[str]] = dict()
favourites: Dict[str, MangaCard] = dict()
language_query: Dict[str, Tuple[str, str]] = dict()
users_in_channel: Dict[int, dt.datetime] = dict()
locks: Dict[int, asyncio.Lock] = dict()

plugin_dicts: Dict[str, Dict[str, MangaClient]] = {
    "🇬🇧 EN": {
        "MangaDex": MangaDexClient(),
        "Manhuaplus": ManhuaPlusClient(),
        "Mangasee": MangaSeeClient(),
        "McReader": McReaderClient(),
        "MagaKakalot": MangaKakalotClient(),
        "Manganelo": ManganeloClient(),
        "Manganato": ManganatoClient(),
        "KissManga": KissMangaClient()
    },
    "🇪🇸 ES": {
        "MangaDex": MangaDexClient(language=("es-la", "es")),
        "ManhuaKo": ManhuaKoClient(),
        "TMO": TMOClient(),
        "Mangatigre": MangatigreClient()
    }
}


class OutputOptions(enum.IntEnum):
    PDF = 1
    CBZ = 2
    HTML = 3
    Telegraph = 4

    def __and__(self, other):
        return self.value & other

    def __xor__(self, other):
        return self.value ^ other

    def __or__(self, other):
        return self.value | other


plugins = dict()
for lang, plugin_dict in plugin_dicts.items():
    for name, plugin in plugin_dict.items():
        plugins[f'[{lang}] {name}'] = plugin

# subsPaused = ["[ES] TMO"]
subsPaused = []


def split_list(li):
    return [li[x: x + 2] for x in range(0, len(li), 2)]


def get_buttons_for_options(user_options: int):
    buttons = []
    for option in OutputOptions:
        checked = "✅" if option & user_options else "❌"
        text = f'{checked} {option.name}'
        buttons.append([InlineKeyboardButton(text, f"options_{option.value}")])
    return InlineKeyboardMarkup(buttons)


env_file = "env.json"
if os.path.exists(env_file):
    with open(env_file) as f:
        env_vars = json.loads(f.read())
else:
    env_vars = dict(os.environ)

bot = Client('bot',
             api_id=int(env_vars.get('API_ID')),
             api_hash=env_vars.get('API_HASH'),
             bot_token=env_vars.get('BOT_TOKEN'))


@bot.on_message(filters=~(filters.private & filters.incoming))
async def on_chat_or_channel_message(client: Client, message: Message):
    pass


@bot.on_message()
async def on_private_message(client: Client, message: Message):
    channel = env_vars.get('CHANNEL')
    if not channel:
        return message.continue_propagation()
    if in_channel_cached := users_in_channel.get(message.from_user.id):
        if dt.datetime.now() - in_channel_cached < dt.timedelta(days=1):
            return message.continue_propagation()
    try:
        if await client.get_chat_member(channel, message.from_user.id):
            users_in_channel[message.from_user.id] = dt.datetime.now()
            return message.continue_propagation()
    except pyrogram.errors.UsernameNotOccupied:
        print("Channel does not exist, therefore bot will continue to operate normally")
        return message.continue_propagation()
    except pyrogram.errors.ChatAdminRequired:
        print("Bot is not admin of the channel, therefore bot will continue to operate normally")
        return message.continue_propagation()
    except pyrogram.errors.UserNotParticipant:
        await message.reply("In order to use the bot you must join it's update channel.",
                            reply_markup=InlineKeyboardMarkup(
                                [[InlineKeyboardButton('Join!', url=f't.me/{channel}')]]
                            ))


@bot.on_message(filters=filters.command(['start']))
async def on_refresh(client: Client, message: Message):
    await message.reply("Welcome to the best manga pdf bot in telegram!!\n"
                        "\n"
                        "How to use? Just type the name of some manga you want to keep up to date.\n"
                        "\n"
                        "For example:\n"
                        "`Fire Force`")


@bot.on_message(filters=filters.command(['refresh']))
async def on_refresh(client: Client, message: Message):
    if not message.reply_to_message or not message.reply_to_message.outgoing or not message.reply_to_message.document \
            or not message.reply_to_message.document.file_name.lower().endswith('.pdf'):
        return await message.reply("This command only works when it replies to a pdf file that bot sent to you")
    replied = message.reply_to_message
    db = DB()
    chapter = await db.get_chapter_file_by_id(replied.document.file_unique_id)
    if not chapter:
        return await message.reply("This file was already refreshed")
    await db.erase(chapter)
    return await message.reply("File refreshed successfully!")


@bot.on_message(filters=filters.command(['subs']))
async def on_subs(client: Client, message: Message):
    db = DB()
    subs = await db.get_subs(str(message.from_user.id))
    lines = []
    for sub in subs:
        lines.append(f'<a href="{sub.url}">{sub.name}</a>')
        lines.append(f'`/cancel {sub.url}`')
        lines.append('')

    if not lines:
        return await message.reply("You have no subscriptions yet.")
    body = "\n".join(lines)
    await message.reply(f'Your subscriptions:\n\n{body}', disable_web_page_preview=True)


@bot.on_message(filters=filters.regex(r'^/cancel ([^ ]+)$'))
async def on_cancel_command(client: Client, message: Message):
    db = DB()
    sub = await db.get(Subscription, (message.matches[0].group(1), str(message.from_user.id)))
    if not sub:
        return await message.reply("You were not subscribed to that manga.")
    await db.erase(sub)
    return await message.reply("You will no longer receive updates for that manga.")


@bot.on_message(filters=filters.command(['options']))
async def on_options_command(client: Client, message: Message):
    db = DB()
    user_options = await db.get(MangaOutput, str(message.from_user.id))
    user_options = user_options.output if user_options else (1 << 30) - 1
    buttons = get_buttons_for_options(user_options)
    return await message.reply("Select the desired output format.", reply_markup=buttons)


@bot.on_message(filters=filters.regex(r'^/'))
async def on_unknown_command(client: Client, message: Message):
    await message.reply("Unknown command")


@bot.on_message(filters=filters.text)
async def on_message(client, message: Message):
    language_query[f"lang_None_{hash(message.text)}"] = (None, message.text)
    for language in plugin_dicts.keys():
        language_query[f"lang_{language}_{hash(message.text)}"] = (language, message.text)
    await bot.send_message(message.chat.id, "Select search languages.", reply_markup=InlineKeyboardMarkup(
        split_list([InlineKeyboardButton(language, callback_data=f"lang_{language}_{hash(message.text)}")
                    for language in plugin_dicts.keys()])
    ))


async def options_click(client, callback: CallbackQuery):
    db = DB()
    user_options = await db.get(MangaOutput, str(callback.from_user.id))
    if not user_options:
        user_options = MangaOutput(user_id=str(callback.from_user.id), output=(2 << 30) - 1)
    option = int(callback.data.split('_')[-1])
    user_options.output ^= option
    buttons = get_buttons_for_options(user_options.output)
    await db.add(user_options)
    return await callback.message.edit_reply_markup(reply_markup=buttons)


async def language_click(client, callback: CallbackQuery):
    lang, query = language_query[callback.data]
    if not lang:
        return await callback.message.edit("Select search languages.", reply_markup=InlineKeyboardMarkup(
            split_list([InlineKeyboardButton(language, callback_data=f"lang_{language}_{hash(query)}")
                        for language in plugin_dicts.keys()])
        ))
    for identifier, manga_client in plugin_dicts[lang].items():
        queries[f"query_{lang}_{identifier}_{hash(query)}"] = (manga_client, query)
    await callback.message.edit(f"Language: {lang}\n\nSelect search plugin.", reply_markup=InlineKeyboardMarkup(
        split_list([InlineKeyboardButton(identifier, callback_data=f"query_{lang}_{identifier}_{hash(query)}")
                    for identifier in plugin_dicts[lang].keys()]) + [
            [InlineKeyboardButton("◀️ Back", callback_data=f"lang_None_{hash(query)}")]]
    ))


async def plugin_click(client, callback: CallbackQuery):
    manga_client, query = queries[callback.data]
    results = await manga_client.search(query)
    if not results:
        await bot.send_message(callback.from_user.id, "No manga found for given query.")
        return
    for result in results:
        mangas[result.unique()] = result
    await bot.send_message(callback.from_user.id,
                           "This is the result of your search",
                           reply_markup=InlineKeyboardMarkup([
                               [InlineKeyboardButton(result.name, callback_data=result.unique())] for result in results
                           ]))


async def manga_click(client, callback: CallbackQuery, pagination: Pagination = None):
    if pagination is None:
        pagination = Pagination()
        paginations[pagination.id] = pagination

    if pagination.manga is None:
        manga = mangas[callback.data]
        pagination.manga = manga

    results = await pagination.manga.client.get_chapters(pagination.manga, pagination.page)

    if not results:
        await callback.answer("Ups, no chapters there.", show_alert=True)
        return

    full_page_key = f'full_page_{hash("".join([result.unique() for result in results]))}'
    full_pages[full_page_key] = []
    for result in results:
        chapters[result.unique()] = result
        full_pages[full_page_key].append(result.unique())

    db = DB()
    subs = await db.get(Subscription, (pagination.manga.url, str(callback.from_user.id)))

    prev = [InlineKeyboardButton('<<', f'{pagination.id}_{pagination.page - 1}')]
    next_ = [InlineKeyboardButton('>>', f'{pagination.id}_{pagination.page + 1}')]
    footer = [prev + next_] if pagination.page > 1 else [next_]

    fav = [[InlineKeyboardButton(
        "Unsubscribe" if subs else "Subscribe",
        f"{'unfav' if subs else 'fav'}_{pagination.manga.unique()}"
    )]]
    favourites[f"fav_{pagination.manga.unique()}"] = pagination.manga
    favourites[f"unfav_{pagination.manga.unique()}"] = pagination.manga

    full_page = [[InlineKeyboardButton('Full Page', full_page_key)]]

    buttons = InlineKeyboardMarkup(fav + footer + [
        [InlineKeyboardButton(result.name, result.unique())] for result in results
    ] + full_page + footer)

    if pagination.message is None:
        try:
            message = await bot.send_photo(callback.from_user.id,
                                           pagination.manga.picture_url,
                                           f'{pagination.manga.name}\n'
                                           f'{pagination.manga.get_url()}', reply_markup=buttons)
            pagination.message = message
        except pyrogram.errors.BadRequest as e:
            file_name = f'pictures/{pagination.manga.unique()}.jpg'
            await pagination.manga.client.get_url(pagination.manga.picture_url, cache=True, file_name=file_name)
            message = await bot.send_photo(callback.from_user.id,
                                           f'./cache/{pagination.manga.client.name}/{file_name}',
                                           f'{pagination.manga.name}\n'
                                           f'{pagination.manga.get_url()}', reply_markup=buttons)
            pagination.message = message
    else:
        await bot.edit_message_reply_markup(
            callback.from_user.id,
            pagination.message.message_id,
            reply_markup=buttons
        )


async def chapter_click(client, data, chat_id):
    lock = locks.get(chat_id)
    if not lock:
        locks[chat_id] = asyncio.Lock()

    async with locks[chat_id]:
        cache_channel = env_vars.get("CACHE_CHANNEL")
        if not cache_channel:
            return await bot.send_message(chat_id, "Bot cache channel is not configured correctly.")
        chapter = chapters[data]

        db = DB()

        chapterFile = await db.get(ChapterFile, chapter.url)
        options = await db.get(MangaOutput, str(chat_id))
        options = options.output if options else (1 << 30) - 1

        caption = '\n'.join([
            f'{chapter.manga.name} - {chapter.name}',
            f'{chapter.get_url()}'
        ])

        download = not chapterFile
        download = download or options & OutputOptions.PDF and not chapterFile.file_id
        download = download or options & OutputOptions.CBZ and not chapterFile.cbz_id
        download = download or options & OutputOptions.CBZ and not chapterFile.html_id
        download = download or options & OutputOptions.Telegraph and not chapterFile.telegraph_url

        if download:
            pictures_folder = await chapter.client.download_pictures(chapter)
            if not chapter.pictures:
                return await bot.send_message(chat_id, f'There was an error parsing this chapter or chapter is missing' +
                                              f', please check the chapter at the web\n\n{caption}')
            ch_name = clean(f'{chapter.manga.name} - {chapter.name}', 45)
            pdf, thumb_path = fld2pdf(pictures_folder, ch_name)
            cbz = fld2cbz(pictures_folder, ch_name)
            img2html(f"{chapter.manga.name}{chapter.name}", chapter)
            html_file = f"{chapter.manga.name}{chapter.name}.html"
            telegraph_url = await img2tph(chapter, clean(f'{chapter.manga.name} {chapter.name}'))

            messages: List[Message] = await bot.send_media_group(cache_channel, [
                InputMediaDocument(pdf, thumb=thumb_path),
                InputMediaDocument(cbz, thumb=thumb_path), 
                InputMediaDocument(html_file, thumb=thumb_path, caption=f'{telegraph_url}')
            ])

            pdf_m, cbz_m, html_m = messages

            if not chapterFile:
                await db.add(ChapterFile(url=chapter.url, file_id=pdf_m.document.file_id,
                                         file_unique_id=pdf_m.document.file_unique_id, cbz_id=cbz_m.document.file_id,
                                         cbz_unique_id=cbz_m.document.file_unique_id, html_id=html_m.document.file_id,
                                         html_unique_id=html_m.document.file_unique_id, telegraph_url=telegraph_url))
            else:
                chapterFile.file_id, chapterFile.file_unique_id, chapterFile.cbz_id, \
                chapterFile.cbz_unique_id, chapterFile.html_id, chapterFile.html_unique_id, chapterFile.telegraph_url = \
                    pdf_m.document.file_id, pdf_m.document.file_unique_id, cbz_m.document.file_id, \
                    cbz_m.document.file_unique_id, html_m.document.file_id, html_m.document.file_unique_id, telegraph_url
                await db.add(chapterFile)

            shutil.rmtree(pictures_folder)

        chapterFile = await db.get(ChapterFile, chapter.url)

        caption = f'{chapter.manga.name} - {chapter.name}\n'
        if options & OutputOptions.Telegraph:
            caption += f'[Read on telegraph]({chapterFile.telegraph_url})\n'
        caption += f'[Read on website]({chapter.get_url()})'
        media_docs = []
        if options & OutputOptions.PDF:
            media_docs.append(InputMediaDocument(chapterFile.file_id))
        if options & OutputOptions.CBZ:
            media_docs.append(InputMediaDocument(chapterFile.cbz_id))
        if options & OutputOptions.HTML:
            media_docs.append(InputMediaDocument(chapterFile.html_id))

        if len(media_docs) == 0:
            return await bot.send_message(chat_id, caption)
        if len(media_docs) == 1:
            return await bot.send_document(chat_id, media_docs[0].media, caption=caption)
        media_docs[-1].caption = caption
        return await bot.send_media_group(chat_id, media_docs)


async def pagination_click(client: Client, callback: CallbackQuery):
    pagination_id, page = map(int, callback.data.split('_'))
    pagination = paginations[pagination_id]
    pagination.page = page
    await manga_click(client, callback, pagination)


async def full_page_click(client: Client, callback: CallbackQuery):
    chapters_data = full_pages[callback.data]
    for chapter_data in reversed(chapters_data):
        try:
            await chapter_click(client, chapter_data, callback.from_user.id)
        except Exception as e:
            print(e)
        await asyncio.sleep(0.1)


async def favourite_click(client: Client, callback: CallbackQuery):
    action, data = callback.data.split('_')
    fav = action == 'fav'
    manga = favourites[callback.data]
    db = DB()
    subs = await db.get(Subscription, (manga.url, str(callback.from_user.id)))
    if not subs and fav:
        await db.add(Subscription(url=manga.url, user_id=str(callback.from_user.id)))
    if subs and not fav:
        await db.erase(subs)
    if subs and fav:
        await callback.answer("You are already subscribed", show_alert=True)
    if not subs and not fav:
        await callback.answer("You are not subscribed", show_alert=True)
    reply_markup = callback.message.reply_markup
    keyboard = reply_markup.inline_keyboard
    keyboard[0] = [InlineKeyboardButton(
        "Unsubscribe" if fav else "Subscribe",
        f"{'unfav' if fav else 'fav'}_{data}"
    )]
    await bot.edit_message_reply_markup(callback.from_user.id, callback.message.message_id,
                                        InlineKeyboardMarkup(keyboard))
    db_manga = await db.get(MangaName, manga.url)
    if not db_manga:
        await db.add(MangaName(url=manga.url, name=manga.name))


def is_pagination_data(callback: CallbackQuery):
    data = callback.data
    match = re.match(r'\d+_\d+', data)
    if not match:
        return False
    pagination_id = int(data.split('_')[0])
    if pagination_id not in paginations:
        return False
    pagination = paginations[pagination_id]
    if pagination.message.chat.id != callback.from_user.id:
        return False
    if pagination.message.message_id != callback.message.message_id:
        return False
    return True


@bot.on_callback_query()
async def on_callback_query(client, callback: CallbackQuery):
    if callback.data in queries:
        await plugin_click(client, callback)
    elif callback.data in mangas:
        await manga_click(client, callback)
    elif callback.data in chapters:
        await chapter_click(client, callback.data, callback.from_user.id)
    elif callback.data in full_pages:
        await full_page_click(client, callback)
    elif callback.data in favourites:
        await favourite_click(client, callback)
    elif is_pagination_data(callback):
        await pagination_click(client, callback)
    elif callback.data in language_query:
        await language_click(client, callback)
    elif callback.data.startswith('options'):
        await options_click(client, callback)
    else:
        await bot.answer_callback_query(callback.id, 'This is an old button, please redo the search', show_alert=True)
        return
    try:
        await callback.answer()
    except BaseException as e:
        print(e)


async def update_mangas():
    db = DB()
    subscriptions = await db.get_all(Subscription)
    last_chapters = await db.get_all(LastChapter)
    manga_names = await db.get_all(MangaName)

    subs_dictionary = dict()
    chapters_dictionary = dict()
    url_client_dictionary = dict()
    client_url_dictionary = {client: set() for client in plugins.values()}
    manga_dict = dict()

    for subscription in subscriptions:
        if subscription.url not in subs_dictionary:
            subs_dictionary[subscription.url] = []
        subs_dictionary[subscription.url].append(subscription.user_id)

    for last_chapter in last_chapters:
        chapters_dictionary[last_chapter.url] = last_chapter

    for manga in manga_names:
        manga_dict[manga.url] = manga

    for url in subs_dictionary:
        for ident, client in plugins.items():
            if ident in subsPaused:
                continue
            if await client.contains_url(url):
                url_client_dictionary[url] = client
                client_url_dictionary[client].add(url)

    for client, urls in client_url_dictionary.items():
        # print('')
        # print(f'Updating {client.name}')
        # print(f'Urls:\t{list(urls)}')
        # new_urls = [url for url in urls if not chapters_dictionary.get(url)]
        # print(f'New Urls:\t{new_urls}')
        to_check = [chapters_dictionary[url] for url in urls if chapters_dictionary.get(url)]
        if len(to_check) == 0:
            continue
        updated, not_updated = await client.check_updated_urls(to_check)
        for url in not_updated:
            del url_client_dictionary[url]
        # print(f'Updated:\t{list(updated)}')
        # print(f'Not Updated:\t{list(not_updated)}')

    updated = dict()

    for url, client in url_client_dictionary.items():
        try:
            if url not in manga_dict:
                continue
            manga_name = manga_dict[url].name
            if url not in chapters_dictionary:
                agen = client.iter_chapters(url, manga_name)
                last_chapter = await anext(agen)
                await db.add(LastChapter(url=url, chapter_url=last_chapter.url))
            else:
                last_chapter = chapters_dictionary[url]
                new_chapters: List[MangaChapter] = []
                async for chapter in client.iter_chapters(url, manga_name):
                    if chapter.url == last_chapter.chapter_url:
                        break
                    new_chapters.append(chapter)
                new_chapters = new_chapters[:20]
                if new_chapters:
                    last_chapter.chapter_url = new_chapters[0].url
                    await db.add(last_chapter)
                    updated[url] = list(reversed(new_chapters))
                    for chapter in new_chapters:
                        if chapter.unique() not in chapters:
                            chapters[chapter.unique()] = chapter
            await asyncio.sleep(1)
        except BaseException as e:
            print(f'An exception occurred getting new chapters for url {url}: {e}')

    for url, chapter_list in updated.items():
        for chapter in chapter_list:
            print(f'{chapter.manga.name} - {chapter.name}')
            for sub in subs_dictionary[url]:
                try:
                    await chapter_click(bot, chapter.unique(), int(sub))
                except BaseException as e:
                    print(f'An exception occurred sending new chapter: {e}')
                await asyncio.sleep(0.1)
            await asyncio.sleep(1)


async def manga_updater():
    minutes = 5
    while True:
        try:
            await update_mangas()
        except BaseException as e:
            print(f'An exception occurred during chapters update: {e}')
        await asyncio.sleep(60 * minutes)
