"""
Post-install patch script for HiImage backend.

Usage:
    # Activate venv first, then:
    python scripts/post_install.py

Fixes incompatibilities that cannot be resolved via requirements.txt alone:
1. basicsr 1.4.2 imports from removed torchvision.transforms.functional_tensor
   -> Patch basicsr/data/degradations.py to try the new location first
"""

import os
import sys
import textwrap


def find_site_packages():
    """Auto-detect site-packages directory for the current Python interpreter."""
    for p in sys.path:
        if p.endswith("site-packages"):
            return p
    print("[ERROR] Cannot find site-packages directory. Are you using the correct Python?")
    sys.exit(1)


PATCHES = [
    {
        "description": "Patch basicsr functional_tensor import for torchvision >= 0.16",
        "rel_path": "basicsr/data/degradations.py",
        "old": "from torchvision.transforms.functional_tensor import rgb_to_grayscale",
        "new": textwrap.dedent("""\
            try:
                from torchvision.transforms.functional_tensor import rgb_to_grayscale
            except ImportError:
                from torchvision.transforms.functional import rgb_to_grayscale
        """).rstrip(),
    },
]


def main():
    site_packages = find_site_packages()
    print(f"[INFO] Using site-packages: {site_packages}\n")

    patched = 0
    for p in PATCHES:
        path = os.path.join(site_packages, p["rel_path"])
        if not os.path.exists(path):
            print(f"[SKIP] {p['description']}  ({path} not found)")
            continue

        with open(path, "r") as f:
            content = f.read()

        if p["new"] in content:
            print(f"[OK]   {p['description']} (already patched)")
            patched += 1
            continue

        if p["old"] not in content:
            print(f"[WARN] {p['description']}  (pattern not found in {path})")
            continue

        content = content.replace(p["old"], p["new"])
        with open(path, "w") as f:
            f.write(content)
        print(f"[PATCHED] {p['description']}")
        patched += 1

    print(f"\nDone: {patched}/{len(PATCHES)} patches applied.")


if __name__ == "__main__":
    main()
