# Bones - When Flesh and Muscles Are Too Much
Simple static site generator.
 - Landing page
 - About page
 - Archive page
 - Markdown posts

## Install
pip install -r requirements.txt

## Venv - optional
pip install virtualenv
python3 -m venv venv
source venv/bin/activate

## Run
python3 gen.py 
- --full -> regenerate everything
- --dev -> use dev url

### Server
in _public run: python3 -m http.server