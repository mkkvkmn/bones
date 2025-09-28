# DemoSite

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

Generated on 2025-09-28 09:58:38
