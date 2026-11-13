import argparse
import functools
import json
import logging
import os
import pickle
import re
import shutil
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime
from itertools import repeat
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote

import markdown
import yaml
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

YAML_PATTERNS = ["*.yml", "*.yaml"]
YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # libyaml when installed
JINJA_MARKERS = ("{{", "{%", "{#")
REQUIRED_ENV_VARS = ["SITES_FOLDER", "SITE_NAME"]
CONTENT_SUFFIXES = (".md", ".html", ".xml", ".txt")
PARALLEL_MIN_DOCS = 100  # on a smaller site, starting worker processes costs more than it saves

ConfigType = Dict[str, Any]
Doc = Dict[str, Any]

# =============================================================================
# 1. CONFIGURATION LOADING
# =============================================================================


def load_initial_config() -> ConfigType:
    """Load initial configuration with args, env, and YAML content."""
    config = add_yml_content_to_config(add_args_and_env_to_config(init_config()))
    if config.get("build", {}).get("settings", {}).get("create_config", False):
        save_dict("initial", config, config)
    return config


def init_config() -> ConfigType:
    """Create initial configuration object with basic structure."""
    return {
        "env": {},
        "build": {"settings": {"theme": {"dir": ""}, "site": {"dir": ""}}},
        "content": {
            "posts": {"defaults": {}, "items": {}},
            "pages": {"defaults": {}, "tags": {"enabled": False, "items": {}}},
            "assets": {"css": [], "images": [], "fonts": [], "favicon": []},
            "latest_posts": {},
        },
    }


