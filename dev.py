"""Serve a site's dev build locally and, with --watch, rebuild it when its sources change.

Run from the bones folder (build.sh does this):
    python dev.py --sites-folder ../sites --site-name my-site [--watch]

The address and output folder come from the site's config/build.yml (envs.dev.url / .output).
"""

import argparse
import functools
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

BONES_DIR = Path(__file__).resolve().parent
THEMES_DIR = BONES_DIR / "themes"
# Files gen.py writes into the site folder on every build (besides the env outputs).
GENERATED_IN_SITE = [Path("config") / "setup.json", Path("templates") / "partials" / "auto-generated"]
QUIET_SECONDS = 0.4  # wait for a burst of saves to settle before rebuilding


def load_envs(site_dir: Path) -> dict:
    build_yml = site_dir / "config" / "build.yml"
    with open(build_yml, encoding="utf-8") as f:
        envs = (yaml.safe_load(f) or {}).get("envs", {})
    if not isinstance(envs.get("dev"), dict) or not envs["dev"].get("url") or not envs["dev"].get("output"):
        raise SystemExit(f"{build_yml}: envs.dev.url and envs.dev.output are required")
    return envs


def run_build(args: argparse.Namespace) -> bool:
    cmd = [sys.executable, "gen.py", "--env", "dev", "--site-name", args.site_name, "--sites-folder", args.sites_folder]
    started = time.time()
    ok = subprocess.run(cmd, cwd=BONES_DIR).returncode == 0
    status = "built" if ok else "BUILD FAILED"
    print(f"[dev] {status} in {time.time() - started:.1f}s", flush=True)
    return ok


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # only show errors
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)


def serve(out_dir: Path, url: str) -> ThreadingHTTPServer:
    parsed = urlparse(url)
    host, port = parsed.hostname or "localhost", parsed.port or 80
    handler = functools.partial(QuietHandler, directory=str(out_dir))
    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError as e:
        raise SystemExit(f"Cannot serve on {host}:{port}: {e}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[dev] serving {out_dir} at {url}", flush=True)
    return server


class ChangeFlag(FileSystemEventHandler):
    def __init__(self, roots: list, ignored: list) -> None:
        self.roots = roots
        self.ignored = ignored
        self.last_change = 0.0
        self.changed_path = ""

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in ("opened", "closed", "closed_no_write") or event.is_directory:
            return
        for raw in (event.src_path, getattr(event, "dest_path", "")):
            if not raw:
                continue
            # absolute(), not resolve(): on Windows a deleted file resolves to C:\$Extend\$Deleted\...
            path = Path(raw).absolute()
            if not any(r in path.parents for r in self.roots):
                continue
            if ".git" in path.parts or any(path == i or i in path.parents for i in self.ignored):
                continue
            self.last_change = time.time()
            self.changed_path = str(path)


def watch(args: argparse.Namespace, site_dir: Path, envs: dict) -> None:
    ignored = [site_dir / p for p in GENERATED_IN_SITE]
    ignored += [(BONES_DIR / env["output"]).resolve() for env in envs.values() if isinstance(env, dict) and env.get("output")]
    roots = [site_dir, THEMES_DIR]
    flag = ChangeFlag(roots, ignored)
    observer = Observer()
    for folder in roots:
        observer.schedule(flag, str(folder), recursive=True)
    observer.start()
    print(f"[dev] watching {site_dir} and {THEMES_DIR} (Ctrl+C to stop)", flush=True)
    built_at = time.time()
    try:
        while True:
            time.sleep(0.1)
            if flag.last_change > built_at and time.time() - flag.last_change >= QUIET_SECONDS:
                print(f"[dev] changed: {flag.changed_path}", flush=True)
                built_at = time.time()
                run_build(args)
    finally:
        observer.stop()
        observer.join()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve (and watch) a bones dev build")
    parser.add_argument("--sites-folder", required=True, help="Sites folder relative to bones, e.g. ../sites")
    parser.add_argument("--site-name", required=True)
    parser.add_argument("--watch", action="store_true", help="Rebuild when the site or theme changes")
    args = parser.parse_args()

    site_dir = (BONES_DIR / args.sites_folder / args.site_name).resolve()
    envs = load_envs(site_dir)
    out_dir = (BONES_DIR / envs["dev"]["output"]).resolve()
    if not (out_dir / "index.html").is_file():
        raise SystemExit(f"No dev build in {out_dir}; build first")

    serve(out_dir, envs["dev"]["url"])
    try:
        if args.watch:
            watch(args, site_dir, envs)
        else:
            print("[dev] Ctrl+C to stop", flush=True)
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[dev] stopped", flush=True)


if __name__ == "__main__":
    main()
