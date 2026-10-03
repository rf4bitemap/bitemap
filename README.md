# BiteMap

**Where are the fish biting in Russian Fishing 4?** BiteMap is two things:

* **BiteMap Logger** – a small Windows app that runs next to RF4 and logs every fish you catch
  (species, weight, length, trophy) together with **the spot** you caught it at – automatically, by reading the screen.
* **BiteMap website** – a community map that turns everyone's shared catches into hotspots per waterbody and fish.

Game languages: **English, German, Russian**. App and website UI: English, German, Russian.

> Fan project – not affiliated with Fishsoft or Russian Fishing 4. The logger only reads pixels from the screen.
> It never automates input and never touches game files or memory.

## How the logger works

| What | How |
|---|---|
| Spot | Every 1.5 s it reads the coordinates under the compass (bottom right, e.g. `73:48`). When a fish bites, the spot is pinned at that moment and attached to the catch (in-game notifications often cover the coordinates right then, so the last confirmed value is used). |
| Catch | When the catch card appears (found by its weight and ruler icons – language independent) it reads the fish name, weight, length and trophy badge. |
| Fish | The name is matched against all 252 species in EN/DE/RU (`data/fish.json`), so small OCR errors don't matter. |
| Waterbody | Chosen once in the app (the HUD doesn't show it). Remembered between sessions. |
| Bite alert | Sound when the round fish icon appears at the left end of the line-tension bar. |
| Sharing | Optional and anonymous: fish, weight, length, waterbody, spot and time are sent with a random install token. No screenshots, no account. |

Everything is stored locally in `%APPDATA%\BiteMap\catches.db` (SQLite); the app works without the server.
Unreadable catch cards are kept as `? unknown` and can be fixed with **Edit** (double-click a row).

## Download & use

1. Download `BiteMapLogger-<version>-win64.zip` from [Releases](../../releases), unzip anywhere, run `BiteMapLogger.exe`.
2. Pick the waterbody you're fishing on. Leave *Game language* on *Auto*.
3. Fish. Catches appear in the list; shared ones get a ✓.

RF4 can run windowed, borderless or fullscreen; the logger reads the game window's area of the screen,
so the catch card and the bottom-right HUD must not be covered by other windows.

**Updates:** from 0.1.4 on, the logger checks GitHub for new versions and shows a bar when one is out.
*Update & restart* downloads it, checks its signature and checksum, replaces the app files and restarts;
settings and your catch list (in `%APPDATA%\BiteMap`) stay untouched. In a write-protected folder
(e.g. Program Files) it only offers the download. Can be turned off in the settings.

## Repository layout

```
client/            BiteMap Logger (Python, customtkinter, OpenCV, Tesseract)
  bitemap_logger/  app code; detect/ = catch card, coordinates, bite icon
  tests/           detector + full engine replay tests on sample screenshots
server/            FastAPI + PostgreSQL API, serves the website (server/web)
data/              fish.json, waterbodies.json (shared by app and server), maps/
tools/             build_data.py (fish list from official record tables), build_client.py (release build),
                   make_template.py, seed_demo.py, samples/
deploy/            Caddyfile;  docker-compose.yml at the root
docs/DEPLOY.md     running the server on a VPS
```

## Development

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
choco install tesseract                    # or install the UB-Mannheim Tesseract build
.venv\Scripts\python tools\build_client.py # vendors Tesseract + language data, builds dist\BiteMapLogger
.venv\Scripts\python -m pytest client\tests server\tests
.venv\Scripts\python -m bitemap_logger     # run from source (cd client first)
```

Local server with demo data:

```bash
.venv\Scripts\python -m uvicorn app.main:app --app-dir server --port 8000
.venv\Scripts\python tools\seed_demo.py http://127.0.0.1:8000
```

Before publishing a release, set `DEFAULT_SERVER` / `PROJECT_URL` in `client/bitemap_logger/__init__.py`
and `RELEASES_URL` in `server/web/app.js`. Pushing a tag `v*` builds and publishes the release on GitHub.

The release also gets a `latest.json` for the in-app updater, signed with an Ed25519 key from the
`BITEMAP_SIGNING_KEY` Actions secret; the app only accepts manifests signed with the matching
`PUBLIC_KEY` in `client/bitemap_logger/updater.py`. A fork makes its own pair with
`python tools/sign_release.py --keygen <file>`, puts the public key into `updater.py` and the file's
content into the secret.

## Contributing data

* **Fish list** – `python tools/build_data.py` rebuilds `data/fish.json` and `data/waterbodies.json` from the official
  RF4 absolute record tables (EN/DE/RU). Manual name fixes go to `data/overrides.json`.
* **Maps** – one image per waterbody in `data/maps/<waterbody_id>.webp`. The image must span exactly the
  coordinate bounds stored in that waterbody's `"map"` entry in `data/waterbodies.json`
  (`min_x/max_x/min_y/max_y`, north up, y grows upwards – the same convention as the in-game map).
  Bounds for all 18 waterbodies come from [rf4-stat.com](https://rf4-stat.com). For a new image, open `/calibrate` on the
  server, click two grid points, enter their coordinates and paste the result. The images are RF4 artwork and are not
  part of this repository (`.gitignore`); copy them to the server's `data/maps/` folder. Without an image the
  website shows a coordinate grid.
* **Templates** – detector icons (catch-card weight/ruler icons, bite icon) are cut from screenshots with
  `python tools/make_template.py SHOT.png bite_icon x y w h`. Full-resolution PNG screenshots give the best templates.
* **Translations** – `client/bitemap_logger/locales/*.json` and `server/web/i18n.js`.

## License

MIT – see [LICENSE](LICENSE). Bundled: Tesseract OCR and tessdata_fast (Apache-2.0).
Game names and map images belong to their respective owners.
