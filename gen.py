import argparse
import sys
import logging
import yaml
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import shutil
import re
import time
import json
import warnings
from datetime import datetime
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from jinja2 import Environment, FileSystemLoader
import markdown
from typing import Dict, List, Any, Tuple, Set
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

YAML_PATTERNS = ["*.yml", "*.yaml"]
REQUIRED_ENV_VARS = ["SITES_FOLDER", "SITE_NAME"]

ConfigType = Dict[str, Any]

# =============================================================================
# 1. CONFIGURATION LOADING
# =============================================================================


def load_initial_config() -> ConfigType:
    """Load initial configuration with args, env, and YAML content."""
    config = init_config()
    config = add_args_and_env_to_config(config)
    config = add_yml_content_to_config(config)
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
    parser.add_argument(
        "--site-name", "-s", help="Site name (overrides SITE_NAME env var)"
    )
    parser.add_argument(
        "--sites-folder",
        "-sf",
        help="Sites folder path (overrides SITES_FOLDER env var)",
    )
    parser.add_argument(
        "--single-file-path",
        "-sfp",
        help="Build only a single file (fast mode, skips dependencies). Use full file path.",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load environment variables from .env file
    load_dotenv()

    # Copy environment variables to config
    for key, value in os.environ.items():
        if key.startswith(("SITES_FOLDER", "SITE_NAME", "BUILD_ENV")):
            config["env"][key] = value

    # Command line args take precedence over env vars
    if args.site_name:
        config["env"]["SITE_NAME"] = args.site_name
    if args.sites_folder:
        config["env"]["SITES_FOLDER"] = args.sites_folder
    if args.single_file_path:
        config["env"]["SINGLE_FILE_PATH"] = args.single_file_path
    if args.env is not None:
        config["env"]["BUILD_ENV"] = args.env
    elif "BUILD_ENV" not in config["env"]:
        config["env"]["BUILD_ENV"] = "dev"

    site_name = config["env"]["SITE_NAME"]
    build_env = config["env"]["BUILD_ENV"]
    logger.info(f"Start: {site_name} ({build_env})")

    missing_vars = [var for var in REQUIRED_ENV_VARS if var not in config["env"]]
    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}"
        )

    return config


def add_yml_content_to_config(config: ConfigType) -> ConfigType:
    """Load site configuration from YAML files based on folder structure."""
    site_name = config["env"]["SITE_NAME"]
    sites_folder = config["env"]["SITES_FOLDER"]

    site_dir = Path(sites_folder) / site_name.lower()
    config_dir = site_dir / "config"

    if not config_dir.exists():
        raise ValueError(f"Config directory not found: {config_dir}")

    yaml_files = []
    for pattern in YAML_PATTERNS:
        yaml_files.extend(list(config_dir.rglob(pattern)))

    for yaml_file in yaml_files:
        try:
            # Convert file path to config key
            relative_path = yaml_file.relative_to(config_dir)
            config_key = (
                str(relative_path.with_suffix("")).replace("/", ".").replace("\\", ".")
            )

            # Load YAML content
            with open(yaml_file, "r", encoding="utf-8") as f:
                yaml_content = yaml.safe_load(f)

            if yaml_content:
                # Create nested structure and assign content
                keys = config_key.split(".")
                current = config
                for key in keys[:-1]:
                    if key not in current:
                        current[key] = {}
                    current = current[key]

                # Handle granular YAML structure
                target_key = keys[-1]
                if isinstance(yaml_content, dict) and len(yaml_content) == 1:
                    # Check if the single key matches the target key (e.g., "pages" in content/pages.yml)
                    single_key = list(yaml_content.keys())[0]
                    if single_key == target_key:
                        yaml_content = yaml_content[
                            single_key
                        ]  # Extract the actual content

                # Merge or assign content
                if (
                    target_key in current
                    and isinstance(current[target_key], dict)
                    and isinstance(yaml_content, dict)
                ):
                    merge_config_dicts(current[target_key], yaml_content)
                else:
                    current[target_key] = yaml_content

                logger.debug(f"Loaded config: {config_key}")

        except Exception as e:
            raise ValueError(f"Failed to load config file {yaml_file}: {e}")

    # Get theme from content configuration or use default
    theme_name = config.get("content", {}).get("site", {}).get("theme", "default")
    config["build"]["settings"]["theme"]["dir"] = str(Path(f"themes/{theme_name}/"))
    config["build"]["settings"]["site"]["dir"] = str(site_dir)
    build_env = config["env"]["BUILD_ENV"]

    if "build" in config and "envs" in config["build"]:
        env_config = config["build"]["envs"].get(build_env, {})
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

    discovery_config = {
        "pages": [
            site_dir / "content" / "pages",
            theme_dir / "content" / "pages",
        ],
        "posts": [
            year_dir
            for year_dir in (site_dir / "content" / "posts").iterdir()
            if year_dir.is_dir() and year_dir.name.isdigit()
        ],
        "assets": [
            site_dir / "assets",
            theme_dir / "assets",
        ],
    }

    all_paths = [
        (path, content_type)
        for content_type, paths in discovery_config.items()
        for path in paths
        if path.exists()
    ]

    parallelize(all_paths, scan_single_dir, description="directories", config=config)

    return config


