# bones - agent notes

Python static site generator. This repo is public; the real sites live in a private repo (`sites/demosite` here is only the example).

## Commands

- Build one site, from the folder that holds your sites: `bash ../bones/build.sh <site> [prod|serve|watch]` (no mode = dev build; `watch` runs until stopped).
- Publish a draft, from the folder that holds your sites: `bash ../bones/build.sh <site> publish <part of draft name>`. It moves the draft to `content/posts/<year>/<today>-<slug>/<today>-<slug>.md`, adds `images/`, and sets the frontmatter `date` to now.
- Deploy and server ops: `ship/README.md` (run from the folder that holds your sites).

## Gotchas

- A build rewrites tracked files in your sites folder (`<site>/z-public/**`, `templates/partials/auto-generated/**`). Don't commit them as part of unrelated work.
- `ship/` is public: no hostnames, IPs, emails or secrets in it. Site-specific data goes in that private repo, secrets in a secrets folder outside all repos (never read by agents).
- `.env` is the user's; agents never read it.

## Code style

- Site settings go in the site's `config/`, text in `content/`, logic in the Python code. Never hardcode a site's values in the generator.
- No fallbacks or workarounds: fix the real cause.

## Repo map

Paths are relative to the repo root.

- `gen.py`: main generator. Builds one site from its config and content.
- `dev.py`: serves a site's dev build. `--watch` rebuilds it when its sources change.
- `init.py`: creates the folders and config files for a new site.
- `build.sh`: builds, serves, watches or publishes a site (Git Bash, run from the folder that holds your sites).
- `requirements.txt`: Python packages for the generator.
- `themes/default/`: the default theme.
- `themes/default/templates/`: Jinja2 templates (`base.html`, `post.html`, `page.html`, `partials/`).
- `themes/default/assets/`: theme CSS, fonts and images.
- `themes/default/content/pages/`: built-in pages (404, feed, robots, sitemap).
- `sites/demosite/`: example site. Shows the layout of a real site.
- `sites/demosite/config/`: site settings (`build.yml`, `content/*.yml`, `setup.json`).
- `sites/demosite/content/`: posts (`posts/<year>/<date>-<slug>/`) and pages (`pages/<name>/`).
- `ship/README.md`: deploy and server ops for bones sites. Read it first.
- `ship/deploy-site.sh`: builds one site and publishes it as a new release.
- `ship/ops.sh`: day-to-day server commands for a site.
- `ship/stats.py`: visitor stats and errors from nginx logs.
- `.claude/settings.json`: Claude Code settings. `.claude/hooks/`: hook scripts.
- `README.md`: user guide (setup, configuration, commands).
