import json
import re
from typing import List, AsyncIterable
from urllib.parse import urlparse, urljoin, quote, quote_plus
from xml.dom.minidom import Document

from bs4 import BeautifulSoup

from plugins.client import MangaClient, MangaCard, MangaChapter, LastChapter

from .search_engine import search

class MangaSeeClient(MangaClient):

    base_url = urlparse("https://mangasee123.com/")
    search_url = urljoin(base_url.geturl(), "_search.php")
    manga_url = urljoin(base_url.geturl(), "manga")
    chapter_url = urljoin(base_url.geturl(), "read-online")
    cover_url = "https://cover.nep.li/cover"

    pre_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:97.0) Gecko/20100101 Firefox/97.0'
    }

    def __init__(self, *args, name="Mangasee", **kwargs):
        super().__init__(*args, name=name, headers=self.pre_headers, **kwargs)

    def mangas_from_page(self, documents: List):
        names = [doc['s'] for doc in documents]
        url = [f"{self.manga_url}/{doc['i']}" for doc in documents]
        images = [f"{self.cover_url}/{doc['i']}.jpg" for doc in documents]

        mangas = [MangaCard(self, *tup) for tup in zip(names, url, images)]

        return mangas

    
    def chapter_url_encode(self, chapter):
        chapter = chapter['Chapter']
        Index=""
        t = chapter[0:1]
        if t != '1':
            Index = "-index-"+t
        n = int(chapter[1:-1])
        m=""
        a = chapter[-1]
        if a != '0':
            m = "." + a
        return "-chapter-" + str(n) + m + Index + ".html"

    def chapter_display(self, chapter):
        chapter = chapter['Chapter']
        t = int(chapter[1:-1])
        n = chapter[-1]
        return t if n == '0' else str(t) + "." + n

    def chapters_from_page(self, page: bytes, manga: MangaCard = None):

        chap_pat = re.compile('vm.Chapters = ([\s\S]*?);')
        chapters_str_list = chap_pat.findall(page.decode())
        if not chapters_str_list:
            return []
        
        chapter_list = json.loads(chapters_str_list[0])
        
        index_pat = re.compile('vm.IndexName = ([\s\S]*?);')
        index_str_list = index_pat.findall(page.decode())
        if not index_str_list:
            return []

        index_str = json.loads(index_str_list[0])

        for ch in chapter_list:
            if not ch.get('Type'):
                ch['Type'] = 'Chapter'

        links = [f"{self.chapter_url}/{index_str}{self.chapter_url_encode(ch)}" for ch in chapter_list]
        texts = [f"{ch.get('Type')} {self.chapter_display(ch)}" for ch in chapter_list]

        return list(map(lambda x: MangaChapter(self, x[0], x[1], manga, []), zip(texts, links)))

    def updates_from_page(self, page: bytes):

        chap_pat = re.compile('vm.LatestJSON = ([\s\S]*?);')
        chapters_str_list = chap_pat.findall(page.decode())
        if not chapters_str_list:
            return []
        
        chapter_list = json.loads(chapters_str_list[0])

        urls = [f"{self.manga_url}/{ch['IndexName']}" for ch in chapter_list]
        chapter_urls = [f"{self.chapter_url}/{ch['IndexName']}{self.chapter_url_encode(ch)}" for ch in chapter_list]

        urls = dict(zip(urls[:32], chapter_urls[:32]))

        return urls

    def chapterImage(self, ChapterString):
        Chapter = ChapterString[1:-1]
        Odd = ChapterString[-1]
        if Odd == '0':
            return Chapter
        else:
            return Chapter + "." + Odd

    def pageImage(self, PageString):
        s = "000" + str(PageString)
        return s[-3:]

    async def pictures_from_chapters(self, content: bytes, response=None):

        chap_pat = re.compile('vm.CurChapter = ([\s\S]*?);')
        chap_str_list = chap_pat.findall(content.decode())
        if not chap_str_list:
            return []

        curChapter = json.loads(chap_str_list[0])

        path_pat = re.compile('vm.CurPathName = ([\s\S]*?);')
        path_str_list = path_pat.findall(content.decode())
        if not path_str_list:
            return []

        curPath = json.loads(path_str_list[0])

        index_pat = re.compile('vm.IndexName = ([\s\S]*?);')
        index_str_list = index_pat.findall(content.decode())
        if not index_str_list:
            return []

        index_str = json.loads(index_str_list[0])

        pages = list(range(1, int(curChapter['Page']) + 1))

        images_url = [f"https://{curPath}/manga/{index_str}/{'' if curChapter['Directory'] == '' else curChapter['Directory'] + '/'}{self.chapterImage(curChapter['Chapter'])}-{self.pageImage(page)}.png" for page in pages]

        return images_url

    async def search(self, query: str = "", page: int = 1) -> List[MangaCard]:
        def text_from_document(doc) -> str:
            return doc['i'] + ' ' + doc['s'] + ' ' + ' '.join(doc['a'])

        request_url = self.search_url

        content = await self.get_url(request_url, method="post", file_name=f'search/{quote_plus(query)}.json', cache=True)

        documents = json.loads(content)

        results = search(query, documents, text_from_document)

        return self.mangas_from_page(results)

    async def get_chapters(self, manga_card: MangaCard, page: int = 1) -> List[MangaChapter]:

        request_url = f'{manga_card.url}'

        content = await self.get_url(request_url)

        return self.chapters_from_page(content, manga_card)[(page - 1) * 20:page * 20]

    async def iter_chapters(self, manga_url: str, manga_name) -> AsyncIterable[MangaChapter]:
        
        manga_card = MangaCard(self, manga_name, manga_url, '')

        request_url = f'{manga_card.url}'

        content = await self.get_url(request_url)

        for ch in self.chapters_from_page(content, manga_card):
            yield ch

    async def contains_url(self, url: str):
        return url.startswith(self.base_url.geturl())

    async def check_updated_urls(self, last_chapters: List[LastChapter]):

        content = await self.get_url(self.base_url.geturl())

        updates = self.updates_from_page(content)

        updated = [lc.url for lc in last_chapters if updates.get(lc.url) and updates.get(lc.url) != lc.chapter_url]
        not_updated = [lc.url for lc in last_chapters if not updates.get(lc.url) or updates.get(lc.url) == lc.chapter_url]

        return updated, not_updated