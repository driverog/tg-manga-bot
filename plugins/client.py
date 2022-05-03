import os
from abc import abstractmethod, ABC
from dataclasses import dataclass
from typing import List, AsyncIterable

from aiohttp import ClientSession
from pathlib import Path

from models import LastChapter

@dataclass
class MangaCard:
    client: "MangaClient"
    name: str
    url: str
    picture_url: str

    def get_url(self):
        return self.url

    def unique(self):
        return str(hash(self.url))


@dataclass
class MangaChapter:
    client: "MangaClient"
    name: str
    url: str
    manga: MangaCard
    pictures: List[str]

    def get_url(self):
        return self.url

    def unique(self):
        return str(hash(self.url))


def clean(folder_name):
    while '  ' in folder_name:
        folder_name = folder_name.replace('  ', ' ')
    return folder_name.replace(':', '')


class MangaClient(ClientSession, ABC):

    def __init__(self, *args, name="client", **kwargs):
        if name == "client":
            raise NotImplementedError
        super().__init__(*args, **kwargs)
        self.name = name

    async def get_url(self, url, *args, file_name=None, cache=False, req_content=True, method='get', data=None,
                      **kwargs):
        def response(): pass
        response.status = "200"
        if cache:
            path = Path(f'cache/{self.name}/{file_name}')
            os.makedirs(path.parent, exist_ok=True)
            try:
                with open(path, 'rb') as f:
                    content = f.read()
            except FileNotFoundError:
                if method == 'get':
                    response = await self.get(url, *args, **kwargs)
                elif method == 'post':
                    response = await self.post(url, data=data or {}, **kwargs)
                else:
                    raise ValueError
                if str(response.status).startswith('2'):
                    content = await response.read()
                    with open(path, 'wb') as f:
                        f.write(content)
        else:
            if method == 'get':
                response = await self.get(url, *args, **kwargs)
            elif method == 'post':
                response = await self.post(url, data=data or {}, **kwargs)
            else:
                raise ValueError
            content = await response.read()
        if req_content:
            return content
        else:
            return response

    async def set_pictures(self, manga_chapter: MangaChapter):
        requests_url = manga_chapter.url

        response = await self.get(requests_url)

        content = await response.read()

        manga_chapter.pictures = await self.pictures_from_chapters(content, response)

        return manga_chapter

    async def download_pictures(self, manga_chapter: MangaChapter):
        if not manga_chapter.pictures:
            await self.set_pictures(manga_chapter)

        folder_name = f'{clean(manga_chapter.manga.name)}/{clean(manga_chapter.name)}'
        i = 0
        for picture in manga_chapter.pictures:
            ext = picture.split('.')[-1]
            file_name = f'{folder_name}/{format(i, "05d")}.{ext}'
            for _ in range(3):
                req = await self.get_picture(picture, file_name=file_name, cache=True, req_content=False)
                if str(req.status).startswith('2'):
                    break
            else:
                raise ValueError
            i += 1

        return Path(f'cache/{manga_chapter.client.name}') / folder_name

    async def get_picture(self, url, *args, **kwargs):
        return await self.get_url(url, *args, **kwargs)

    async def check_updated_urls(self, last_chapters: List[LastChapter]):
        return [lc.url for lc in last_chapters], []

    @abstractmethod
    async def search(self, query: str = "", page: int = 1) -> List[MangaCard]:
        raise NotImplementedError

    @abstractmethod
    async def get_chapters(self, manga_card: MangaCard, page: int = 1) -> List[MangaChapter]:
        raise NotImplementedError

    @abstractmethod
    async def contains_url(self, url: str):
        raise NotImplementedError

    @abstractmethod
    async def iter_chapters(self, manga_url: str, manga_name: str) -> AsyncIterable[MangaChapter]:
        raise NotImplementedError

    @abstractmethod
    async def pictures_from_chapters(self, content: bytes, response=None):
        raise NotImplementedError
