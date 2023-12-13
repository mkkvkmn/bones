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

# configurations
OUTPUT_DIR = '_public'
POSTS_DIR = 'posts'
TEMPLATES_DIR = 'templates'
TEMPLATE_FOR_POST = 'post.html'
TEMPLATE_FOR_ARCHIVE = 'archive.html'
TEMPLATE_FOR_LANDING = 'landing.html'
ARCHIVE_URL = 'a'

# Initialize Jinja environment once and load templates
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
post_template = env.get_template(TEMPLATE_FOR_POST)

def parse_front_matter(file_content):
    pattern = re.compile(r'^---\s+(.*?)\s+---\s+(.*)', re.DOTALL)
    match = pattern.match(file_content)
    if match:
        front_matter = yaml.safe_load(match.group(1))
        content = match.group(2)
        # print(front_matter)
        return front_matter, content
    else:
        return {}, file_content

def markdown_to_html(md_content):
    return markdown.markdown(md_content)

def apply_template(front_matter, html_content):
    return post_template.render(post=front_matter, content=html_content)

def is_outdated(source_path, output_path, template_mod_time):
    if not os.path.exists(output_path):
        return True
    source_mod_time = os.path.getmtime(source_path)
    output_mod_time = os.path.getmtime(output_path)
    return source_mod_time > output_mod_time or template_mod_time > output_mod_time


def get_metadata():
    posts_metadata = []
    url_set = set()
    latest_post_mod_time = 0

    template_mod_time = max(os.path.getmtime(f.path)
                        for f in os.scandir(TEMPLATES_DIR)
                        if f.is_file())

    for md_file in os.scandir(POSTS_DIR):
        if md_file.name.endswith('.md'):
            post_mod_time = os.path.getmtime(md_file.path)
            latest_post_mod_time = max(latest_post_mod_time, post_mod_time)

            with open(md_file.path, 'r') as file:
                file_content = file.read()
            front_matter, _ = parse_front_matter(file_content)

            url = front_matter.get('url')
            if not url:
                raise ValueError(f"Missing URL in front matter of {md_file.name}")
            if url in url_set:
                raise ValueError(f"Duplicate URL '{url}' found in {md_file.name}")
            url_set.add(url)

            posts_metadata.append({
                'title': front_matter.get('title', ''),
                'excerpt': front_matter.get('excerpt', ''),
                'date': front_matter.get('date', ''),
                'url': url
            })

    return posts_metadata, latest_post_mod_time, template_mod_time


def get_latest_post_metadata(posts_metadata):
    if posts_metadata:
        # print(max(posts_metadata, key=lambda x: x['date']))
        return max(posts_metadata, key=lambda x: x['date'])
    return None


def setup_output_directory(full_rebuild):
    if full_rebuild:
        logging.info(f"Full rebuild: clearing folder {OUTPUT_DIR}...")
        if os.path.exists(OUTPUT_DIR):
            shutil.rmtree(OUTPUT_DIR)

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)


def build_landing(posts_metadata, template_mod_time, latest_post_mod_time, full_rebuild=False):
    landing_output_path = os.path.join(OUTPUT_DIR, 'index.html')

    landing_needs_rebuild = (
        full_rebuild or 
        not os.path.exists(landing_output_path) or 
        os.path.getmtime(landing_output_path) < latest_post_mod_time or 
        os.path.getmtime(landing_output_path) < template_mod_time
    )
    
    if landing_needs_rebuild:
        latest_post = get_latest_post_metadata(posts_metadata)
        landing_template = env.get_template('landing.html')
        landing_content = landing_template.render(latest_post=latest_post)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        logging.info("Building: Landing Page")
        with open(landing_output_path, 'w') as file:
            file.write(landing_content)


def build_archive(posts_metadata, template_mod_time, latest_post_mod_time, full_rebuild=False):
    archive_output_path = os.path.join(OUTPUT_DIR, ARCHIVE_URL, 'index.html')

    archive_needs_rebuild = (
        full_rebuild or 
        not os.path.exists(archive_output_path) or 
        os.path.getmtime(archive_output_path) < latest_post_mod_time or 
        os.path.getmtime(archive_output_path) < template_mod_time
    )

    if archive_needs_rebuild:
        posts_metadata.sort(key=lambda x: x['date'], reverse=True)
        archive_template = env.get_template(TEMPLATE_FOR_ARCHIVE)
        archive_content = archive_template.render(posts=posts_metadata)
        os.makedirs(os.path.join(OUTPUT_DIR, ARCHIVE_URL), exist_ok=True)
        logging.info(f"Building: Archive (url: {ARCHIVE_URL})")
        with open(archive_output_path, 'w') as file:
            file.write(archive_content)


def build_posts(template_mod_time):
    for md_file in os.scandir(POSTS_DIR):
        if md_file.name.endswith('.md'):
            source_path = md_file.path
            with open(source_path, 'r') as file:
                file_content = file.read()
            front_matter, md_content = parse_front_matter(file_content)
            html_content = markdown_to_html(md_content)

            output_dir_path = os.path.join(OUTPUT_DIR, front_matter['url'])
            os.makedirs(output_dir_path, exist_ok=True)
            output_file_path = os.path.join(output_dir_path, 'index.html')

            if is_outdated(source_path, output_file_path, template_mod_time):
                logging.info(f"Building: {front_matter['url']}")
                final_output = apply_template(front_matter, html_content)
                with open(output_file_path, 'w') as file:
                    file.write(final_output)


def generate_site(full_rebuild=False):
    start_time = time.time()

    setup_output_directory(full_rebuild)

    try:
        posts_metadata, latest_post_mod_time, template_mod_time = get_metadata()
    except ValueError as e:
        logging.error(e)
        return

    build_landing(posts_metadata, template_mod_time, latest_post_mod_time, full_rebuild)
    build_archive(posts_metadata, template_mod_time, latest_post_mod_time, full_rebuild)
    build_posts(template_mod_time)

    elapsed_time = time.time() - start_time
    logging.info(f"Done in {elapsed_time:.2f} seconds")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Static Site Generator")
    parser.add_argument("--full", action="store_true", help="Perform a full site rebuild")
    args = parser.parse_args()

    generate_site(full_rebuild=args.full)