def scan_single_dir(path_info: Tuple[Path, str], config: ConfigType) -> None:
    """Discover content from a single directory and insert directly into the config."""
    path, content_type = path_info
    path = Path(path)

    if not path.exists():
        return

    if content_type in ("posts", "pages"):
        for root, _, files in os.walk(path):
            # Images inside content dirs
            img_dir = Path(root) / "images"
            if img_dir.exists():
                config["content"]["assets"].setdefault("images", []).extend(
                    str(Path(img_root) / f)
                    for img_root, _, img_files in os.walk(img_dir)
                    for f in img_files
                )

            # Content files
            for filename in files:
                if filename.endswith((".md", ".html", ".xml", ".txt")):
                    file_path = Path(root) / filename
                    item_name = file_path.stem

                    # Merge with existing item config if it exists
                    if item_name in config["content"][content_type]["items"]:
                        item = config["content"][content_type]["items"][item_name]
                        item["file_path"] = str(file_path.absolute())
                        item["content_type"] = content_type
                        item["name"] = item_name
                    else:
                        # Create new item with file_path, content_type, and name
                        config["content"][content_type]["items"][item_name] = {
                            "file_path": str(file_path.absolute()),
                            "content_type": content_type,
                            "name": item_name,
                        }

    elif content_type == "assets":
        for subdir in path.iterdir():
            if subdir.is_dir():
                bucket = config["content"]["assets"].setdefault(subdir.name, [])
                for root, _, files in os.walk(subdir):
                    bucket.extend(str(Path(root) / f) for f in files)


# =============================================================================
# 3. ADD PROCESSED CONTENT TO CONFIG
# =============================================================================


def add_processed_content_to_config(config: ConfigType) -> ConfigType:
    """Process all discovered content and add it to the global config."""
    posts, config = process_docs(config)
    config = add_latest_posts_to_config(posts, config)
    config = add_tags_to_config(posts, config)
    config = add_tag_pages_to_config(posts, config)
    config = trim_slashes_from_config(config)
    validate_content_urls(config)
    save_dict("after processing", config, config)
    return config


def process_docs(config: ConfigType) -> Tuple[List[Dict[str, Any]], ConfigType]:
    """Convert and enrich all discovered content, updating config directly."""
    posts_items = list(config["content"]["posts"]["items"].values())
    pages_items = list(config["content"]["pages"]["items"].values())
    items_to_process = posts_items + pages_items

    results = parallelize(
        items_to_process, process_single_doc, description="content files", config=config
    )

    posts, pages, errors = [], [], []
    for docs in results:  # each element is a list of docs
        for doc in docs:
            if "error" in doc:
                errors.append(f"{doc['file_path']}: {doc['error']}")
            elif doc["content_type"] == "posts":
                posts.append(doc)
            else:  # assume "pages"
                pages.append(doc)

    if errors:
        raise ValueError("Process doc failed:\n" + "\n".join(f"  {e}" for e in errors))

    posts.sort(key=lambda p: p.get("date"), reverse=True)
    pages.sort(key=lambda p: p.get("nav_title", "zzz"))

    add_navigation_links_to_docs(posts)

    config["content"]["posts"]["items"] = {post["name"]: post for post in posts}
    config["content"]["pages"]["items"] = {page["name"]: page for page in pages}

    return posts, config


def process_single_doc(
    item: Dict[str, Any], config: ConfigType
) -> List[Dict[str, Any]]:
    """Parse front matter and expand multi-language content into multiple documents."""
    file_path = Path(item["file_path"])
    content_type = item["content_type"]

    # Get the right defaults for this content type
    defaults = config["content"][content_type]["defaults"]

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        front_matter, body = parse_front_matter_and_content(content)

        doc = {
            **defaults,  # Apply defaults first
            **item,  # Item config overrides defaults
            **front_matter,  # Front matter overrides everything
            "content": body,
        }

        # Handle multi-language pages
        if isinstance(doc.get("languages"), dict):
            docs = []
            for lang, overrides in doc["languages"].items():
                lang_doc = doc.copy()
                lang_doc.update(overrides)
                lang_doc["language"] = lang
                lang_doc["name"] = f"{doc['name']}-{lang}"

                # Enrich this language variant
                enriched_doc = enrich_doc(lang_doc, body, file_path, config)
                docs.append(enriched_doc)
            return docs

        # Single language page
        enriched_doc = enrich_doc(doc, body, file_path, config)
        return [enriched_doc]

    except Exception as e:
        logger.warning(f"Failed to process {item.get('file_path', '??')}: {e}")
        return []


