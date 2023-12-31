import os
import re
import markdown
import yaml
import logging
import time
import argparse
import shutil
import email.utils
from jinja2 import Environment, FileSystemLoader
from datetime import datetime
from itertools import chain

# logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# global
SITE = {
    'logo': 'Logo',
    'title': 'My Awesome Blog',
    'description': 'A blog about awesome things.',
    'url': 'https://myproductionurl.com',
    'copyright': datetime.now().year,
    'default_img': 'default.jpg',
    'lang_html': 'fi',
    'lang_xml': 'fi-fi',
    'pages': {
        'landing': {
            'dir': '',
            'template': 'landing.html',
            'url':'',
        },
        'archive': {
            'dir': '',
            'template': 'archive.html',
            'url':'a',
            'title':'Archive',
            'description':'Description for archive',
        },
        'about': {
            'dir': '',
            'template': 'about.html',
            'url':'about',
            'title':'About',
            'description':'Description for about page',
            'img': 'default.jpg',
        },
        '404': {
            'dir': '',
            'template': '404.html',
            'url':'',
            'title':'Sorry! Page Not Found.',
            'description':'404',
            'output_file':'404.html',
        },
    },
    'post': {
        'dir': 'posts',
        'template': 'post.html'
    },
    'feed': {
        'dir': '',
        'template': 'feed.xml',
        'output_file':'feed.xml',
    },
    'sitemap': {
        'dir': '',
        'template': 'sitemap.xml',
        'output_file':'sitemap.xml',
    },
    'robots': {
        'dir': '',
        'template': 'robots.txt',
        'output_file':'robots.txt',
    },
    'templates': {
        'dir': 'templates'
    },
    'output': {
        'dir': '_public'
    },
    'assets': {
        'dir': 'assets',
        'dir_css': 'assets/css',
        'dir_img': 'assets/img',
        'dir_favicon': 'assets/favicon',
    },
}

# init jinja
env = Environment(
    loader=FileSystemLoader(SITE['templates']['dir']),
    trim_blocks=True,
    lstrip_blocks=True
)


def copy_assets():
    source = SITE['assets']['dir']
    target = os.path.join(SITE['output']['dir'], SITE['assets']['dir'])
    if os.path.exists(target):
        shutil.rmtree(target)
    shutil.copytree(source, target)


def parse_front_matter(file_content):
    pattern = re.compile(r'^---\s+(.*?)\s+---\s+(.*)', re.DOTALL)
    match = pattern.match(file_content)
    if match:
        front_matter = yaml.safe_load(match.group(1))
        content = match.group(2)
        return front_matter, content
    else:
        return {}, file_content
    

def calculate_read_time(text):
    words_per_minute = 200
    words = text.split()
    word_count = len(words)
    read_time = round(word_count / words_per_minute)
    return max(read_time, 1)


def assign_prev_next_posts(posts_meta):
    for i, post_meta in enumerate(posts_meta):
        post_meta['prev_post'] = posts_meta[i - 1] if i > 0 else None
        post_meta['next_post'] = posts_meta[i + 1] if i < len(posts_meta) - 1 else None


def get_posts_meta():
    posts_meta = []
    url_set = set()

    for md_file in os.scandir(SITE['post']['dir']):
        if md_file.name.endswith('.md'):

            with open(md_file.path, 'r') as file:
                file_content = file.read()

            front_matter, md_content = parse_front_matter(file_content)
            read_time = calculate_read_time(md_content)

            url = front_matter.get('url').strip('/')
            if not url:
                raise ValueError(f"Missing URL in front matter of {md_file.name}")
            if url in url_set:
                raise ValueError(f"Duplicate URL '{url}' found in {md_file.name}")
            url_set.add(url)

            date_str = front_matter.get('date', '')
            date_obj = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S %z')
            formatted_date = email.utils.formatdate(date_obj.timestamp())

            post_mod_time = os.path.getmtime(md_file.path)

            posts_meta.append({
                'title': front_matter.get('title', ''),
                'description': front_matter.get('description', ''),
                'date': date_obj,
                'date_xml': formatted_date,
                'url': url,
                'filename': md_file.name,
                'content_md': md_content,
                'read_time': read_time,
                'img': front_matter.get('img', ''),
                'mod_time': post_mod_time
            })

    return posts_meta

