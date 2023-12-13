import os
import re
import markdown
import yaml
import logging
import time
from jinja2 import Environment, FileSystemLoader

# Logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Directory configurations
OUTPUT_DIR = '_public'          # Output directory for generated HTML files
POSTS_DIR = 'posts'             # Directory containing Markdown posts
TEMPLATES_DIR = 'templates'     # Directory for Jinja2 templates
TEMPLATE_FOR_POST = 'post.html'

# Initialize Jinja environment once and load templates
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
post_template = env.get_template(TEMPLATE_FOR_POST)

def parse_front_matter(file_content):
    """
    Parses the YAML front matter and returns a dictionary of metadata
    and the Markdown content without the front matter.
    """
    # get front matter from post between --- and ---
    pattern = re.compile(r'^---\s+(.*?)\s+---\s+(.*)', re.DOTALL)
    match = pattern.match(file_content)
    if match:
        front_matter = yaml.safe_load(match.group(1))
        content = match.group(2)
        return front_matter, content
    else:
        return {}, file_content


def markdown_to_html(md_content):
    return markdown.markdown(md_content)


def apply_template(front_matter, html_content):
    # Use the pre-loaded template
    return post_template.render(post=front_matter, content=html_content)


def is_outdated(source_path, output_path, template_mod_time):
    if not os.path.exists(output_path):
        return True
    source_mod_time = os.path.getmtime(source_path)
    output_mod_time = os.path.getmtime(output_path)
    return source_mod_time > output_mod_time or template_mod_time > output_mod_time


def generate_site():
    start_time = time.time()  # Start timing the build process

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    template_mod_time = max(os.path.getmtime(f.path)
                            for f in os.scandir(TEMPLATES_DIR)
                            if f.is_file())

    for md_file in os.scandir(POSTS_DIR):
        if md_file.name.endswith('.md'):
            source_path = md_file.path
            output_path = f'{OUTPUT_DIR}/{md_file.name.replace(".md", ".html")}'

            if is_outdated(source_path, output_path, template_mod_time):
                logging.info(f"Building: {md_file.name}")
                with open(source_path, 'r') as file:
                    file_content = file.read()
                    front_matter, md_content = parse_front_matter(file_content)
                    html_content = markdown_to_html(md_content)
                    final_output = apply_template(front_matter, html_content)

                    with open(output_path, 'w') as file:
                        file.write(final_output)

    elapsed_time = time.time() - start_time 
    logging.info(f"Done in {elapsed_time:.2f} seconds")


if __name__ == "__main__":
    generate_site()