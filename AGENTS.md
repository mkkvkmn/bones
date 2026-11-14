# bones - agent notes

Python static site generator. This repo is public; the real sites live in the private sibling repo `../sites` (`sites/demosite` here is only the example).

## Commands

- Build one site, from the sites repo root: `bash ../bones/build.sh <site> [prod|serve|watch]` (no mode = dev build; `watch` runs until stopped).
- Publish a draft, from the sites repo root: `bash ../bones/build.sh <site> publish <part of draft name>`. It moves the draft to `content/posts/<year>/<today>-<slug>/<today>-<slug>.md`, adds `images/`, and sets the frontmatter `date` to now.
- Deploy and server ops: `ship/README.md` (run from the sites repo root).

## Gotchas

- A build rewrites tracked files in the sites repo (`<site>/config/setup.json`, `<site>/z-public/**`, `templates/partials/auto-generated/**`). Don't commit them as part of unrelated work.
- `ship/` is public: no hostnames, IPs, emails or secrets in it. Site-specific data goes in `../sites/ship/`, secrets in `~/.secrets/sites/` (never read by agents).
- `.env` is the user's; agents never read it.

## Code style

- Site settings go in the site's `config/`, text in `content/`, logic in the Python code. Never hardcode a site's values in the generator.
- No fallbacks or workarounds: fix the real cause.