def get_meta():
    posts_meta = get_posts_meta()
    latest_post = max(posts_meta, key=lambda x: x['date'])
    last_post_mod_time = max(os.path.getmtime(f.path) for f in os.scandir(SITE['post']['dir']) if f.is_file())
    last_template_mod_time = max(
        os.path.getmtime(f.path) for f in chain(
            os.scandir(SITE['templates']['dir']), 
            os.scandir(SITE['assets']['dir_css'])
        ) if f.is_file()
    )

    assign_prev_next_posts(posts_meta)

    posts_meta.sort(key=lambda x: x['date'], reverse=True) # sort reverse for archive

    return {
        'last_post_mod_time':last_post_mod_time,
        'last_template_mod_time':last_template_mod_time,
        'last_build_date': latest_post['date_xml'],
        'posts':posts_meta,
        'latest_post':latest_post,
        'site':SITE,
    }


def clean_or_make_output_dir(full_rebuild):
    output_dir = SITE['output']['dir']
    if full_rebuild:
        logging.info(f"Full rebuild: clearing folder {output_dir}...")
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)


def build_page(template_name, source_path, output_path, meta, full_rebuild=False):
    source_mod_time = os.path.getmtime(source_path) if os.path.exists(source_path) else 0
    output_mod_time = os.path.getmtime(output_path) if os.path.exists(output_path) else 0

    # all need rebuild, if template modified, file doesn't exist or full rebuild
    needs_rebuild = (
        full_rebuild or 
        not os.path.exists(output_path) or 
        os.path.getmtime(output_path) < meta['last_template_mod_time']
    )
    
    if SITE['post']['dir'] in  source_path:
        needs_rebuild = needs_rebuild or source_mod_time > output_mod_time 
    else:
        # landing, feed, sitemap needs rebuild if new post
        needs_rebuild = needs_rebuild or os.path.getmtime(output_path) < meta['last_post_mod_time']

    if needs_rebuild:
        template = env.get_template(template_name)
        content = template.render(meta=meta)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as file:
            file.write(content)
        logging.info(f"Built: {output_path}")

  
def build_pages(meta,full_rebuild=False):
    for key, page in SITE['pages'].items():
        source_path = os.path.join(SITE['templates']['dir'], page['template'])
        output_path = os.path.join(SITE['output']['dir'], page.get('url', ''), page.get('output_file', 'index.html'))
        page_meta = {**meta, 'page': page}
        build_page(page['template'], source_path, output_path, page_meta, full_rebuild)

    
def build_posts(meta, full_rebuild=False):
    for post_meta in meta['posts']:
        source_path = os.path.join(SITE['post']['dir'], post_meta['filename'])
        output_path = os.path.join(SITE['output']['dir'], post_meta['url'], 'index.html')
        html_content = markdown.markdown((post_meta['content_md']))
        page_meta = {**meta, 'post': post_meta,'content': html_content}
        build_page(SITE['post']['template'], source_path, output_path, page_meta, full_rebuild)


def build_feed(meta, full_rebuild=False):
    source_path = os.path.join(SITE['templates']['dir'], SITE['feed']['template'])
    output_path = os.path.join(SITE['output']['dir'], SITE['feed']['output_file'])
    build_page(SITE['feed']['template'], source_path, output_path, meta, full_rebuild)


def build_sitemap(meta, full_rebuild=False):
    source_path = os.path.join(SITE['templates']['dir'], SITE['sitemap']['template'])
    output_path = os.path.join(SITE['output']['dir'], SITE['sitemap']['output_file'])
    build_page(SITE['sitemap']['template'], source_path, output_path, meta, full_rebuild)


def build_robotstxt(meta, full_rebuild=False):
    source_path = os.path.join(SITE['templates']['dir'], SITE['robots']['template'])
    output_path = os.path.join(SITE['output']['dir'], SITE['robots']['output_file'])
    build_page(SITE['robots']['template'], source_path, output_path, meta, full_rebuild)


def generate_site(full_rebuild=False):
    start_time = time.time()

    clean_or_make_output_dir(full_rebuild)

    meta = get_meta()

    build_pages(meta, full_rebuild)
    build_posts(meta, full_rebuild)
    build_feed(meta, full_rebuild)
    build_sitemap(meta, full_rebuild)
    build_robotstxt(meta, full_rebuild)
    copy_assets()

    elapsed_time = time.time() - start_time
    logging.info(f"Done in {elapsed_time:.2f} seconds")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Static Site Generator")
    parser.add_argument("--full", action="store_true", help="Perform a full site rebuild")
    parser.add_argument("--dev", action="store_true", help="Run in production mode")
    args = parser.parse_args()

    if args.dev:
        base_url = 'http://localhost:8000'
        SITE['url'] = base_url

    generate_site(full_rebuild=args.full)
