"""Build the React dashboard and copy it into ai_router/static."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
DIST_DIR = WEB_DIR / "dist"
STATIC_DIR = ROOT / "ai_router" / "static"


def main() -> int:
    if not WEB_DIR.exists():
        print(f"Missing web directory: {WEB_DIR}", file=sys.stderr)
        return 1

    npm = shutil.which("npm")
    if not npm:
        print("npm not found. Install Node.js 20+ first.", file=sys.stderr)
        return 1

    subprocess.run([npm, "run", "build"], cwd=WEB_DIR, check=True)

    if not DIST_DIR.exists():
        print(f"Build did not create dist directory: {DIST_DIR}", file=sys.stderr)
        return 1

    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    for path in STATIC_DIR.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    for path in DIST_DIR.iterdir():
        target = STATIC_DIR / path.name
        if path.is_dir():
            shutil.copytree(path, target)
        else:
            shutil.copy2(path, target)

    print(f"Built dashboard and copied {DIST_DIR} -> {STATIC_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
