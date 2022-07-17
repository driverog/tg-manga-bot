from plugins import MangaChapter


def img2html(manga_chapter: MangaChapter, name):
    n = '''<div class="idkpythonbot" style="background-color:black">'''
    for i in manga_chapter.pictures:
        n = f'''{n}
    <div class="image">
      <img src="{i}" style="width:100%"><br>
      <br>
    </div>
        '''
    with open(f"{name}.html", "w") as f:
        f.write(n)
