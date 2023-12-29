# source venv/bin/activate

import os
import re
import markdown
import yaml
import logging
import time
import argparse
import shutil
from jinja2 import Environment, FileSystemLoader

# logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# global
SITE = {
    'title': 'My Awesome Blog',
    'description': 'A blog about awesome things.',
    'landing': {
        'dir': '',
        'template': 'landing.html'
    },
    'archive': {
        'dir': '',
        'template': 'archive.html',
        'url':'a',
        'title':'Archive',
        'description':'Description for archive',
    },
    'post': {
        'dir': 'posts',
        'template': 'post.html'
    },
    'templates': {
        'dir': 'templates'
    },
    'output': {
        'dir': '_public'
    },
}

# init jinja
env = Environment(loader=FileSystemLoader(SITE['templates']['dir']))


def parse_front_matter(file_content):
    pattern = re.compile(r'^---\s+(.*?)\s+---\s+(.*)', re.DOTALL)
    match = pattern.match(file_content)
    if match:
        front_matter = yaml.safe_load(match.group(1))
        content = match.group(2)
        return front_matter, content
    else:
        return {}, file_content


def get_meta():
    posts_meta = []
    url_set = set()
    last_post_mod_time = 0

    last_template_mod_time = max(os.path.getmtime(f.path) for f in os.scandir(SITE['templates']['dir']) if f.is_file())

    for md_file in os.scandir(SITE['post']['dir']):
        if md_file.name.endswith('.md'):
            post_mod_time = os.path.getmtime(md_file.path)
            last_post_mod_time = max(last_post_mod_time, post_mod_time)

            with open(md_file.path, 'r') as file:
                file_content = file.read()
            front_matter, md_content = parse_front_matter(file_content)

            url = front_matter.get('url').strip('/')
            if not url:
                raise ValueError(f"Missing URL in front matter of {md_file.name}")
            if url in url_set:
                raise ValueError(f"Duplicate URL '{url}' found in {md_file.name}")
            url_set.add(url)

            posts_meta.append({
                'title': front_matter.get('title', ''),
                'excerpt': front_matter.get('excerpt', ''),
                'date': front_matter.get('date', ''),
                'url': url,
                'filename': md_file.name,
                'front_matter':front_matter,
                'content': md_content
            })

    latest_post = max(posts_meta, key=lambda x: x['date'])

    meta = {
        'last_post_mod_time':last_post_mod_time,
        'last_template_mod_time':last_template_mod_time,
        'posts':posts_meta,
        'latest_post':latest_post,
        'site':SITE,
    }

    return meta


def rebuild(full_rebuild):
    output_dir = SITE['output']['dir']
    if full_rebuild:
        logging.info(f"Full rebuild: clearing folder {output_dir}...")
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)


def build_page(template_name, output_path, meta, full_rebuild=False):
    needs_rebuild = (
        full_rebuild or 
        not os.path.exists(output_path) or 
        os.path.getmtime(output_path) < meta['last_post_mod_time'] or 
        os.path.getmtime(output_path) < meta['last_template_mod_time']
    )

    if needs_rebuild:
        template = env.get_template(template_name)
        content = template.render(meta=meta)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as file:
            file.write(content)
        logging.info(f"Built: {output_path}")

 
def build_landing(meta, full_rebuild=False):
    output_path = os.path.join(SITE['output']['dir'], 'index.html')
    page_meta = {**meta, 'page': SITE['landing']}
    build_page(SITE['landing']['template'], output_path, page_meta, full_rebuild)


def build_archive(meta, full_rebuild=False):
    output_path = os.path.join(SITE['output']['dir'], SITE['archive']['url'], 'index.html')
    page_meta = {**meta, 'page': SITE['archive']}
    build_page(SITE['archive']['template'], output_path, page_meta, full_rebuild)

    
def build_posts(meta, full_rebuild=False):
    for post_meta in meta['posts']:
        source_path = os.path.join(SITE['post']['dir'], post_meta['filename'])
        output_path = os.path.join(SITE['output']['dir'], post_meta['url'], 'index.html')
        source_mod_time = os.path.getmtime(source_path) if os.path.exists(source_path) else 0
        output_mod_time = os.path.getmtime(output_path) if os.path.exists(output_path) else 0

        if os.path.exists(source_path) and source_mod_time > output_mod_time:
            html_content = markdown.markdown((post_meta['content']))
            page_meta = {**meta, 'post': post_meta['front_matter'],'content': html_content,}
            build_page(SITE['post']['template'], output_path, page_meta,full_rebuild)


def generate_site(full_rebuild=False):
    start_time = time.time()

    rebuild(full_rebuild)

    try:
        meta = get_meta()
    except ValueError as e:
        logging.error(e)
        return

    build_landing(meta, full_rebuild)
    build_archive(meta, full_rebuild)
    build_posts(meta, full_rebuild)

    elapsed_time = time.time() - start_time
    logging.info(f"Done in {elapsed_time:.2f} seconds")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Static Site Generator")
    parser.add_argument("--full", action="store_true", help="Perform a full site rebuild")
    args = parser.parse_args()

    generate_site(full_rebuild=args.full)