def parse_front_matter_and_content(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse front matter from content string and return (front_matter, body)."""
    # Check if file has front matter
    if not content.startswith("---"):
        return {}, content

    # Split content into front matter and body
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    front_matter_text = parts[1]
    body = parts[2]

    # Parse front matter
    try:
        front_matter = yaml.safe_load(front_matter_text)
        if front_matter is None:
            front_matter = {}
        return front_matter, body
    except yaml.YAMLError as e:
        logger.warning(f"Could not parse front matter: {e}")
        return {}, content


def enrich_doc(
    doc: Dict[str, Any], body: str, file_path: Path, config: ConfigType
) -> Dict[str, Any]:
    """Enrich a document with HTML, URL, dates, and read time."""
    doc = add_html_to_doc(body, doc, config)
    doc = add_url_to_doc(doc, config)
    doc = add_dates_to_doc(doc, file_path)
    doc = add_read_time_to_doc(doc, config)
    return doc


def add_html_to_doc(
    content: str, doc: Dict[str, Any], config: ConfigType = None
) -> Dict[str, Any]:
    """Convert markdown content to HTML, or keep as-is for non-markdown files."""

    # Check if this is a markdown file based on the original file path
    file_path = doc.get("file_path", "")
    if file_path.endswith(".md"):
        # Convert markdown to HTML
        toc_title = "Table of Contents"
        if config:
            language = doc.get("language")
            lang_config = (
                config.get("content", {}).get("languages", {}).get(language, {})
            )
            if "table_of_contents_title" in lang_config:
                toc_title = lang_config["table_of_contents_title"]

        extensions = [
            "markdown.extensions.attr_list",
            "toc",
        ]
        extension_configs = {"toc": {"title": toc_title}}

        html_content = markdown.markdown(
            content, extensions=extensions, extension_configs=extension_configs
        )
    else:
        # Keep non-markdown content as-is (HTML, XML, etc.)
        html_content = content

    doc["html_content"] = html_content
    return doc


def add_url_to_doc(doc: Dict[str, Any], config: ConfigType) -> Dict[str, Any]:
    """Handle all URL logic: target_file for pages and language URL prefix logic."""

    if "target_file" in doc:
        doc["url"] = doc["target_file"]

    if "language" not in doc:
        raise ValueError(f"Doc missing 'language': {doc.get('name', '')}")

    base_url = doc["url"]
    language = doc["language"]
    languages_config = config.get("content", {}).get("languages", {})
    lang_config = languages_config.get(language, {})

    if not lang_config.get("skip_language_path_in_url", False):
        # Add language prefix
        final_url = f"{language}/{base_url}"
        if "target_file" in doc:
            doc["target_file"] = f"{language}/{doc['target_file']}"
    else:
        # Keep URL as-is (no prefix)
        final_url = base_url

    doc["url"] = final_url
    return doc


def add_dates_to_doc(doc: Dict[str, Any], file_path: Path) -> Dict[str, Any]:
    """Process dates and create formatted versions."""

    date_value = doc.get("date")
    if not date_value:
        raise ValueError(f"Missing required 'date' field in {file_path}")

    # Handle both string and datetime objects
    if isinstance(date_value, datetime):
        date_obj = date_value
    else:
        try:
            # Parse standardized date format: YYYY-MM-DD HH:MM:SS +0000
            date_obj = datetime.strptime(date_value, "%Y-%m-%d %H:%M:%S %z")
        except Exception as e:
            raise ValueError(
                f"Failed to parse date '{date_value}' for {file_path}: {e}"
            )

    doc["date"] = date_obj
    doc["date_html"] = date_obj.strftime("%d.%m.%Y")
    doc["date_iso"] = date_obj.isoformat()
    doc["date_xml_feed"] = date_obj.strftime("%a, %d %b %Y %H:%M:%S %z")

    return doc


def add_read_time_to_doc(doc: Dict[str, Any], config: ConfigType) -> Dict[str, Any]:
    """Add read time to document."""

    content = doc.get("content", "")
    words = content.split()
    words_per_minute = (
        config.get("build", {})
        .get("settings", {})
        .get("read_time_words_per_minute", 200)
    )
    read_time = max(1, len(words) // words_per_minute)
    doc["read_time"] = read_time

    return doc


def add_navigation_links_to_docs(posts: List[Dict[str, Any]]) -> None:
    """Add navigation metadata for posts (next/previous post links) within the same language."""
    # Group posts by language
    posts_by_language = {}
    for post in posts:
        language = post.get("language", "en")
        if language not in posts_by_language:
            posts_by_language[language] = []
        posts_by_language[language].append(post)

    # Add navigation links within each language group
    for language, lang_posts in posts_by_language.items():
        for i, post in enumerate(lang_posts):
            if i > 0:
                post["next_post_url"] = lang_posts[i - 1].get("url", "")
                post["next_post_title"] = lang_posts[i - 1].get("title", "")
            else:
                post["next_post_url"] = ""
                post["next_post_title"] = ""

            if i < len(lang_posts) - 1:
                post["prev_post_url"] = lang_posts[i + 1].get("url", "")
                post["prev_post_title"] = lang_posts[i + 1].get("title", "")
            else:
                post["prev_post_url"] = ""
                post["prev_post_title"] = ""


def add_latest_posts_to_config(
    posts: List[Dict[str, Any]], config: ConfigType
) -> ConfigType:
    """Store latest posts by language in config."""
    if posts:
        latest_posts_by_language = {}
        for post in posts:
            language = post.get("language")
            if language and language not in latest_posts_by_language:
                latest_posts_by_language[language] = post

        config["content"]["latest_posts"] = latest_posts_by_language

        for language, latest_post in latest_posts_by_language.items():
            logger.debug(
                f"Latest post ({language}): {latest_post.get('title', '??')} "
                f"({latest_post.get('date_html', 'No date')})"
            )

    return config


def add_tags_to_config(posts: List[Dict[str, Any]], config: ConfigType) -> ConfigType:
    """Collect unique tags from posts and store them in config."""
    # Check if tag pages are enabled
    tag_config = config.get("content", {}).get("pages", {}).get("tags", {})
    if not tag_config.get("enabled", False):
        return config

    # Get enabled languages for tag pages
    enabled_languages = tag_config.get("languages", [])
    if not enabled_languages:
        return config

    # Collect all unique tags from processed posts
    all_tags = set()
    for post in posts:
        if post.get("tags"):
            all_tags.update(post["tags"])

    # Store tags by language
    if "items" not in tag_config:
        tag_config["items"] = {}

    for lang_code in enabled_languages:
        # Get posts for this language
        lang_posts = [post for post in posts if post.get("language") == lang_code]

        # Get tags that have posts in this language
        lang_tags = set()
        for post in lang_posts:
            if post.get("tags"):
                lang_tags.update(post["tags"])

        tag_config["items"][lang_code] = sorted(list(lang_tags))

    return config


def add_tag_pages_to_config(
    posts: List[Dict[str, Any]], config: ConfigType
) -> ConfigType:
    """Generate tag pages dynamically from collected tags and add them to config."""
    tag_config = config.get("content", {}).get("pages", {}).get("tags", {})

    if not tag_config.get("enabled", False):
        return config

    languages = config.get("content", {}).get("languages", {})
    tag_items = tag_config.get("items", {})

    for lang_code, tags in tag_items.items():
        if lang_code not in languages:
            continue

        lang_config = languages[lang_code]
        if not isinstance(lang_config, dict):
            continue

        for tag in tags:
            # Get language-specific tag URL prefix
            tag_prefix = lang_config.get("tag_url_prefix", "tag")

            # Create tag page configuration with language-aware description
            tag_title = lang_config.get("archive", {}).get("tag_title", "Tag")
            description_template = lang_config.get("archive", {}).get(
                "tag_description", "Posts tagged with '{tag}'"
            )
            description = description_template.replace("{tag}", tag)

            tag_page = {
                "name": f"tag-{tag}-{lang_code}",
                "title": f"{tag_title}: {tag}",
                "description": description,
                "template": tag_config.get("template", "tag.html"),
                "url": f"{tag_prefix}/{tag}",
                "language": lang_code,
                "tag": tag,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S %z"),
                "content_type": "pages",
                "html_content": "<!-- Tag page content will be generated by template -->",
            }

            add_url_to_doc(tag_page, config)

            # Add tag page to config
            config["content"]["pages"]["items"][tag_page["name"]] = tag_page

    return config


def validate_content_urls(config: ConfigType) -> None:
    """Validate content configuration for missing and duplicate URLs."""
    all_urls: List[str] = []
    url_errors: List[str] = []

    posts = list(config["content"]["posts"].get("items", {}).values())
    pages = list(config["content"]["pages"].get("items", {}).values())
    all_content = posts + pages

    for result in all_content:
        url = result.get("url")
        content_type = result.get("content_type", "??")
        title = result.get("title", result.get("name", "??"))

        if not url:
            url_errors.append(f"{content_type.title()} '{title}' missing URL")
        elif url in all_urls:
            url_errors.append(
                f"Duplicate URL '{url}' found for {content_type} '{title}'"
            )
            logger.debug(f"Duplicate URL found: {url} for {content_type} '{title}'")
        else:
            all_urls.append(url)
            logger.debug(f"Added URL: {url} for {content_type} '{title}'")

    if url_errors:
        error_message = "Content configuration validation failed:\n"
        for error in url_errors:
            error_message += f"  {error}\n"
        raise ValueError(error_message)


# =============================================================================
# 4. GENERATE SITE
# =============================================================================


def generate_site(config: ConfigType) -> None:
    """Main build function that orchestrates the entire build process."""
    clean_output_directory(config["env"]["output_dir"])
    copy_assets(config)
    build_css_html_files(config)
    template_env = build_templates(config)
    build_docs(config, template_env)
    create_empty_index_files(config)


def clean_output_directory(output_dir: str) -> None:
    """Clean up the build directory efficiently."""
    output_path = Path(output_dir)

    if output_path.exists():
        shutil.rmtree(output_path)
        logger.debug(f"Cleaned build directory: {output_path}")

    output_path.mkdir(parents=True, exist_ok=True)


def copy_assets(config: ConfigType) -> None:
    """Copy all discovered assets to output directory using parallel processing."""
    output_dir = Path(config["env"]["output_dir"])
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    copy_operations = []

    for asset_type, asset_list in config["content"]["assets"].items():
        if isinstance(asset_list, list) and asset_list:
            type_dir = assets_dir / asset_type
            type_dir.mkdir(parents=True, exist_ok=True)

            for asset_path in asset_list:
                src_path = Path(asset_path)
                if src_path.exists():
                    # Special handling for favicon files - copy to root directory
                    if asset_type == "favicon" and src_path.name == "favicon.ico":
                        dst_path = output_dir / src_path.name
                    else:
                        dst_path = type_dir / src_path.name
                    copy_operations.append((src_path, dst_path))

    if copy_operations:

        def copy_single_asset(operation):
            src, dst = operation
            shutil.copy2(src, dst)
            return str(dst)

        results = parallelize(copy_operations, copy_single_asset, description="assets")
        logger.debug(f"Copied {len(results)} assets")
    else:
        logger.info("No assets to copy")


def build_css_html_files(config: ConfigType) -> None:
    """Build CSS files into HTML partials."""

    site_dir = Path(config["build"]["settings"]["site"]["dir"])
    partials_dir = site_dir / "templates" / "partials" / "auto-generated"
    partials_dir.mkdir(parents=True, exist_ok=True)

    css_files = config["content"]["assets"].get("css", [])

    def build_single_css_file(css_path):
        source_path = Path(css_path)

        with open(source_path, "r", encoding="utf-8") as f:
            css_content = f.read()

            # Minify CSS
            css_content = re.sub(r"/\*.*?\*/", "", css_content, flags=re.DOTALL)
            css_content = re.sub(r"\s+", " ", css_content)
            css_content = re.sub(r"\s*([{:;},])\s*", r"\1", css_content)
            css_content = css_content.strip()

            partial_html = f"<style>\n{css_content}\n</style>"
            partial_name = f"_{source_path.stem}_css.html"
            partial_path = partials_dir / partial_name

            with open(partial_path, "w", encoding="utf-8") as f:
                f.write(partial_html)

            return partial_name

    if css_files:
        results = parallelize(css_files, build_single_css_file, description="CSS files")
        logger.debug(f"Generated {len(results)} CSS partials")
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
        if hasattr(value, "strftime"):
            return value.strftime(format_str)
        return str(value)

    env.filters["format_date"] = format_date

    return env


def build_docs(
    config: ConfigType,
    template_env: Environment,
) -> None:
    """Build all documents in parallel."""

    posts_items = list(config["content"]["posts"].get("items", {}).values())
    pages_items = list(config["content"]["pages"].get("items", {}).values())
    items_to_build = posts_items + pages_items

    def build_single_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
        """Render a single document using templates."""
        try:
            # Convert template variables in content
            try:
                content_template = template_env.from_string(doc["html_content"])
                doc["html_content"] = content_template.render(doc=doc, config=config)
            except Exception as e:
                raise ValueError(f"Template rendering error in content: {e}")

            template_name = doc.get("template_page") or doc.get("template")
            if not template_name:
                raise ValueError(f"No template for: '{doc.get('name', '??')}'")

            if not Path(template_name).suffix:
                template_name = f"{template_name}.html"

            if doc.get("template_page"):
                # If has template_page, use it as template
                template = template_env.from_string(doc["html_content"])
            else:
                template = template_env.get_template(template_name)

            rendered_html = template.render(doc=doc, config=config)

            formatted_html = format_doc(rendered_html, doc.get("name", "??"), config)

            validate_doc(formatted_html, doc.get("name", "??"))

            save_doc(formatted_html, doc, config)

            return {"success": True, "name": doc.get("name", "??")}
        except Exception as e:
            raise RuntimeError(
                f"Failed to render document *{doc.get('name', '??')}*: {e}"
            )

    results = parallelize(items_to_build, build_single_doc, description="documents")

    # Count successful renders
    posts = list(config["content"]["posts"].get("items", {}).values())
    pages = list(config["content"]["pages"].get("items", {}).values())

    post_names = [p["name"] for p in posts]
    page_names = [p["name"] for p in pages]
    successful_posts = sum(
        1 for r in results if r.get("success") and r.get("name") in post_names
    )
    successful_pages = sum(
        1 for r in results if r.get("success") and r.get("name") in page_names
    )

    logger.debug(
        f"Rendered {successful_posts}/{len(posts)} posts and {successful_pages}/{len(pages)} pages"
    )


def save_doc(rendered_html: str, doc: Dict[str, Any], config: ConfigType) -> None:
    """Save a rendered document to the filesystem."""
    output_dir = Path(config["env"]["output_dir"])

    # Check if this document has a target_file (like feed.xml, sitemap.xml)
    target_file = doc.get("target_file")

    if target_file:
        # Save directly as the specified file
        output_file = output_dir / target_file
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(rendered_html)
    else:
        # Standard behavior: create folder with index.html
        doc_slug = doc.get("url", "").strip("/")
        if not doc_slug:
            raise ValueError(f"Document '{doc.get('title', '??')}' missing URL")

        doc_output_dir = output_dir / doc_slug
        doc_output_dir.mkdir(parents=True, exist_ok=True)
        output_file = doc_output_dir / "index.html"

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(rendered_html)


def format_doc(html_content: str, doc_name: str, config: ConfigType) -> str:
    """Format HTML content based on configuration settings."""
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

    prettify_html = (
        config.get("build", {}).get("settings", {}).get("prettify_html", False)
    )

    if prettify_html:
        if any(item in doc_name for item in ["robots", "feed", "sitemap"]):
            return html_content  # Skip text files
        else:
            return BeautifulSoup(html_content, "html.parser").prettify()
    else:
        # Minify html
        html_content = re.sub(r"\s+", " ", html_content)  # Multiple spaces to single
        html_content = re.sub(
            r">\s+<", "><", html_content
        )  # Remove spaces between tags
        html_content = re.sub(r"\s+>", ">", html_content)  # Remove spaces before >
        html_content = re.sub(r"<\s+", "<", html_content)  # Remove spaces after <
        return html_content.strip()


def validate_doc(content: str, doc_name: str) -> None:
    """Validate document content structure using BeautifulSoup."""
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

    try:
        if any(item in doc_name for item in ["robots", "feed", "sitemap"]):
            return content
        else:
            soup = BeautifulSoup(content, "html.parser")

        # Validate document structure
        if not soup.find():
            raise ValueError(
                f"Document validation failed for '{doc_name}': Empty or invalid structure"
            )

    except Exception as e:
        raise ValueError(f"Document validation failed for '{doc_name}': {e}")


def create_empty_index_files(config: ConfigType) -> None:
    """Ensure each directory in output has an empty index.html to prevent directory listing."""
    output_dir = Path(config["env"]["output_dir"])

    if not output_dir.exists():
        return

    # Scan all directories in output and add empty index.html if missing
    for root, dirs, files in os.walk(output_dir):
        root_path = Path(root)
        index_file = root_path / "index.html"

        # Skip if index.html already exists
        if index_file.exists():
            continue

        # Skip the root directory (it should have content)
        if root_path == output_dir:
            continue

        try:
            index_file.write_text("", encoding="utf-8")
            logger.debug(
                f"Created empty index.html in {root_path.relative_to(output_dir)}"
            )
        except Exception as e:
            logger.warning(f"Failed to create {index_file}: {e}")


# =============================================================================
# 5. VALIDATE
# =============================================================================


def validate_site(config: ConfigType) -> None:
    """Validate the entire site - optional complete validation."""
    has_validation = (
        config.get("build", {}).get("settings", {}).get("validate_site", False)
    )

    if not has_validation:
        logger.info("Site validation disabled")
        return

    output_dir = config["env"]["output_dir"]
    valid_urls, html_files = scan_valid_urls_from_output(output_dir)

    if not html_files:
        logger.info("No HTML files to validate")
        return

    def validate_single_doc_internal_links(html_file: Path) -> List[str]:
        """Validate a single HTML file and return any broken links found."""
        broken_links = []
        try:
            with open(html_file, "r", encoding="utf-8") as f:
                html_content = f.read()

            # Get relative path for error reporting
            output_dir = Path(config["env"]["output_dir"])
            relative_path = html_file.relative_to(output_dir)
            doc_name = str(relative_path).replace("\\", "-").replace(".html", "")

            # Extract and validate links from final HTML
            warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(html_content, "html.parser")

            # Extract all URLs from final HTML
            all_urls = extract_all_urls(soup)

            for url in all_urls:
                if not is_valid_url(url, valid_urls, config):
                    broken_links.append(f"'{url}' in '{doc_name}' page")

        except Exception as e:
            logger.warning(f"Failed to validate links in {html_file}: {e}")

        return broken_links

    results = parallelize(
        html_files, validate_single_doc_internal_links, description="HTML files"
    )

    all_broken_links = []
    for broken_links in results:
        all_broken_links.extend(broken_links)

    if all_broken_links:
        raise ValueError("Invalid internal URLs found:\n" + "\n".join(all_broken_links))


def scan_valid_urls_from_output(output_dir: str) -> Tuple[Set[str], List[Path]]:
    """Scan actual output directory to find all valid URLs and HTML files."""
    output_path = Path(output_dir)
    valid_urls = set()
    html_files = []

    if not output_path.exists():
        logger.warning(f"Output directory does not exist: {output_path}")
        return valid_urls, html_files

    # Scan all files and directories
    for root, dirs, files in os.walk(output_path):
        root_path = Path(root)
        relative_path = root_path.relative_to(output_path)

        # Convert directory path to URL
        if relative_path != Path("."):
            url_path = str(relative_path).replace("\\", "/")
            valid_urls.add(url_path)

        # Convert files to URLs and collect HTML files
        for file in files:
            file_path = root_path / file
            relative_file_path = file_path.relative_to(output_path)
            url_path = str(relative_file_path).replace("\\", "/")

            # Handle index.html files - they represent directory URLs
            if file == "index.html":
                if relative_path != Path("."):
                    # Directory URL (without index.html)
                    dir_url = str(relative_path).replace("\\", "/")
                    valid_urls.add(dir_url)
                else:
                    # Root URL
                    valid_urls.add("")
                # Add HTML file to our collection
                html_files.append(file_path)
            else:
                # Regular file URL
                valid_urls.add(url_path)
                # Add HTML files to our collection
                if file.endswith(".html"):
                    html_files.append(file_path)

    # Add server-created URLs that don't exist as physical directories
    valid_urls.add("pub")

    logger.debug(
        f"Scanned {len(valid_urls)} URLs and {len(html_files)} HTML files output dir"
    )
    return valid_urls, html_files


def extract_all_urls(soup: BeautifulSoup) -> Set[str]:
    """Extract all URLs in single BeautifulSoup pass."""
    urls = set()

    # Get all href attributes
    for element in soup.find_all(attrs={"href": True}):
        urls.add(element["href"])

    # Get all src attributes
    for element in soup.find_all(attrs={"src": True}):
        urls.add(element["src"])

    # Get meta content URLs
    for meta in soup.find_all("meta", attrs={"content": True}):
        content = meta.get("content", "")
        if content.startswith("http"):
            urls.add(content)

    # Get JSON-LD URLs (simplified)
    for script in soup.find_all("script", type="application/ld+json"):
        if script.string:
            urls.update(extract_urls_from_json_simple(script.string))

    return urls


def is_valid_url(url: str, valid_urls: Set[str], config: ConfigType) -> bool:
    """Fast validation: is this URL valid?"""
    base_url = config["env"]["url"]

    # Skip external URLs, mailto, tel, javascript, anchors
    if not url.startswith(base_url) and not url.startswith(("/", "./", "../")):
        return True

    # Check for malformed URLs (double slashes, etc.) BEFORE normalization
    # Check for double slashes in the path part (after the protocol)
    if url.startswith(("http://", "https://")):
        # For absolute URLs, check if there are double slashes after the protocol
        protocol_end = url.find("://") + 3
        path_part = url[protocol_end:]
        if "//" in path_part:
            logger.debug(f"Invalid URL detected (double slashes in path): {url}")
            return False
    elif "//" in url:
        # For relative URLs, any double slashes are invalid
        logger.debug(f"Invalid URL detected (double slashes): {url}")
        return False

    # Normalize path
    if url.startswith(base_url):
        path = url.replace(base_url, "").strip("/")
    else:
        # Handle relative URLs
        path = url.lstrip("/").replace("../", "").replace("./", "")

    # Remove query parameters and anchors
    path = path.split("?")[0].split("#")[0]

    # Strip trailing slashes after anchor removal
    path = path.rstrip("/")

    # Skip empty paths (anchors only)
    if not path:
        return True

    if path == "index.html":
        path = ""  # root URL
    elif path.endswith("/index.html"):
        path = path[:-10]  # Remove "/index.html"

    # Check if path is valid (with or without trailing slash)
    return path in valid_urls or f"{path}/" in valid_urls


# =============================================================================
# BUILD SINGLE FILE
# =============================================================================


def build_single_file(config: ConfigType, file_path: str) -> None:
    """Build a single file in fast mode, skipping dependencies."""

    logger.warning("Building a single doc.")
    logger.warning("Remember to do a full build before publish to refresh dependencies")

    file_path_obj = Path(file_path)

    # Determine content type based on path
    if "posts" in str(file_path_obj):
        content_type = "posts"
    elif "pages" in str(file_path_obj):
        content_type = "pages"
    else:
        raise ValueError(f"Cannot determine content type for file: {file_path}")

    # Create a minimal item for processing
    item_name = file_path_obj.stem
    item = {
        "file_path": str(file_path_obj.absolute()),
        "content_type": content_type,
        "name": item_name,
    }

    # Process the single document
    docs = process_single_doc(item, config)
    if not docs:
        raise ValueError(f"Failed to process file: {file_path}")

    # Set up templates
    template_env = build_templates(config)

    # Build the single document
    for doc in docs:
        try:
            # Convert template variables in content
            try:
                content_template = template_env.from_string(doc["html_content"])
                doc["html_content"] = content_template.render(doc=doc, config=config)
            except Exception as e:
                raise ValueError(f"Template rendering error in content: {e}")

            template_name = doc.get("template_page") or doc.get("template")
            if not template_name:
                raise ValueError(
                    f"Template not found for: '{doc.get('name', '??')}'"
                )

            if not Path(template_name).suffix:
                template_name = f"{template_name}.html"

            # If template_page is specified, use the processed content as template
            if doc.get("template_page"):
                template = template_env.from_string(doc["html_content"])
            else:
                # Load template from file
                template = template_env.get_template(template_name)

            rendered_html = template.render(doc=doc, config=config)

            formatted_html = format_doc(rendered_html, doc.get("name", "??"), config)

            save_doc(formatted_html, doc, config)

            logger.info(f"Built single file: {doc.get('name', '??')}")

        except Exception as e:
            raise RuntimeError(
                f"Failed to render document *{doc.get('name', '??')}*: {e}"
            )


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def merge_config_dicts(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Deep merge source dict into target dict, preserving existing keys."""
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            # Recursively merge nested dicts
            merge_config_dicts(target[key], value)
        else:
            # Overwrite or add new key
            target[key] = value


def time_phase(phase_name: str, func: Any, *args: Any, **kwargs: Any) -> Any:
    start_time = time.time()
    result = func(*args, **kwargs)
    logger.info(f"{phase_name}: {time.time() - start_time:.2f}s")
    return result


def parallelize(
    items: List[Any],
    processor_func: Any,
    description: str = "items",
    config: ConfigType = None,
) -> List[Any]:
    """Generic parallel processing function."""
    if not items:
        logger.info(f"No {description} to parallelize")
        return []

    max_workers = 8  # Default value
    if config:
        max_workers = config.get("build", {}).get("envs", {}).get("max_workers", 8)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        if config:
            futures = [executor.submit(processor_func, item, config) for item in items]
        else:
            futures = [executor.submit(processor_func, item) for item in items]
        results = []
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as e:
                raise RuntimeError(f"Failed to parallelize {description}: {e}")

    return results


def save_dict(state: str, data: Dict[str, Any], config: ConfigType) -> None:
    """Save data dict to setup.json in site config folder"""
    site_dir = Path(config["build"]["settings"]["site"]["dir"])
    output_path = site_dir / "config" / "setup.json"

    output_data = {"config_state": state}
    output_data.update(data)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, default=str, ensure_ascii=False)
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

    trim_slashes(config)  # modifies config in-place

    return config