def add_args_and_env_to_config(config: ConfigType) -> ConfigType:
    """Add command line arguments and environment variables to config."""
    parser = argparse.ArgumentParser(description="Static Site Generator v4")
    parser.add_argument("--env", "-e", choices=["dev", "prod"], default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--site-name", "-s", help="Site name (overrides SITE_NAME env var)")
    parser.add_argument("--sites-folder", "-sf", help="Sites folder path (overrides SITES_FOLDER env var)")
    parser.add_argument(
        "--single-file-path",
        "-sfp",
        help="Build only a single file (fast mode, skips dependencies). Use full file path.",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    load_dotenv()
    env = config["env"]
    env.update((k, v) for k, v in os.environ.items() if k.startswith(("SITES_FOLDER", "SITE_NAME", "BUILD_ENV")))

    # Command line args take precedence over env vars
    if args.site_name:
        env["SITE_NAME"] = args.site_name
    if args.sites_folder:
        env["SITES_FOLDER"] = args.sites_folder
    if args.single_file_path:
        env["SINGLE_FILE_PATH"] = args.single_file_path
    if args.env is not None:
        env["BUILD_ENV"] = args.env
    env.setdefault("BUILD_ENV", "dev")

    logger.info(f"Start: {env['SITE_NAME']} ({env['BUILD_ENV']})")

    missing_vars = [var for var in REQUIRED_ENV_VARS if var not in env]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
    return config


def add_yml_content_to_config(config: ConfigType) -> ConfigType:
    """Load site configuration from YAML files based on folder structure."""
    site_dir = Path(config["env"]["SITES_FOLDER"]) / config["env"]["SITE_NAME"].lower()
    config_dir = site_dir / "config"
    if not config_dir.exists():
        raise ValueError(f"Config directory not found: {config_dir}")

    for yaml_file in [f for pattern in YAML_PATTERNS for f in config_dir.rglob(pattern)]:
        try:
            # The file path is the config key: content/pages.yml -> config["content"]["pages"]
            keys = ".".join(yaml_file.relative_to(config_dir).with_suffix("").parts).split(".")
            *parents, target_key = keys
            with open(yaml_file, "r", encoding="utf-8") as f:
                yaml_content = yaml.safe_load(f)
            if not yaml_content:
                continue

            current = config
            for key in parents:
                current = current.setdefault(key, {})

            # A file may repeat its own name as its single top-level key (e.g. "pages:" in pages.yml)
            if isinstance(yaml_content, dict) and list(yaml_content) == [target_key]:
                yaml_content = yaml_content[target_key]

            if isinstance(current.get(target_key), dict) and isinstance(yaml_content, dict):
                merge_config_dicts(current[target_key], yaml_content)
            else:
                current[target_key] = yaml_content
            logger.debug(f"Loaded config: {'.'.join(keys)}")
        except Exception as e:
            raise ValueError(f"Failed to load config file {yaml_file}: {e}")

    theme_name = config.get("content", {}).get("site", {}).get("theme", "default")
    config["build"]["settings"]["theme"]["dir"] = str(Path(f"themes/{theme_name}/"))
    config["build"]["settings"]["site"]["dir"] = str(site_dir)

    if "envs" in config["build"]:
        env_config = config["build"]["envs"].get(config["env"]["BUILD_ENV"], {})
        config["env"]["url"] = env_config.get("url", "")
        config["env"]["output_dir"] = env_config.get("output", "")
    return config


# =============================================================================
# 2. ADD DISCOVERED CONTENT TO CONFIG
# =============================================================================


def add_discovered_to_config(config: ConfigType) -> ConfigType:
    """Discover all content and add it to the config."""
    config = scan_content_dirs(config)
    if config.get("build", {}).get("settings", {}).get("create_config", False):
        save_dict("after discovery", config, config)
    return config


def scan_content_dirs(config: ConfigType) -> ConfigType:
    """Discover all content and merge it into the existing config."""
    site_dir = Path(config["build"]["settings"]["site"]["dir"])
    theme_dir = Path(config["build"]["settings"]["theme"]["dir"])
    year_dirs = [d for d in (site_dir / "content" / "posts").iterdir() if d.is_dir()]

    # In this order: site pages are scanned after theme pages to override them.
    for path in (theme_dir / "content" / "pages", site_dir / "content" / "pages"):
        scan_content_dir(path, "pages", config)
    for path in year_dirs:
        scan_content_dir(path, "posts", config)
    for path in (site_dir / "assets", theme_dir / "assets"):
        scan_assets_dir(path, config)
    return config


def walk_files(path: Path) -> List[str]:
    """All files under path, in sorted order."""
    return [str(Path(root) / f) for root, _, files in sorted(os.walk(path)) for f in sorted(files)]


def scan_content_dir(path: Path, content_type: str, config: ConfigType) -> None:
    """Add the content files (and the images next to them) under path to the config."""
    items = config["content"][content_type]["items"]
    for root, dirs, files in os.walk(path):
        dirs.sort()
        files.sort()
        img_dir = Path(root) / "images"
        if img_dir.exists():
            config["content"]["assets"].setdefault("images", []).extend(walk_files(img_dir))

        for filename in files:
            if filename.endswith(CONTENT_SUFFIXES):
                file_path = Path(root) / filename
                # Merges with the item's config when there is one
                items.setdefault(file_path.stem, {}).update(
                    file_path=str(file_path.absolute()), content_type=content_type, name=file_path.stem
                )


def scan_assets_dir(path: Path, config: ConfigType) -> None:
    """Add the files under each asset type folder (css, images, ...) to the config."""
    if path.exists():
        for subdir in sorted(path.iterdir()):
            if subdir.is_dir():
                config["content"]["assets"].setdefault(subdir.name, []).extend(walk_files(subdir))


# =============================================================================
# 3. ADD PROCESSED CONTENT TO CONFIG
# =============================================================================


def add_processed_content_to_config(config: ConfigType, pool: Optional[ProcessPoolExecutor]) -> ConfigType:
    """Process all discovered content and add it to the global config."""
    posts, config = process_docs(config, pool)
    config = add_latest_posts_to_config(posts, config)
    config = add_tags_to_config(posts, config)
    config = add_tag_pages_to_config(posts, config)
    config = trim_slashes_from_config(config)
    validate_content_urls(config)
    save_dict("after processing", config, config)
    return config


def process_docs(config: ConfigType, pool: Optional[ProcessPoolExecutor]) -> Tuple[List[Doc], ConfigType]:
    """Convert and enrich all discovered content, updating config directly."""
    items = [*config["content"]["posts"]["items"].values(), *config["content"]["pages"]["items"].values()]
    results = parallelize(pool, process_single_doc, items, config, description="content files")

    posts, pages, errors = [], [], []
    for doc in (doc for docs in results for doc in docs):
        if "error" in doc:
            errors.append(f"{doc['file_path']}: {doc['error']}")
        elif doc["content_type"] == "posts":
            posts.append(doc)
        else:
            pages.append(doc)
    if errors:
        raise ValueError("Process doc failed:\n" + "\n".join(f"  {e}" for e in errors))

    posts.sort(key=lambda p: p["name"])
    posts.sort(key=lambda p: p.get("date"), reverse=True)
    pages.sort(key=lambda p: (p.get("nav_title", "zzz"), p["name"]))
    add_navigation_links_to_docs(posts)

    config["content"]["posts"]["items"] = {post["name"]: post for post in posts}
    config["content"]["pages"]["items"] = {page["name"]: page for page in pages}
    return posts, config


def process_single_doc(item: Doc, config: ConfigType) -> List[Doc]:
    """Parse front matter and expand multi-language content into multiple documents."""
    file_path = Path(item["file_path"])
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            front_matter, body = parse_front_matter_and_content(f.read())

        # Front matter overrides the item's config, which overrides the content type defaults
        doc = {**config["content"][item["content_type"]]["defaults"], **item, **front_matter, "content": body}

        if not isinstance(doc.get("languages"), dict):
            return [enrich_doc(doc, body, file_path, config)]
        return [
            enrich_doc({**doc, **overrides, "language": lang, "name": f"{doc['name']}-{lang}"}, body, file_path, config)
            for lang, overrides in doc["languages"].items()
        ]
    except Exception as e:
        logger.warning(f"Failed to process {item.get('file_path', '??')}: {e}")
        return []


def parse_front_matter_and_content(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse front matter from content string and return (front_matter, body)."""
    parts = content.split("---", 2)
    if not content.startswith("---") or len(parts) < 3:
        return {}, content
    try:
        return yaml.load(parts[1], Loader=YAML_LOADER) or {}, parts[2]
    except yaml.YAMLError as e:
        logger.warning(f"Could not parse front matter: {e}")
        return {}, content


def enrich_doc(doc: Doc, body: str, file_path: Path, config: ConfigType) -> Doc:
    """Enrich a document with HTML, URL, dates, and read time."""
    doc = add_html_to_doc(body, doc, config)
    doc = add_url_to_doc(doc, config)
    doc = add_dates_to_doc(doc, file_path)
    doc = add_read_time_to_doc(doc, config)
    return doc


def normalize_markdown_html(html: str) -> str:
    """Tidy Python-Markdown output so fragments read cleanly inside templates."""
    html = re.sub(r"\s*\n\s*</p>", "</p>", html)
    html = re.sub(r"</p>\s*<p>", "</p>\n<p>", html)
    html = re.sub(r'<div class="toc">', '<div class="toc">\n', html)
    html = re.sub(r"</span><ul>", "</span>\n<ul>", html)
    html = re.sub(r"<ul>\s*<li>", "<ul>\n<li>", html)
    html = re.sub(r"</li>\s*<li>", "</li>\n<li>", html)
    html = re.sub(r"</li>\s*</ul>", "</li>\n</ul>", html)
    html = re.sub(r"</ul>\s*</div>", "</ul>\n</div>", html)
    html = re.sub(r"</div>\s*<h2", "</div>\n\n<h2", html)
    return html


@functools.lru_cache(maxsize=None)
def markdown_converter(toc_title: str, toc_depth: Optional[int]) -> markdown.Markdown:
    """A converter per table of contents setup: building one costs as much as a small document."""
    toc = {"title": toc_title} if toc_depth is None else {"title": toc_title, "toc_depth": toc_depth}
    return markdown.Markdown(extensions=["markdown.extensions.attr_list", "toc"], extension_configs={"toc": toc})


def add_html_to_doc(content: str, doc: Doc, config: ConfigType) -> Doc:
    """Convert markdown content to HTML, or keep as-is for non-markdown files."""
    if doc.get("file_path", "").endswith(".md"):
        lang_config = config.get("content", {}).get("languages", {}).get(doc.get("language"), {})
        toc_depth = doc.get("toc_depth")
        if not (isinstance(toc_depth, int) and 1 <= toc_depth <= 6):
            toc_depth = None
        converter = markdown_converter(lang_config.get("table_of_contents_title", "Table of Contents"), toc_depth)
        content = normalize_markdown_html(converter.reset().convert(content))
    doc["html_content"] = content
    return doc


def add_url_to_doc(doc: Doc, config: ConfigType) -> Doc:
    """Handle all URL logic: target_file for pages and language URL prefix logic."""
    if "target_file" in doc:
        doc["url"] = doc["target_file"]
    if "language" not in doc:
        raise ValueError(f"Doc missing 'language': {doc.get('name', '')}")

    url, language = doc["url"], doc["language"]
    lang_config = config.get("content", {}).get("languages", {}).get(language, {})
    if not lang_config.get("skip_language_path_in_url", False):
        url = f"{language}/{url}"
        if "target_file" in doc:
            doc["target_file"] = f"{language}/{doc['target_file']}"
    doc["url"] = url
    return doc


def add_dates_to_doc(doc: Doc, file_path: Path) -> Doc:
    """Process dates and create formatted versions."""
    date_obj = doc.get("date")
    if not date_obj:
        raise ValueError(f"Missing required 'date' field in {file_path}")
    if not isinstance(date_obj, datetime):
        try:
            # Standardized date format: YYYY-MM-DD HH:MM:SS +0000
            date_obj = datetime.strptime(date_obj, "%Y-%m-%d %H:%M:%S %z")
        except Exception as e:
            raise ValueError(f"Failed to parse date '{date_obj}' for {file_path}: {e}")

    doc["date"] = date_obj
    doc["date_html"] = date_obj.strftime("%d.%m.%Y")
    doc["date_iso"] = date_obj.isoformat()
    doc["date_xml_feed"] = date_obj.strftime("%a, %d %b %Y %H:%M:%S %z")
    return doc


def add_read_time_to_doc(doc: Doc, config: ConfigType) -> Doc:
    """Add read time to document."""
    words_per_minute = config.get("build", {}).get("settings", {}).get("read_time_words_per_minute", 200)
    doc["read_time"] = max(1, len(doc.get("content", "").split()) // words_per_minute)
    return doc


def add_navigation_links_to_docs(posts: List[Doc]) -> None:
    """Add navigation metadata for posts (next/previous post links) within the same language."""
    posts_by_language: Dict[str, List[Doc]] = {}
    for post in posts:
        posts_by_language.setdefault(post.get("language", "en"), []).append(post)

    for lang_posts in posts_by_language.values():
        for i, post in enumerate(lang_posts):
            newer = lang_posts[i - 1] if i > 0 else {}
            older = lang_posts[i + 1] if i < len(lang_posts) - 1 else {}
            post["next_post_url"] = newer.get("url", "")
            post["next_post_title"] = newer.get("title", "")
            post["prev_post_url"] = older.get("url", "")
            post["prev_post_title"] = older.get("title", "")


def add_latest_posts_to_config(posts: List[Doc], config: ConfigType) -> ConfigType:
    """Store latest posts by language in config."""
    if posts:
        latest_posts: Dict[str, Doc] = {}
        for post in posts:
            if post.get("language"):
                latest_posts.setdefault(post["language"], post)
        config["content"]["latest_posts"] = latest_posts

        for language, post in latest_posts.items():
            logger.debug(f"Latest post ({language}): {post.get('title', '??')} ({post.get('date_html', 'No date')})")
    return config


def add_tags_to_config(posts: List[Doc], config: ConfigType) -> ConfigType:
    """Collect the tags used by each language's posts and store them in config."""
    tag_config = config.get("content", {}).get("pages", {}).get("tags", {})
    if tag_config.get("enabled", False):
        for lang_code in tag_config.get("languages", []):
            lang_tags = {tag for post in posts if post.get("language") == lang_code for tag in post.get("tags") or []}
            tag_config.setdefault("items", {})[lang_code] = sorted(lang_tags)
    return config


def add_tag_pages_to_config(posts: List[Doc], config: ConfigType) -> ConfigType:
    """Generate tag pages dynamically from collected tags and add them to config."""
    tag_config = config.get("content", {}).get("pages", {}).get("tags", {})
    if not tag_config.get("enabled", False):
        return config

    languages = config.get("content", {}).get("languages", {})
    for lang_code, tags in tag_config.get("items", {}).items():
        lang_config = languages.get(lang_code)
        if not isinstance(lang_config, dict):
            continue
        archive = lang_config.get("archive", {})

        for tag in tags:
            # Dated by the newest post with the tag (posts are sorted newest first)
            newest = next(p for p in posts if p.get("language") == lang_code and tag in (p.get("tags") or []))
            tag_page = {
                "name": f"tag-{tag}-{lang_code}",
                "title": f"{archive.get('tag_title', 'Tag')}: {tag}",
                "description": archive.get("tag_description", "Posts tagged with '{tag}'").replace("{tag}", tag),
                "template": tag_config.get("template", "tag.html"),
                "url": f"{lang_config.get('tag_url_prefix', 'tag')}/{tag}",
                "language": lang_code,
                "tag": tag,
                "date": newest["date"].strftime("%Y-%m-%d %H:%M:%S %z"),
                "content_type": "pages",
                "html_content": "<!-- Tag page content will be generated by template -->",
            }
            add_url_to_doc(tag_page, config)
            config["content"]["pages"]["items"][tag_page["name"]] = tag_page
    return config


def validate_content_urls(config: ConfigType) -> None:
    """Validate content configuration for missing and duplicate URLs."""
    all_urls: Set[str] = set()
    url_errors: List[str] = []

    for doc in doc_refs(config).values():
        url = doc.get("url")
        content_type = doc.get("content_type", "??")
        title = doc.get("title", doc.get("name", "??"))

        if not url:
            url_errors.append(f"{content_type.title()} '{title}' missing URL")
        elif url in all_urls:
            url_errors.append(f"Duplicate URL '{url}' found for {content_type} '{title}'")
            logger.debug(f"Duplicate URL found: {url} for {content_type} '{title}'")
        else:
            all_urls.add(url)
            logger.debug(f"Added URL: {url} for {content_type} '{title}'")

    if url_errors:
        raise ValueError("Content configuration validation failed:\n" + "".join(f"  {e}\n" for e in url_errors))


# =============================================================================
# 4. GENERATE SITE
# =============================================================================


def generate_site(config: ConfigType, pool: Optional[ProcessPoolExecutor]) -> None:
    """Main build function that orchestrates the entire build process."""
    docs = doc_refs(config)
    posts = [ref for ref in docs if ref[0] == "posts"]
    with ThreadPoolExecutor(max_workers=8) as io:
        # The disk work runs while the post content renders
        resetting = io.submit(reset_output_directory, config, io)
        build_css_html_files(config)
        # Post content first, so the pages that show posts (the feed) get them rendered
        jobs = [(ref, (render_content,)) for ref in posts]
        contents = parallelize(pool, render_stages, jobs, pickle.dumps(config), description="documents")
        for ref, html_content in zip(posts, contents):
            docs[ref]["html_content"] = html_content
        resetting.result()
        jobs = [(ref, (render_page,) if ref[0] == "posts" else (render_content, render_page)) for ref in docs]
        parallelize(pool, render_stages, jobs, pickle.dumps(config), description="documents")
    logger.debug(f"Rendered {len(docs)} docs")
    create_empty_index_files(config)


def doc_refs(config: ConfigType) -> Dict[Tuple[str, str], Doc]:
    """Every doc to build by (content type, name): posts first, then pages."""
    content = config["content"]
    return {(t, name): doc for t in ("posts", "pages") for name, doc in content[t].get("items", {}).items()}


def reset_output_directory(config: ConfigType, io: ThreadPoolExecutor) -> None:
    """Clean the output directory down to what this build writes anyway, and copy in the new and changed assets."""
    output_dir = Path(config["env"]["output_dir"])
    asset_dirs = [output_dir / "assets"]
    sources: Dict[str, Path] = {}  # by destination
    for asset_type, asset_list in config["content"]["assets"].items():
        if isinstance(asset_list, list) and asset_list:
            asset_dirs.append(output_dir / "assets" / asset_type)
            for src_path in filter(Path.exists, map(Path, asset_list)):
                # favicon.ico goes to the site root
                is_root_favicon = asset_type == "favicon" and src_path.name == "favicon.ico"
                sources[str((output_dir if is_root_favicon else asset_dirs[-1]) / src_path.name)] = src_path
    doc_files = {str(doc_output_file(doc, config)) for doc in doc_refs(config).values()}

    def is_copy(src: Path, dst: str) -> bool:  # copy2 keeps the modification time
        src_stat, dst_stat = src.stat(), os.stat(dst)
        return (src_stat.st_size, src_stat.st_mtime_ns) == (dst_stat.st_size, dst_stat.st_mtime_ns)

    # Creating a file costs more than overwriting it, so the files to render stay, as do the up-to-date assets
    for root, dirs, files in os.walk(output_dir, topdown=False):
        for path in (str(Path(root) / name) for name in files):
            if path in sources and is_copy(sources[path], path):
                del sources[path]
            elif path not in doc_files:
                os.unlink(path)
        for path in (Path(root) / name for name in dirs):
            if not os.listdir(path):
                path.rmdir()

    for asset_dir in asset_dirs:
        asset_dir.mkdir(parents=True, exist_ok=True)
    if sources:
        parallelize(io, lambda dst: shutil.copy2(sources[dst], dst), list(sources), description="assets")
    logger.debug(f"Cleaned {output_dir} and copied {len(sources)} assets")


def build_css_html_files(config: ConfigType) -> None:
    """Build CSS files into HTML partials."""
    partials_dir = Path(config["build"]["settings"]["site"]["dir"]) / "templates" / "partials" / "auto-generated"
    partials_dir.mkdir(parents=True, exist_ok=True)

    css_files = config["content"]["assets"].get("css", [])
    for source_path in map(Path, css_files):
        # Minify CSS
        css_content = re.sub(r"/\*.*?\*/", "", source_path.read_text(encoding="utf-8"), flags=re.DOTALL)
        css_content = re.sub(r"\s+", " ", css_content)
        css_content = re.sub(r"\s*([{:;},])\s*", r"\1", css_content).strip()
        with open(partials_dir / f"_{source_path.stem}_css.html", "w", encoding="utf-8", newline="\n") as f:
            f.write(f"<style>{css_content}</style>")

    if css_files:
        logger.debug(f"Generated {len(css_files)} CSS partials")
    else:
        logger.info("No CSS files to process")


def build_templates(config: ConfigType) -> Environment:
    """Set up Jinja2 template environment with proper search paths."""
    theme_dir = Path(config["build"]["settings"]["theme"]["dir"])
    site_dir = Path(config["build"]["settings"]["site"]["dir"])
    template_dirs = [
        # Site templates first (override)
        str(site_dir / "content" / "pages"),
        str(site_dir / "templates"),
        str(site_dir / "templates" / "partials"),
        str(site_dir / "templates" / "partials" / "auto-generated"),
        # then theme templates
        str(theme_dir),
        str(theme_dir / "templates"),
        str(theme_dir / "templates" / "partials"),
        str(theme_dir / "content" / "pages"),
    ]

    env = Environment(
        loader=FileSystemLoader(template_dirs),
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=True,
        auto_reload=False,  # templates do not change during a build
    )
    env.globals.update(
        {
            "env": config.get("env", {}),
            "site": config.get("content", {}).get("site", {}),
            "pages": config["content"]["pages"]["items"],
            "tags": config["content"]["pages"]["tags"]["items"],
            "posts": list(config["content"]["posts"]["items"].values()),
            "latest_post": config["content"].get("latest_post"),
            "latest_posts": config["content"].get("latest_posts", {}),
            "languages": config.get("content", {}).get("languages", {}),
            "config": config,
            "now": datetime.now(),
        }
    )

    def format_date(value, format_str="%Y-%m-%d"):
        return value.strftime(format_str) if hasattr(value, "strftime") else str(value)

    def sort_by(items: List[Doc], attr: str) -> List[Doc]:
        """Sort dicts by *attr*; raise ValueError naming the post if any item lacks *attr*."""
        for item in items or []:
            if not isinstance(item, dict):
                raise TypeError(f"sort_by: expected dict items, got {type(item)!r}")
            if attr not in item:
                where = item.get("file_path") or item.get("url") or item.get("name")
                post_path = "?"
                if where:
                    try:
                        post_path = str(Path(where).resolve())
                    except OSError:
                        post_path = str(Path(where))
                raise ValueError(f"|sort_by: missing YAML key {attr!r} in post {item.get('title', '')!r} → {post_path}")

        def key_fn(item: Doc) -> Any:
            v = item[attr]
            if isinstance(v, bool):
                return str(v)
            if isinstance(v, int):
                return v
            try:
                return int(v)
            except (TypeError, ValueError):
                return str(v) if v is not None else ""

        return sorted(items or [], key=key_fn)

    env.filters["format_date"] = format_date
    env.filters["sort_by"] = sort_by
    return env


@functools.lru_cache(maxsize=1)
def load_build_state(config_blob: bytes) -> Tuple[ConfigType, Environment]:
    """The config and its template environment, loaded once per process for each render stage."""
    config = pickle.loads(config_blob)
    return config, build_templates(config)


def render_stages(job: Tuple[Tuple[str, str], Tuple[Callable, ...]], config_blob: bytes) -> Any:
    """Run a job's render stages for the doc it names (in a worker process on big sites)."""
    (content_type, name), stages = job
    config, template_env = load_build_state(config_blob)
    return render_doc(config["content"][content_type]["items"][name], template_env, config, stages)


def render_string(template_env: Environment, source: str, **context: Any) -> str:
    """Render source as a Jinja template; plain text skips the (slow) compile."""
    if not any(marker in source for marker in JINJA_MARKERS):
        # What Jinja returns for plain text (keep_trailing_newline=False)
        return source[:-1] if source.endswith("\n") else source
    return template_env.from_string(source).render(**context)


def render_content(doc: Doc, template_env: Environment, config: ConfigType) -> str:
    """Render the doc's content as a template, and return it."""
    # Twice: content may output template syntax that needs a second pass
    for _ in range(2):
        doc["html_content"] = render_string(template_env, doc["html_content"], doc=doc, config=config)
    return doc["html_content"]


def render_page(doc: Doc, template_env: Environment, config: ConfigType) -> None:
    """Render the doc's rendered content through its page template, validate and save it."""
    template_name = doc.get("template_page") or doc.get("template")
    if not template_name:
        raise ValueError(f"No template for: '{doc.get('name', '??')}'")
    if not Path(template_name).suffix:
        template_name = f"{template_name}.html"

    if doc.get("template_page"):
        rendered_html = render_string(template_env, doc["html_content"], doc=doc, config=config)
    else:
        rendered_html = template_env.get_template(template_name).render(doc=doc, config=config)

    formatted_html = rendered_html.strip()
    validate_doc(formatted_html, doc.get("name", "??"))
    save_doc(formatted_html, doc, config)


def render_doc(doc: Doc, template_env: Environment, config: ConfigType, stages=(render_content, render_page)) -> Any:
    """Run render stages for a document (all of them builds it) and return the last one's result."""
    try:
        for stage in stages:
            result = stage(doc, template_env, config)
        return result
    except Exception as e:
        raise RuntimeError(f"Failed to render *{doc.get('name', '??')}*: {e}") from e


def doc_output_file(doc: Doc, config: ConfigType) -> Path:
    """Where a rendered document goes: its target_file (feed.xml, sitemap.xml) or <url>/index.html."""
    output_dir = Path(config["env"]["output_dir"])
    if doc.get("target_file"):
        return output_dir / doc["target_file"]
    doc_slug = doc.get("url", "").strip("/")
    if not doc_slug:
        raise ValueError(f"Document '{doc.get('title', '??')}' missing URL")
    return output_dir / doc_slug / "index.html"


def save_doc(rendered_html: str, doc: Doc, config: ConfigType) -> None:
    """Save a rendered document to the filesystem."""
    output_file = doc_output_file(doc, config)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8", newline="\n") as f:
        f.write(rendered_html)


def validate_doc(content: str, doc_name: str) -> None:
    """Fail on a document without any HTML element (robots, feed and sitemap are not HTML)."""
    if any(item in doc_name for item in ["robots", "feed", "sitemap"]):
        return
    if not re.search(r"<[a-zA-Z]", content):
        raise ValueError(f"Document validation failed for '{doc_name}': Empty or invalid structure")


def create_empty_index_files(config: ConfigType) -> None:
    """Ensure each directory in output has an empty index.html to prevent directory listing."""
    output_dir = Path(config["env"]["output_dir"])
    for root, _, files in os.walk(output_dir):
        # The root directory is skipped (it should have content)
        if "index.html" in files or Path(root) == output_dir:
            continue
        try:
            (Path(root) / "index.html").write_text("", encoding="utf-8")
            logger.debug(f"Created empty index.html in {Path(root).relative_to(output_dir)}")
        except Exception as e:
            logger.warning(f"Failed to create {Path(root) / 'index.html'}: {e}")


# =============================================================================
# 5. VALIDATE
# =============================================================================


def validate_site(config: ConfigType, pool: Optional[ProcessPoolExecutor]) -> None:
    """Validate the entire site - optional complete validation."""
    if not config.get("build", {}).get("settings", {}).get("validate_site", False):
        logger.info("Site validation disabled")
        return

    output_dir = config["env"]["output_dir"]
    valid_urls, html_files = scan_valid_urls_from_output(output_dir)
    if not html_files:
        logger.info("No HTML files to validate")
        return

    # Without worker processes, threads: opening a just written file is slow, and that wait runs in parallel
    with ThreadPoolExecutor(max_workers=8) as threads:
        results = parallelize(
            pool or threads, find_broken_links, html_files, output_dir, config["env"]["url"], valid_urls,
            description="HTML files",
        )
    all_broken_links = [link for broken_links in results for link in broken_links]
    if all_broken_links:
        raise ValueError("Invalid internal URLs found:\n" + "\n".join(all_broken_links))


def find_broken_links(html_file: Path, output_dir: str, base_url: str, valid_urls: Set[str]) -> List[str]:
    """Validate a single HTML file and return any broken links found."""
    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning  # slow to import, and only validation needs it

    try:
        doc_name = str(html_file.relative_to(output_dir)).replace("\\", "-").replace(".html", "")
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "html.parser")
        return [
            f"'{url}' in '{doc_name}' page"
            for url in extract_all_urls(soup)
            if not is_valid_url(url, valid_urls, base_url)
        ]
    except Exception as e:
        logger.warning(f"Failed to validate links in {html_file}: {e}")
        return []


def scan_valid_urls_from_output(output_dir: str) -> Tuple[Set[str], List[Path]]:
    """Scan actual output directory to find all valid URLs and HTML files."""
    output_path = Path(output_dir)
    valid_urls: Set[str] = set()
    html_files: List[Path] = []

    if not output_path.exists():
        logger.warning(f"Output directory does not exist: {output_path}")
        return valid_urls, html_files

    for root, _, files in os.walk(output_path):
        root_path = Path(root)
        is_root = root_path == output_path
        if not is_root:
            valid_urls.add(str(root_path.relative_to(output_path)).replace("\\", "/"))

        for file in files:
            if file != "index.html":
                valid_urls.add(str((root_path / file).relative_to(output_path)).replace("\\", "/"))
            elif is_root:
                valid_urls.add("")  # an index.html is its directory's URL
            if file.endswith(".html"):
                html_files.append(root_path / file)

    # Add server-created URLs that don't exist as physical directories
    valid_urls.add("pub")

    logger.debug(f"Scanned {len(valid_urls)} URLs and {len(html_files)} HTML files output dir")
    return valid_urls, html_files


def extract_all_urls(soup: Any) -> Set[str]:
    """Extract all URLs from a parsed document: href, src, meta content and JSON-LD."""
    urls = {element[attr] for attr in ("href", "src") for element in soup.find_all(attrs={attr: True})}
    for meta in soup.find_all("meta", attrs={"content": True}):
        if meta.get("content", "").startswith("http"):
            urls.add(meta["content"])
    for script in soup.find_all("script", type="application/ld+json"):
        if script.string:
            urls.update(extract_urls_from_json_simple(script.string))
    return urls


def is_valid_url(url: str, valid_urls: Set[str], base_url: str) -> bool:
    """Fast validation: is this URL valid?"""
    # Skip external URLs, mailto, tel, javascript, anchors
    if not url.startswith(base_url) and not url.startswith(("/", "./", "../")):
        return True

    # Double slashes are malformed (after the protocol, for absolute URLs)
    path_part = url[url.find("://") + 3 :] if url.startswith(("http://", "https://")) else url
    if "//" in path_part:
        logger.debug(f"Invalid URL detected (double slashes): {url}")
        return False

    # Normalize path
    if url.startswith(base_url):
        path = url.replace(base_url, "").strip("/")
    else:
        path = url.lstrip("/").replace("../", "").replace("./", "")

    # Remove query parameters and anchors, then trailing slashes
    path = path.split("?")[0].split("#")[0].rstrip("/")
    if not path:
        return True  # anchors only

    if path == "index.html":
        path = ""  # root URL
    elif path.endswith("/index.html"):
        path = path[:-10]  # Remove "/index.html"

    # HTML may use percent-encoding (e.g. ä -> %C3%A4); disk paths in valid_urls are Unicode.
    path = unquote(path)
    return path in valid_urls or f"{path}/" in valid_urls


# =============================================================================
# BUILD SINGLE FILE
# =============================================================================


def build_single_file(config: ConfigType, file_path: str) -> None:
    """Build a single file in fast mode, skipping dependencies."""
    logger.warning("Building a single doc.")
    logger.warning("Remember to do a full build before publish to refresh dependencies")

    file_path_obj = Path(file_path)
    content_type = next((t for t in ("posts", "pages") if t in str(file_path_obj)), None)
    if not content_type:
        raise ValueError(f"Cannot determine content type for file: {file_path}")

    item = {"file_path": str(file_path_obj.absolute()), "content_type": content_type, "name": file_path_obj.stem}
    docs = process_single_doc(item, config)
    if not docs:
        raise ValueError(f"Failed to process file: {file_path}")

    template_env = build_templates(config)
    for doc in docs:
        render_doc(doc, template_env, config)
        logger.info(f"Built single file: {doc.get('name', '??')}")


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def merge_config_dicts(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Deep merge source dict into target dict, preserving existing keys."""
    for key, value in source.items():
        if isinstance(target.get(key), dict) and isinstance(value, dict):
            merge_config_dicts(target[key], value)
        else:
            target[key] = value


def time_phase(phase_name: str, func: Any, *args: Any, **kwargs: Any) -> Any:
    start_time = time.time()
    result = func(*args, **kwargs)
    logger.info(f"{phase_name}: {time.time() - start_time:.2f}s")
    return result


def parallelize(pool: Any, func: Callable, items: List[Any], *args: Any, description: str = "items") -> List[Any]:
    """Run func(item, *args) for every item (on the pool's workers, given a pool); results in item order."""
    if not items:
        logger.info(f"No {description} to parallelize")
        return []
    shared = list(map(repeat, args))
    try:
        if pool is None:
            return list(map(func, items, *shared))
        # One chunk per worker, so the shared args (the config) cross to each worker once. A chunk is every
        # n-th item: neighbours cost alike (heavy pages sit together), which would leave one worker with the bulk.
        n = pool._max_workers
        order = [i for first in range(n) for i in range(first, len(items), n)]
        results = pool.map(func, [items[i] for i in order], *shared, chunksize=-(-len(items) // n))
        return [result for _, result in sorted(zip(order, results), key=lambda pair: pair[0])]
    except Exception as e:
        raise RuntimeError(f"Failed to parallelize {description}: {e}") from e


def save_dict(state: str, data: Dict[str, Any], config: ConfigType) -> None:
    """Save data dict to setup.json in site config folder"""
    output_path = Path(config["build"]["settings"]["site"]["dir"]) / "config" / "setup.json"
    try:
        with open(output_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"config_state": state, **data}, f, indent=2, default=str, ensure_ascii=False)
    except Exception as e:
        raise ValueError(f"Failed to save configuration: {e}")


def trim_slashes_from_config(config: ConfigType) -> ConfigType:
    """Recursively normalize all strings in the config by trimming slashes."""

    def trim_slashes(data: Any) -> Any:
        if isinstance(data, dict):
            for k, v in data.items():
                data[k] = trim_slashes(v)
        elif isinstance(data, list):
            for i, v in enumerate(data):
                data[i] = trim_slashes(v)
        elif isinstance(data, str):
            return data.strip("/")
        return data

    return trim_slashes(config)  # modifies config in-place


def extract_urls_from_json_simple(json_string: str) -> Set[str]:
    """Extract the top-level URLs (url, href, @id) from JSON-LD data."""
    try:
        data = json.loads(json_string)
    except (json.JSONDecodeError, TypeError):
        return set()
    return {
        value
        for item in (data if isinstance(data, list) else [data])
        if isinstance(item, dict)
        for key, value in item.items()
        if key in ("url", "href", "@id") and isinstance(value, str)
    }


def main() -> None:
    total_start_time = time.time()
    try:
        config = time_phase("Phase 1: Load initial config", load_initial_config)

        single_file_path = config["env"].get("SINGLE_FILE_PATH")
        if single_file_path:
            time_phase("Single file build", build_single_file, config, single_file_path)
        else:
            config = time_phase("Phase 2: Discover content", add_discovered_to_config, config)
            # Markdown and Jinja are CPU work: doing it in parallel takes processes
            workers = min(config.get("build", {}).get("envs", {}).get("max_workers", 8), os.cpu_count() or 1)
            root_logger = logging.getLogger()
            use_workers = len(doc_refs(config)) >= PARALLEL_MIN_DOCS
            pool_args = {"initializer": root_logger.setLevel, "initargs": (root_logger.level,)}
            with ProcessPoolExecutor(workers, **pool_args) if use_workers else nullcontext() as pool:
                config = time_phase("Phase 3: Process content", add_processed_content_to_config, config, pool)
                time_phase("Phase 4: Generate site", generate_site, config, pool)
                time_phase("Phase 5: Validate site", validate_site, config, pool)

        output_dir = Path(config["env"]["output_dir"])
        logger.info(f"Done in: {time.time() - total_start_time:.2f}s (Output: {output_dir})")

    except (ValueError, RuntimeError) as e:
        logger.error(f"Build failed: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
