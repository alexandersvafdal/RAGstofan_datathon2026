"""The static demo site for GitHub Pages: frontend/ copied to _site/, with demo.js loaded before app.js (it answers
the app's /api calls from saved answers), links to "/" kept inside the project's folder, and the tags that give
the link a preview card (title, description, image) when it is shared.

  python scripts/build_demo_site.py   then serve _site/ (the Pages workflow publishes it)
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"
URL = "https://alexandersvafdal.github.io/RAGstofan_datathon2026/"
TITLE = "RAGstofan demo · Iceland, in numbers"
DESCRIPTION = ("Static demo with saved answers. Ask Iceland's official statistics in Icelandic or English and get the figure back with its source, "
               "a chart or a map. 2nd place at Gagnaþon 2026, Statistics Iceland.")
CARD = f"""  <meta name="description" content="{DESCRIPTION}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{URL}">
  <meta property="og:title" content="{TITLE}">
  <meta property="og:description" content="{DESCRIPTION}">
  <meta property="og:image" content="{URL}preview.png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="RAGstofan: a map of Iceland's municipalities and a chart, answered from Statistics Iceland">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{TITLE}">
  <meta name="twitter:description" content="{DESCRIPTION}">
  <meta name="twitter:image" content="{URL}preview.png">
"""


def main():
    shutil.rmtree(SITE, ignore_errors=True)
    shutil.copytree(ROOT / "frontend", SITE)
    page = (SITE / "index.html").read_text()
    for old, new in [('<title>RAGstofan</title>\n', f"<title>{TITLE}</title>\n{CARD}"),
                     ('<script src="app.js', '<script src="demo.js?v=4"></script>\n  <script src="app.js'),
                     ('href="/"', 'href="./"')]:
        assert old in page, old
        page = page.replace(old, new, 1)
    (SITE / "index.html").write_text(page)
    (SITE / ".nojekyll").touch()
    print(f"built {SITE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
