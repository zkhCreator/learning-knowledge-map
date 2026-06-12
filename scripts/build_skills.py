"""
File: scripts/build_skills.py

Purpose:
    Vendor the shared Python runtime (src/) into each skill that imports it,
    so the skill keeps working after `npx skills add` copies its directory out
    of this repository (docs/22).

Responsibilities:
    - Copy src/ (minus __pycache__) and requirements.txt into
      skills/<skill>/_core/ for every Python-runtime skill
    - Write _core/.manifest.json with a sha256 per vendored file
    - --check: verify the committed vendor matches the current src/ exactly
      (drift gate, wired into tests/test_skill_packaging.py)

What this file does NOT do:
    - Build the web frontend (web/dist is built separately and committed)
    - Publish anything to a registry
    - Touch skills that are stdlib-only by design (print-learning-graph)

Inputs:  --root (repo root, default: this file's parent's parent),
         --skill (repeatable; default: all vendored skills), --check
Outputs: skills/<skill>/_core/ trees, or a non-zero exit on drift
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

# Skills whose scripts import src.* and therefore need the vendored runtime.
VENDORED_SKILLS = ["decompose-learning-goal", "exam-start", "serve-learning-graph"]

CORE_DIR_NAME = "_core"
MANIFEST_NAME = ".manifest.json"


def _iter_src_files(src_root: Path):
    for path in sorted(src_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_manifest(root: Path) -> dict[str, str]:
    """relpath (under _core/) -> sha256 of the current source of truth."""
    manifest: dict[str, str] = {}
    src_root = root / "src"
    for path in _iter_src_files(src_root):
        rel = Path("src") / path.relative_to(src_root)
        manifest[rel.as_posix()] = _sha256(path)
    requirements = root / "requirements.txt"
    if requirements.exists():
        manifest["requirements.txt"] = _sha256(requirements)
    return manifest


def build(root: Path, skills: list[str]) -> None:
    manifest = _expected_manifest(root)
    for skill in skills:
        core = root / "skills" / skill / CORE_DIR_NAME
        if core.exists():
            shutil.rmtree(core)
        for rel in manifest:
            target = core / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, target)
        (core / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"✓ vendored {len(manifest)} files into skills/{skill}/{CORE_DIR_NAME}/")


def check(root: Path, skills: list[str]) -> int:
    expected = _expected_manifest(root)
    failures: list[str] = []
    for skill in skills:
        core = root / "skills" / skill / CORE_DIR_NAME
        if not core.exists():
            failures.append(f"skills/{skill}: missing {CORE_DIR_NAME}/ (never built)")
            continue
        for rel, digest in expected.items():
            vendored = core / rel
            if not vendored.exists():
                failures.append(f"skills/{skill}: missing {rel}")
            elif _sha256(vendored) != digest:
                failures.append(f"skills/{skill}: stale {rel}")
        manifest_file = core / MANIFEST_NAME
        recorded = (
            json.loads(manifest_file.read_text(encoding="utf-8"))
            if manifest_file.exists() else {}
        )
        for rel in recorded:
            if rel not in expected:
                failures.append(f"skills/{skill}: orphan vendored file {rel}")
    if failures:
        print("Vendored runtime is out of sync with src/ — run: python scripts/build_skills.py")
        for line in failures:
            print(f"  ✗ {line}")
        return 1
    print(f"✓ vendor in sync for: {', '.join(skills)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help="Repo root (default: auto)")
    parser.add_argument("--skill", action="append", dest="skills",
                        help="Limit to one skill (repeatable)")
    parser.add_argument("--check", action="store_true",
                        help="Verify vendor matches src/ instead of building")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    skills = args.skills or VENDORED_SKILLS

    if args.check:
        return check(root, skills)
    build(root, skills)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
