# Bones - Static Site Generator

> When Flesh and Muscles Are Too Much

A Python static site generator with multi-language and multi site support.

## Quick Start

1. **Setup VENV & Install Dependencies**

   ```bash
   python -m venv venv
   ```

   ```bash
   venv\Scripts\activate
   ```

   ```bash
   python -m pip install --upgrade pip
   ```

   ```bash
   pip install -r requirements.txt
   ```

2. **Initialize New Site**

   ```bash
   python init.py your-site-name
   ```

3. **Configure Environment**

   ```bash
   # .env file
   DATA_FOLDER=data
   SITE_NAME=your-site-name
   BUILD_ENV=dev
   ```

4. **Build Site**

   ```bash
   python gen.py                   # Development
   python gen.py --env prod        # Production
   python gen.py --verbose         # Debug output
   ```

## Project Structure

```txt
root/
├── README.md
├── requirements.txt
├── gen.py                      # Main generator
├── .env                        # Environment configuration
│
├── themes/                     # Available themes
│   └── default/                # Default theme
│   │   ├── templates/          # Jinja2 templates
│   │   │   ├── base.html
│   │   │   ├── post.html
│   │   │   ├── page.html
│   │   │   ├── landing.html
│   │   │   ├── feed.xml
│   │   │   └── sitemap.xml
│   │   ├── assets/             # Theme assets
│   │   │   ├── css/
│   │   │   ├── fonts/
│   │   │   └── images/
│   │   └── partials/           # Template partials
│   │       ├── _macros.html
│   │       └── _schema.html
```

```txt
├── sites/                      # Site data directory
│   └── your-site/          # Site configuration
│       ├── config/
│       │   ├── build.yml   # Build settings
│       │   ├── content/    # Content configuration
│       │   │   ├── site.yml
│       │   │   ├── pages.yml
│       │   │   └── languages/
│       │   │       ├── fi.yml
│       │   │       └── en.yml
│       │   └── setup.json  # Generated config
│       ├── content/
│       │   ├── posts/       # Blog posts
│       │   │   └── 2024/
│       │   │       └── 2024-12-31-post-name/
│       │   │           ├── 2024-12-31-post-name.md
│       │   │           └── images/
│       │   └── pages/       # Static pages
│       │       ├── about/
│       │       └── landing/
│       └── assets/          # Site assets
│
└── z-public/                   # Generated output
    └── your-site/
        ├── dev/                # Development build
        └── prod/               # Production build
```

## Configuration

Create a site with init.py and see the config folder. Generator creates a config object dynamically using the folders and .yml file names in the config folder (and sub folders).

## Build Process

1. **Load Config**: Environment and YAML files
2. **Discover Content**: Scan content directories
3. **Process Content**: Parse markdown, enrich metadata
4. **Generate Site**: Render templates
5. **Validate Site**: Check links and integrity

## Commands

```bash
python init.py site-name        # Create new site
python gen.py                   # Build site
python gen.py --env prod       # Production build
python gen.py --verbose        # Debug output
```

## Requirements

- Python 3.8+
- See `requirements.txt`
