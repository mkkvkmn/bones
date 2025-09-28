#!/usr/bin/env python3
"""
Site Initialization Script

This script creates a new site structure based on the project structure documentation.
It sets up the basic directory structure, configuration files, and template files for a new site.

Usage:
    python init.py <site_name> [--sites-folder <path>]

Examples:
    python init.py DemoSite --sites-folder sites
    python init.py DemoSite -sf sites

The script reads SITES_FOLDER from your .env file, but command line arguments take precedence (defaults to "sites").
"""

import argparse
import yaml
from pathlib import Path
from datetime import datetime
import sys


def create_directory_structure(site_path: Path) -> None:
    """Create the basic directory structure for a new site."""
    directories = [
        "config",
        "config/content",
        "config/content/languages",
        "content/posts",
        "content/pages",
        "content/drafts",
        "templates/partials/auto-generated",
        "assets/images",
        "assets/css",
        "assets/fonts",
        "assets/favicon",
    ]

    for directory in directories:
        dir_path = site_path / directory
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"Created directory: {dir_path.as_posix()}")


def create_build_yml(site_path: Path, site_name: str) -> None:
    """Create build.yml configuration file."""
    build_config = {
        "envs": {
            "max_workers": 8,
            "dev": {
                "output": f"sites/{site_name.lower()}/z-public/dev",
                "url": "http://localhost:8000",
            },
            "prod": {
                "output": f"sites/{site_name.lower()}/z-public/prod",
                "url": f"https://{site_name.lower()}.com",
            },
        },
        "settings": {
            "create_config": True,
            "validate_site": True,
            "prettify_html": True,
            "front_matter_parts": 3,
            "read_time_words_per_minute": 200,
            "date_format": "%Y-%m-%d %H:%M:%S %z",
            "html_date_format": "%d.%m.%Y",
            "json_indent": 2,
            "time_precision": 3,
        },
        "language_switcher": {
            "enabled": False,
            "codes": ["en", "fi"],
        },
    }

    build_file = site_path / "config" / "build.yml"
    with open(build_file, "w", encoding="utf-8") as f:
        yaml.dump(
            build_config,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    print(f"Created: {build_file.as_posix()}")


def create_site_yml(site_path: Path, site_name: str) -> None:
    """Create site.yml configuration file."""
    site_config = {
        "logo": site_name,
        "name": site_name,
        "img": "default.jpg",
        "theme": "default",
        "contact": {
            "email": f"contact@{site_name}.com",
            "subscribe_url": "",
            "twitter": "",
            "linkedin": "",
        },
    }

    site_file = site_path / "config" / "content" / "site.yml"
    with open(site_file, "w", encoding="utf-8") as f:
        yaml.dump(
            site_config,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    print(f"Created: {site_file.as_posix()}")


def create_comments_yml(site_path: Path, site_name: str) -> None:
    """Create comments.yml configuration file."""
    comments_config = {
        "enabled": True,
        "isso_url": "",
        "isso_script_url": "",
    }

    comments_file = site_path / "config" / "content" / "comments.yml"
    with open(comments_file, "w", encoding="utf-8") as f:
        yaml.dump(
            comments_config,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    print(f"Created: {comments_file.as_posix()}")


def create_pages_yml(site_path: Path, site_name: str) -> None:
    """Create pages.yml configuration file."""
    pages_config = {
        "defaults": {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S +0000"),
            "template": "page.html",
            "schema": {"type": "WebPage"},
            "language": "en",
        },
        "tags": {
            "enabled": True,
            "template": "tag.html",
            "languages": ["en"],
        },
        "items": {
            "404": {
                "name": "404",
                "title": "Page Not Found",
                "description": "404 - Page not found",
                "template_page": "404/404.html",
                "target_file": "404.html",
                "url": "404",
            },
            "feed": {
                "name": "feed",
                "template_page": "feed/feed.xml",
                "target_file": "feed.xml",
            },
            "sitemap": {
                "name": "sitemap",
                "template_page": "sitemap/sitemap.xml",
                "target_file": "sitemap.xml",
            },
            "robots": {
                "name": "robots",
                "template_page": "robots/robots.txt",
                "target_file": "robots.txt",
            },
        },
    }

    pages_file = site_path / "config" / "content" / "pages.yml"
    with open(pages_file, "w", encoding="utf-8") as f:
        yaml.dump(
            pages_config,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    print(f"Created: {pages_file.as_posix()}")


def create_language_yml(site_path: Path, site_name: str, lang_code: str) -> None:
    """Create language-specific configuration file."""
    if lang_code == "en":
        lang_config = {
            "en": {
                "site_title": f"Welcome to {site_name}",
                "site_description": "Your site description here",
                "skip_language_path_in_url": True,
                "tag_url_prefix": "tag",
                "html": "en",
                "xml": "en",
                "table_of_contents_title": "Table of Contents",
                "latest": "Latest blog:",
                "read": "min read",
                "footer": {
                    "columns": {
                        "left": {
                            "title": "Disclaimer",
                            "content": (
                                "This is a sample disclaimer for your new site. You can customize this content."
                            ),
                        },
                        "right": {
                            "subscribe": {
                                "enabled": False,
                                "title": "Subscribe",
                                "description": "Articles only. No spam. Cancel anytime.",
                                "email_placeholder": "Email",
                                "button_text": "Subscribe",
                            },
                            "tag_cloud": {"title": "Tags", "enabled": True},
                        },
                    }
                },
                "posts": {
                    "meta": {"read_time_text": "min read"},
                    "after": {
                        "privacy_notice": (
                            "The site does not store any information about you or use cookies when you read articles. "
                            "Suggestions, comments and questions can also be sent by"
                        ),
                        "email": "email",
                        "comment_notice": (
                            "Note! Commenting uses cookies. Your name, email and website are saved to your browser so you can "
                            "comment more easily in the future with the same information. Fields are optional and you can "
                            "leave them empty if you wish."
                        ),
                    },
                    "comments": {
                        "javascript_required": "Enable Javascript if you want to participate in commenting.",
                        "back_to_top": "Back to comments ↑",
                        "fields": {"name": "Name", "email": "Email", "website": "www"},
                    },
                },
                "archive": {
                    "no_posts": "No articles available.",
                    "tag_title": "Tag",
                    "tag_description": "Posts tagged with '{tag}'",
                },
            }
        }
    else:  # Default to Finnish
        lang_config = {
            "fi": {
                "site_title": f"Tervetuloa {site_name}",
                "site_description": "Sivuston kuvaus tähän",
                "skip_language_path_in_url": True,
                "tag_url_prefix": "aihepiiri",
                "html": "fi",
                "xml": "fi",
                "table_of_contents_title": "Sisältö",
                "latest": "Uusin blogi:",
                "read": "min lukuaika",
                "footer": {
                    "columns": {
                        "left": {
                            "title": "Vastuuvapaus",
                            "content": (
                                "Kirjoitan mielipiteitäni blogissa ja olen usein väärässä. "
                                "Toivon, että tarinani antavat sinulle uusia ideoita, mutta mitään mitä kirjoitan ei "
                                "tulisi tulkita neuvoksi. En ole vastuussa vahingoista tai tappioista. "
                                "Lukeminen ja soveltaminen on omalla vastuullasi."
                            ),
                        },
                        "right": {
                            "subscribe": {
                                "enabled": False,
                                "title": "Tilaa",
                                "description": "Vain artikkelit. Ei roskapostia. Peru milloin vain.",
                                "email_placeholder": "Sähköposti",
                                "button_text": "Tilaa",
                            },
                            "tag_cloud": {"title": "Aihepiirit", "enabled": True},
                        },
                    }
                },
                "posts": {
                    "meta": {"read_time_text": "min lukuaika"},
                    "after": {
                        "privacy_notice": (
                            "Sivusto ei tallenna sinusta mitään tietoja tai käytä evästeitä, kun luet artikkeleita. "
                            "Toiveita, kommentteja ja kysymyksiä voi laittaa tulemaan myös"
                        ),
                        "email": "meilillä",
                        "comment_notice": (
                            "Huom! Kommentointi käyttää evästeitä. Nimi, sähköposti ja verkkosivusi tallennetaan selaimeesi, "
                            "jotta voit jatkossa kommentoida helpommin samoilla tiedoilla. Kentät vapaaehtoisia ja voit "
                            "jättää ne halutessasi tyhjiksi."
                        ),
                    },
                    "comments": {
                        "javascript_required": "Laita Javascript päälle, jos haluat osallistua kommentointiin.",
                        "back_to_top": "Kommenttien alkuun ↑",
                        "fields": {
                            "name": "Nimi",
                            "email": "Sähköposti",
                            "website": "www",
                        },
                    },
                },
                "archive": {
                    "no_posts": "Ei artikkeleita saatavilla.",
                    "tag_title": "Aihepiiri",
                    "tag_description": "Artikkelit aihepiiristä '{tag}'",
                },
            }
        }

    lang_file = site_path / "config" / "content" / "languages" / f"{lang_code}.yml"
    with open(lang_file, "w", encoding="utf-8") as f:
        yaml.dump(
            lang_config,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    print(f"Created: {lang_file.as_posix()}")


def create_posts_yml(site_path: Path, site_name: str) -> None:
    """Create posts.yml configuration file."""
    posts_config = {
        "defaults": {
            "template": "post.html",
            "schema": {"type": "BlogPosting"},
            "draft": False,
        },
    }

    posts_file = site_path / "config" / "content" / "posts.yml"
    with open(posts_file, "w", encoding="utf-8") as f:
        yaml.dump(
            posts_config,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    print(f"Created: {posts_file.as_posix()}")


def create_sample_posts(site_path: Path, site_name: str) -> None:
    """Create sample posts to demonstrate the structure."""
    current_year = datetime.now().year

    # First post - Welcome post
    post1_dir = (
        site_path
        / "content"
        / "posts"
        / str(current_year)
        / f"{current_year}-01-01-welcome-post"
    )
    post1_dir.mkdir(parents=True, exist_ok=True)

    post1_content = f"""---
title: "Welcome to {site_name}"
date: "{current_year}-01-01 12:00:00 +0000"
url: "welcome-post"
description: "Welcome to your new site"
tags: ["welcome", "first-post"]
language: "en"
---

# Welcome to {site_name}

This is your first post. You can edit this file to add your content.

## Getting Started

1. Edit this post in `{post1_dir}/{current_year}-01-01-welcome-post.md`
2. Add images to the `images/` subdirectory
3. Customize your site configuration in `config/content.yml`
4. Run the generator to build your site

## Features

- Markdown support with front matter
- Automatic date processing
- Tag-based organization
- Multi-language support
- Responsive templates

Happy blogging!
"""

    post1_file = post1_dir / f"{current_year}-01-01-welcome-post.md"
    with open(post1_file, "w", encoding="utf-8") as f:
        f.write(post1_content)

    # Create images directory for first post
    images1_dir = post1_dir / "images"
    images1_dir.mkdir(exist_ok=True)
    placeholder1_image = images1_dir / "placeholder.placeholder"
    placeholder1_image.write_text("Add your post images here")

    print(f"Created sample post: {post1_file.as_posix()}")

    # Second post - Getting Started guide
    post2_dir = (
        site_path
        / "content"
        / "posts"
        / str(current_year)
        / f"{current_year}-01-15-getting-started"
    )
    post2_dir.mkdir(parents=True, exist_ok=True)

    post2_content = f"""---
title: "Getting Started with Static Site Generation"
date: "{current_year}-01-15 14:30:00 +0000"
url: "getting-started"
description: "Learn how to create and customize your static site"
tags: ["tutorial", "guide", "static-site"]
language: "en"
---

# Getting Started with Static Site Generation

This is your second post, demonstrating more advanced features of the static site generator.

## What is a Static Site Generator?

A static site generator takes your content and templates, then generates a complete website as static HTML files. This approach offers several advantages:

- **Performance**: Static files load faster than dynamic pages
- **Security**: No server-side code means fewer vulnerabilities
- **Reliability**: Fewer moving parts means less can go wrong
- **Cost**: Can be hosted on simple file hosting services

## Key Features

### Markdown Support
Write your content in Markdown for easy formatting:

```markdown
# This is a heading
## This is a subheading

- Bullet point 1
- Bullet point 2

**Bold text** and *italic text*
```

### Front Matter
Each post can include metadata at the top:

```yaml
---
title: "Your Post Title"
date: "2025-01-15 14:30:00 +0000"
url: "your-post-url"
description: "A brief description"
tags: ["tag1", "tag2"]
language: "en"
---
```

### Template System
The generator uses Jinja2 templates for flexible page layouts.

## Next Steps

1. Explore the theme templates in `themes/default/templates/`
2. Customize the CSS in your site's `assets/css/` directory
3. Add more content and experiment with different post formats
4. Configure your site settings in the `config/` directory

Happy building!
"""

    post2_file = post2_dir / f"{current_year}-01-15-getting-started.md"
    with open(post2_file, "w", encoding="utf-8") as f:
        f.write(post2_content)

    # Create images directory for second post
    images2_dir = post2_dir / "images"
    images2_dir.mkdir(exist_ok=True)
    placeholder2_image = images2_dir / "placeholder.placeholder"
    placeholder2_image.write_text("Add your post images here")

    print(f"Created sample post: {post2_file.as_posix()}")


def create_landing_page(site_path: Path, site_name: str) -> None:
    """Create a landing page template that extends the theme's base template."""
    landing_dir = site_path / "content" / "pages" / "landing"
    landing_dir.mkdir(parents=True, exist_ok=True)

    landing_content = f"""---
title: "{site_name}"
description: "Welcome to {site_name}"
url: "landing"
target_file: "index.html"
date: "{datetime.now().strftime('%Y-%m-%d %H:%M:%S +0000')}"
template_page: "landing/landing.html"
language: "en"
---

{{% extends "base.html" %}}

{{% block content %}}
<main>
    <section class="hero">
        <h1>{{{{ doc.title or '{site_name}' }}}}</h1>
        <p>{{{{ doc.description or 'Welcome to {site_name}' }}}}</p>
    </section>
    
    {{% if latest_post %}}
    <section class="latest-post">
        <h2>Latest Post</h2>
        <article>
            <h3><a href="/{{{{ latest_post.url }}}}">{{{{ latest_post.title }}}}</a></h3>
            <p>{{{{ latest_post.description }}}}</p>
            <time>{{{{ latest_post.date_html }}}}</time>
        </article>
    </section>
    {{% endif %}}
    
    <section class="welcome">
        <h2>Welcome to Your New Site</h2>
        <p>This is your new static site generated with the Bones static site generator. You can customize this landing page by editing the template file.</p>
        
        <h3>Getting Started</h3>
        <ul>
            <li>Edit this landing page template</li>
            <li>Add your own content and pages</li>
            <li>Customize the theme and styling</li>
            <li>Configure your site settings</li>
        </ul>
    </section>
</main>
{{% endblock %}}"""

    landing_file = landing_dir / "landing.html"
    with open(landing_file, "w", encoding="utf-8") as f:
        f.write(landing_content)

    print(f"Created landing page: {landing_file.as_posix()}")


def create_about_page(site_path: Path, site_name: str) -> None:
    """Create an about page template that extends the theme's base template."""
    about_dir = site_path / "content" / "pages" / "about"
    about_dir.mkdir(parents=True, exist_ok=True)

    about_content = f"""---
title: "About {site_name}"
description: "Learn more about {site_name}"
url: "about"
date: "{datetime.now().strftime('%Y-%m-%d %H:%M:%S +0000')}"
template_page: "about/about.html"
nav_title: "About"
language: "en"
---

{{% extends "base.html" %}}

{{% block content %}}
<main>
    <h1>{{{{ doc.title or 'About {site_name}' }}}}</h1>
    <p>{{{{ doc.description or 'Learn more about {site_name}' }}}}</p>
    
    <section>
        <h2>About This Site</h2>
        <p>This is a sample about page for your new site. You can customize this content.</p>
        
        <h3>Features</h3>
        <ul>
            <li>Static site generation with markdown support</li>
            <li>Theme-based design system</li>
            <li>Responsive templates</li>
            <li>Multi-language support</li>
            <li>Tag-based content organization</li>
        </ul>
        
        <h3>Contact</h3>
        <p>Feel free to get in touch if you have any questions or suggestions.</p>
    </section>
</main>
{{% endblock %}}"""

    about_file = about_dir / "about.html"
    with open(about_file, "w", encoding="utf-8") as f:
        f.write(about_content)

    print(f"Created about page: {about_file.as_posix()}")


def create_archive_page(site_path: Path, site_name: str) -> None:
    """Create an archive page template that extends the theme's base template."""
    archive_dir = site_path / "content" / "pages" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    archive_content = f"""---
title: "Blog"
description: "All blog posts"
url: "blog"
date: "{datetime.now().strftime('%Y-%m-%d %H:%M:%S +0000')}"
template_page: "archive/archive.html"
nav_title: "Blog"
language: "en"
---

{{% extends "base.html" %}}

{{% block head %}}
    {{% include 'partials/auto-generated/_archive_css.html' %}}
{{% endblock %}}

{{% block content %}}
<div class="archive">
    <h1>{{{{ doc.title }}}}</h1>
    
    {{% set filtered_posts = [] %}}
    {{% for post in posts %}}
        {{% if post.language == doc.language %}}
            {{% set _ = filtered_posts.append(post) %}}
        {{% endif %}}
    {{% endfor %}}
    
    {{% if filtered_posts %}}
        <div class="posts-list">
            {{% for post in filtered_posts %}}
                <article class="post-summary">
                    <h2><a href="{{{{ env.url }}}}/{{{{ post.url }}}}/">{{{{ post.title }}}}</a></h2>
                    <div class="post-meta">
                        {{{{ post.date_html }}}} - {{{{ post.read_time or 5 }}}} {{{{ languages[doc.language].read }}}}
                    </div>
                    {{% if post.description %}}
                    <p class="post-description">{{{{ post.description }}}}</p>
                    {{% endif %}}
                    {{% if post.tags %}}
                    <div class="post-tags">
                        {{% for tag in post.tags %}}
                        {{% set tag_prefix = languages[post.language].get('tag_url_prefix', 'tag') %}}
                        {{% if languages[post.language].get('skip_language_path_in_url', False) %}}
                        {{% set tag_url = env.url ~ '/' ~ tag_prefix ~ '/' ~ tag ~ '/' %}}
                        {{% else %}}
                        {{% set tag_url = env.url ~ '/' ~ post.language ~ '/' ~ tag_prefix ~ '/' ~ tag ~ '/' %}}
                        {{% endif %}}
                        <a href="{{{{ tag_url }}}}" class="tag">{{{{ tag }}}}</a>
                        {{% endfor %}}
                    </div>
                    {{% endif %}}
                </article>
            {{% endfor %}}
        </div>
    {{% else %}}
        <p>{{{{ languages[doc.language].archive.no_posts }}}}</p>
    {{% endif %}}
</div>
{{% endblock %}}"""

    archive_file = archive_dir / "archive.html"
    with open(archive_file, "w", encoding="utf-8") as f:
        f.write(archive_content)

    print(f"Created archive page: {archive_file.as_posix()}")


def create_asset_placeholders(site_path: Path) -> None:
    """Create placeholder files for assets."""
    # CSS placeholder
    css_file = site_path / "assets" / "css" / "main.css"
    css_file.parent.mkdir(parents=True, exist_ok=True)
    css_file.write_text(
        """/* Main CSS file for your site */
body {
    font-family: Arial, sans-serif;
    line-height: 1.6;
    margin: 0;
    padding: 20px;
}

h1 {
    color: #333;
}

/* Customize your styles here */"""
    )

    # Archive CSS file
    archive_css_file = site_path / "assets" / "css" / "archive.css"
    archive_css_file.write_text(
        """/* archive */
.archive {
    max-width: 780px;
    margin: 0 auto;
}

.posts-list {
    list-style: none;
    padding-left: 0;
    margin: 0;
}

.post-summary {
    margin-bottom: 2rem;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid #eee;
}

.post-summary:last-child {
    border-bottom: none;
}

.post-summary h2 {
    margin: 0 0 0.5rem 0;
}

.post-summary h2 a {
    text-decoration: none;
    color: #333;
}

.post-summary h2 a:hover {
    color: #007acc;
}

.post-meta {
    color: #666;
    font-size: 0.9rem;
    margin-bottom: 0.5rem;
}

.post-description {
    color: #555;
    margin: 0.5rem 0;
}

.post-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.8rem;
}

.post-tags .tag {
    display: inline-block;
    padding: 0.2rem 0.6rem;
    background: #f5f5f5;
    border: 1px solid #ddd;
    border-radius: 12px;
    font-size: 0.85rem;
    font-weight: 500;
    color: #666;
    white-space: nowrap;
    text-decoration: none;
    transition: all 0.2s ease;
}

.post-tags .tag:hover {
    background: #e9e9e9;
    border-color: #bbb;
    color: #333;
}
/* end archive */"""
    )

    # Favicon placeholder
    favicon_file = site_path / "assets" / "favicon" / "favicon.ico"
    favicon_file.parent.mkdir(parents=True, exist_ok=True)
    favicon_file.write_text("Add your favicon.ico file here")

    print("Created asset placeholder files")


def create_readme(site_path: Path, site_name: str) -> None:
    """Create a README file for the new site."""
    readme_content = f"""# {site_name}

This site was created using the static site generator with theme support.

## Structure

- `config/` - Site configuration files
- `content/` - Posts and pages content
- `assets/` - Site-specific images, CSS, fonts, and other assets
- `public/` - Generated output (created by the generator)

## Theme System

This site uses the theme system:
- **Theme**: `themes/default/` (configurable in `content.yml`)
- **Theme templates**: `themes/default/templates/`
- **Theme assets**: `themes/default/assets/`
- **Theme content pages**: `themes/default/content/pages/` (feed.xml, sitemap.xml, etc.)

Site-specific templates in `content/pages/` can override theme templates.

## Getting Started

1. **Customize Configuration**: Edit `config/content/site.yml`, `config/content/pages.yml`, and `config/build.yml`
2. **Add Content**: Create posts in `content/posts/YYYY/` directories
3. **Add Pages**: Create pages in `content/pages/` directory
4. **Add Assets**: Place images, CSS, and fonts in `assets/` directory
5. **Customize Theme**: Override theme templates by creating files in `content/pages/`
6. **Build Site**: Run the generator to create your site

## Posts

Posts are organized by year in `content/posts/YYYY/` directories. Each post should have:
- Front matter with title, date, URL, and other metadata
- Markdown content
- Optional images in a `images/` subdirectory

## Pages

Pages are in `content/pages/` and can use HTML, XML, or other formats.
- Standard pages (feed.xml, sitemap.xml, robots.txt, 404.html) are provided by the theme
- Custom pages can override theme templates
- Use `template_page` to specify which template to use

## Assets

- `assets/images/` - Site-specific images
- `assets/css/` - Site-specific stylesheets
- `assets/fonts/` - Site-specific fonts
- `assets/favicon/` - Site favicon

Theme assets are automatically included from `themes/default/assets/`.

## Configuration

- `config/content/site.yml` - Site identity and theme selection
- `config/content/pages.yml` - Page configuration and defaults
- `config/content/languages/` - Language-specific configurations
- `config/build.yml` - Build settings and environment configuration

## Theme Customization

To customize the theme:
1. Create files in `content/pages/` with the same name as theme templates
2. Site templates take priority over theme templates
3. Modify `config/content/site.yml` to change theme or template settings

Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    readme_file = site_path / "README.md"
    with open(readme_file, "w", encoding="utf-8") as f:
        f.write(readme_content)

    print(f"Created: {readme_file.as_posix()}")


# Template copying functionality removed - each site should be independent


def main():
    parser = argparse.ArgumentParser(description="Initialize a new site structure")
    parser.add_argument("site_name", help="Name of the new site")
    parser.add_argument(
        "--sites-folder",
        "-sf",
        help="Sites folder path (overrides .env and defaults to 'sites')",
    )

    args = parser.parse_args()
    site_name = args.site_name

    # Load environment variables from .env file
    env_file = Path(".env")
    env_vars = {}

    if env_file.exists():
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    value = value.strip().strip("'\"")
                    if "#" in value:
                        value = value.split("#")[0].strip()
                    env_vars[key.strip()] = value

    # Command line takes precedence over .env, then defaults
    sites_folder = Path(args.sites_folder or env_vars.get("SITES_FOLDER", "sites"))
    # Always use lowercase for folder paths
    site_path = sites_folder / site_name.lower()

    if site_path.exists():
        print(f"Site '{site_name}' already exists at {site_path}")
        print("Clearing existing site...")
        import shutil

        shutil.rmtree(site_path)
        print(f"Cleared existing site: {site_path}")

    print(f"Initializing new site: {site_name}")
    print(f"Location: {site_path.as_posix()} (folder: {site_name.lower()})")
    print()

    try:
        # Create directory structure
        create_directory_structure(site_path)
        print()

        # Create configuration files
        create_build_yml(site_path, site_name)
        create_site_yml(site_path, site_name)
        create_comments_yml(site_path, site_name)
        create_pages_yml(site_path, site_name)
        create_posts_yml(site_path, site_name)
        create_language_yml(site_path, site_name, "en")
        print()

        # Create sample content
        create_sample_posts(site_path, site_name)
        print()

        # Create landing page template
        create_landing_page(site_path, site_name)
        print()

        # Create about page template
        create_about_page(site_path, site_name)
        print()

        # Create archive page template
        create_archive_page(site_path, site_name)
        print()

        # Theme system provides standard pages (feed.xml, sitemap.xml, robots.txt, 404.html)
        print()

        # Create asset placeholders
        create_asset_placeholders(site_path)
        print()

        # Each site is created independently with its own configuration

        # Create README
        create_readme(site_path, site_name)
        print()

        print(f"✅ Site '{site_name}' initialized successfully!")
        print()
        print("Next steps:")
        print(f"1. Customize configuration in {site_path.as_posix()}/config/")
        print(f"2. Add content to {site_path.as_posix()}/content/")
        print(f"3. Add assets to {site_path.as_posix()}/assets/")
        print("4. Customize theme templates if needed")
        print(
            f"5. Run the generator: python gen.py --site-name {site_name} --sites-folder {sites_folder}"
        )

    except Exception as e:
        print(f"Error initializing site: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