def extract_urls_from_json_simple(json_string: str) -> Set[str]:
    """Extract URLs from JSON-LD data - simplified iterative approach."""
    urls = set()
    try:
        data = json.loads(json_string)
        if isinstance(data, dict):
            for key, value in data.items():
                if key in ["url", "href", "@id"] and isinstance(value, str):
                    urls.add(value)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    for key, value in item.items():
                        if key in ["url", "href", "@id"] and isinstance(value, str):
                            urls.add(value)
    except (json.JSONDecodeError, TypeError):
        pass
    return urls


def main() -> None:
    total_start_time = time.time()

    try:
        config = time_phase("Phase 1: Load initial config", load_initial_config)

        # Check if single file build mode
        single_file_path = config["env"].get("SINGLE_FILE_PATH")
        if single_file_path:
            time_phase("Single file build", build_single_file, config, single_file_path)
        else:
            config = time_phase(
                "Phase 2: Discover content", add_discovered_to_config, config
            )

            config = time_phase(
                "Phase 3: Process content", add_processed_content_to_config, config
            )

            time_phase("Phase 4: Generate site", generate_site, config)

            time_phase("Phase 5: Validate site", validate_site, config)

        output_dir = Path(config["env"]["output_dir"])
        logger.info(
            f"Done in: {time.time() - total_start_time:.2f}s (Output: {output_dir})"
        )

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
