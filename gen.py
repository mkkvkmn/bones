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
ARCHIVE_URL = 'a'


# Initialize Jinja environment once and load templates
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
post_template = env.get_template(TEMPLATE_FOR_POST)

def parse_front_matter(file_content, filename):
    pattern = re.compile(r'^---\s+(.*?)\s+---\s+(.*)', re.DOTALL)
    match = pattern.match(file_content)
    if match:
        front_matter = yaml.safe_load(match.group(1))
        content = match.group(2)

        if 'url' not in front_matter:
            # Generate URL from filename if not present in front matter
            base_filename = filename.rsplit('.', 1)[0]
            url = re.sub(r'[^\w\s-]', '', base_filename).replace(' ', '-').lower()
            front_matter['url'] = url

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


def collect_posts_metadata():
    posts_metadata = []
    for md_file in os.scandir(POSTS_DIR):
        if md_file.name.endswith('.md'):
            with open(md_file.path, 'r') as file:
                file_content = file.read()
                front_matter, _ = parse_front_matter(file_content, md_file.name)
                posts_metadata.append({
                    'title': front_matter.get('title', ''),
                    'date': front_matter.get('date', ''),
                    'url': front_matter.get('url', '')
                })
    return posts_metadata

def build_archive_page(posts_metadata):
    posts_metadata.sort(key=lambda x: x['date'], reverse=True)
    archive_template = env.get_template(TEMPLATE_FOR_ARCHIVE)
    archive_content = archive_template.render(posts=posts_metadata)
    archive_output_dir = os.path.join(OUTPUT_DIR, ARCHIVE_URL)
    os.makedirs(archive_output_dir, exist_ok=True)
    logging.info(f"Building: Archive (url: {ARCHIVE_URL})")
    with open(os.path.join(archive_output_dir, 'index.html'), 'w') as file:
        file.write(archive_content)


def generate_site(full_rebuild=False):
    start_time = time.time()

    if full_rebuild:
        logging.info("Full rebuild. Clearing the output directory...")
        if os.path.exists(OUTPUT_DIR):
            shutil.rmtree(OUTPUT_DIR)

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    # Build archive page
    posts_metadata = collect_posts_metadata()
    build_archive_page(posts_metadata)

    template_mod_time = max(os.path.getmtime(f.path)
                            for f in os.scandir(TEMPLATES_DIR)
                            if f.is_file())

    for md_file in os.scandir(POSTS_DIR):
        if md_file.name.endswith('.md'):
            source_path = md_file.path
            with open(source_path, 'r') as file:
                file_content = file.read()
                front_matter, md_content = parse_front_matter(file_content, md_file.name)
                html_content = markdown_to_html(md_content)
                final_output = apply_template(front_matter, html_content)

                output_dir_path = os.path.join(OUTPUT_DIR, front_matter['url'])
                os.makedirs(output_dir_path, exist_ok=True)
                output_file_path = os.path.join(output_dir_path, 'index.html')

                if is_outdated(source_path, output_file_path, template_mod_time):
                    logging.info(f"Building: {front_matter['url']}")
                    with open(output_file_path, 'w') as file:
                        file.write(final_output)

    elapsed_time = time.time() - start_time
    logging.info(f"Done in {elapsed_time:.2f} seconds")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Static Site Generator")
    parser.add_argument("--full", action="store_true", help="Perform a full site rebuild")
    args = parser.parse_args()

    generate_site(full_rebuild=args.full)
