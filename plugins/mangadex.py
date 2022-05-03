import json
from dataclasses import dataclass
from typing import List, AsyncIterable
from urllib.parse import urlparse, urljoin, quote

from plugins.client import MangaClient, MangaCard, MangaChapter, LastChapter


@dataclass
class MangaDexMangaCard(MangaCard):
    id: str

    def get_url(self):
        return f"https://mangadex.org/title/{self.id}"


@dataclass
class MangaDexMangaChapter(MangaChapter):
    id: str

    def get_url(self):
        return f"https://mangadex.org/chapter/{self.id}"


class MangaDexClient(MangaClient):

    base_url = urlparse("https://api.mangadex.org/")
    search_url = urljoin(base_url.geturl(), "manga")
    search_param = 'q'
    latest_uploads = 'https://api.mangadex.org/chapter?limit=32&offset=0&includes[]=manga&contentRating[]=safe&contentRating[]=suggestive&contentRating[]=erotica&order[readableAt]=desc'

    covers_url = urlparse("https://uploads.mangadex.org/covers")

    pre_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:97.0) Gecko/20100101 Firefox/97.0'
    }

    def __init__(self, *args, name="MangaDex", language="en", **kwargs):
        super().__init__(*args, name=f'{name}-{language}', headers=self.pre_headers, **kwargs)
        self.language = language

    def mangas_from_page(self, page: bytes):
        dt = json.loads(page.decode())

        cards = dt['data']

        names = [list(card['attributes']['title'].values())[0] for card in cards]
        ids = [card["id"] for card in cards]
        url = [f'https://api.mangadex.org/manga/{card["id"]}/feed?translatedLanguage[]={self.language}' for card in cards]

        cover_filename = lambda x: list(filter(lambda y: y['type'] == 'cover_art', x))[0]['attributes']['fileName']

        images = [f'https://uploads.mangadex.org/covers/{card["id"]}/{cover_filename(card["relationships"])}.512.jpg' for card in cards]

        mangas = [MangaDexMangaCard(self, *tup) for tup in zip(names, url, images, ids)]

        return mangas

    def chapters_from_page(self, page: bytes, manga: MangaCard = None):
        dt = json.loads(page.decode())

        dt_chapters = dt['data']

        visited = set()
        chapters = []
        for chapter in dt_chapters:
            if chapter["attributes"]["chapter"] not in visited:
                visited.add(chapter["attributes"]["chapter"])
                chapters.append(chapter)

        def chapter_name(c):
            if c["attributes"]["title"]:
                return f'{c["attributes"]["chapter"]} - {c["attributes"]["title"]}'
            return f'{c["attributes"]["chapter"]}'

        ids = [chapter.get("id") for chapter in chapters]
        links = [f'https://api.mangadex.org/at-home/server/{chapter.get("id")}?forcePort443=false' for chapter in chapters]
        texts = [chapter_name(chapter) for chapter in chapters]

        return list(map(lambda x: MangaDexMangaChapter(self, x[0], x[1], manga, [], x[2]), zip(texts, links, ids)))

    async def pictures_from_chapters(self, content: bytes, response=None):
        dt = json.loads(content)

        if dt.get('result') == 'error':
            return []

        base_url = dt['baseUrl']
        chapter_hash = dt['chapter']['hash']
        file_names = dt['chapter']['data']

        images_url = [f"{base_url}/data/{chapter_hash}/{file}" for file in file_names]

        return images_url

    async def search(self, query: str = "", page: int = 1) -> List[MangaCard]:
        query = quote(query)

        request_url = f'{self.search_url}?limit=20&offset={(page - 1) * 20}&includes[]=cover_art&includes[]=author&includes[' \
                      f']=artist&contentRating[]=safe&contentRating[]=suggestive&contentRating[' \
                      f']=erotica&title={query}&order[relevance]=desc'

        content = await self.get_url(request_url)

        return self.mangas_from_page(content)

    async def get_chapters(self, manga_card: MangaCard, page: int = 1, count: int = 10) -> List[MangaChapter]:

        request_url = f'{manga_card.url}' \
                      f'&limit={count}&offset={(page - 1) * count}&includes[' \
                      f']=scanlation_group&includes[]=user&order[volume]=desc&order[' \
                      f'chapter]=desc&contentRating[]=safe&contentRating[]=suggestive&contentRating[' \
                      f']=erotica&contentRating[]=pornographic'

        content = await self.get_url(request_url)

        return self.chapters_from_page(content, manga_card)

    async def iter_chapters(self, manga_url: str, manga_name) -> AsyncIterable[MangaChapter]:
        manga = MangaCard(self, manga_name, manga_url, '')
        page = 1
        while page > 0:
            chapters = await self.get_chapters(manga_card=manga, page=page, count=500)
            if not chapters:
                break
            for chapter in chapters:
                yield chapter
            page += 1

    async def contains_url(self, url: str):
        return url.startswith(self.base_url.geturl()) and url.endswith(self.language)

    async def check_updated_urls(self, last_chapters: List[LastChapter]):

        content = await self.get_url(f'{self.latest_uploads}&translatedLanguage[]={self.language}')

        data = json.loads(content)['data']

        updates = {}
        for item in data:
            ch_id = item['id']
            manga_id = None
            for rel in item['relationships']:
                if rel['type'] == 'manga':
                    manga_id = rel['id']
            if manga_id and not updates.get(manga_id):
                updates[manga_id] = ch_id

        updated = []
        not_updated = []

        for lc in last_chapters:
            upd = False
            for manga_id, ch_id in updates.items():
                if manga_id in lc.url and not ch_id in lc.chapter_url:
                    upd = True
                    updated.append(lc.url)
                    break
            if not upd:
                not_updated.append(lc.url)

        return updated, not_updated
