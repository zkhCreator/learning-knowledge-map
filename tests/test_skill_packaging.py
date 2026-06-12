"""
tests/test_skill_packaging.py

Tests for the installable-skill packaging mechanism (docs/22):
    - scripts/build_skills.py vendors src/ into each Python-runtime skill's
      _core/ directory with a sha256 manifest, and --check detects drift
    - scripts/_bootstrap.py resolves the vendored runtime in an installed
      (copied-out) skill and falls back to the repo root in development
    - the committed vendor in this repo is in sync with src/ (drift gate)

No LLM calls; subprocesses run plain Python imports only.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_skills.py"
VENDORED_SKILLS = ["decompose-learning-goal", "exam-start", "serve-learning-graph"]


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args], cwd=cwd or ROOT, capture_output=True, text=True
    )


def _make_fake_repo(tmp_path: Path) -> Path:
    """Minimal repo shape: src/ package + requirements.txt + one target skill."""
    repo = tmp_path / "repo"
    (repo / "src" / "sub").mkdir(parents=True)
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "sub" / "mod.py").write_text("VALUE = 42\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("rich\n", encoding="utf-8")
    scripts = repo / "skills" / "exam-start" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "skills" / "exam-start" / "scripts" / "_bootstrap.py", scripts)
    return repo


class TestBuildSkills:
    def test_build_vendors_src_and_manifest(self, tmp_path):
        repo = _make_fake_repo(tmp_path)
        result = _run([str(BUILD_SCRIPT), "--root", str(repo), "--skill", "exam-start"])
        assert result.returncode == 0, result.stderr

        core = repo / "skills" / "exam-start" / "_core"
        assert (core / "src" / "sub" / "mod.py").read_text(encoding="utf-8") == "VALUE = 42\n"
        assert (core / "requirements.txt").exists()
        assert (core / ".manifest.json").exists()

    def test_check_passes_when_in_sync_and_fails_on_drift(self, tmp_path):
        repo = _make_fake_repo(tmp_path)
        _run([str(BUILD_SCRIPT), "--root", str(repo), "--skill", "exam-start"])

        ok = _run([str(BUILD_SCRIPT), "--check", "--root", str(repo), "--skill", "exam-start"])
        assert ok.returncode == 0, ok.stdout + ok.stderr

        # Drift: src changes without re-vendoring must fail the check.
        (repo / "src" / "sub" / "mod.py").write_text("VALUE = 43\n", encoding="utf-8")
        drift = _run([str(BUILD_SCRIPT), "--check", "--root", str(repo), "--skill", "exam-start"])
        assert drift.returncode != 0

    def test_committed_vendor_is_in_sync(self):
        """Drift gate for this repo: editing src/ requires re-running the build."""
        result = _run([str(BUILD_SCRIPT), "--check"])
        assert result.returncode == 0, (
            "Vendored _core/ is out of sync with src/. "
            "Run: python scripts/build_skills.py\n" + result.stdout + result.stderr
        )


class TestBootstrapResolution:
    def test_installed_skill_imports_vendored_runtime(self, tmp_path):
        """Copy a single skill directory out of the repo (what `npx skills add`
        does) and verify the vendored runtime is importable with no repo around."""
        install_dir = tmp_path / "installed" / "exam-start"
        shutil.copytree(
            ROOT / "skills" / "exam-start", install_dir,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        probe = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, r'{install_dir / 'scripts'}'); "
            "import _bootstrap; "
            "import src.infrastructure.config as c; "
            "import src.domain.dag; "
            "print('vendored-ok', Path(c.__file__).resolve())"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert "vendored-ok" in result.stdout
        # Must import from the install dir, not from this repo's src/.
        assert str(install_dir) in result.stdout

    def test_repo_mode_falls_back_to_repo_root(self, tmp_path):
        """Without _core/, _bootstrap resolves the repo root three levels up."""
        repo = _make_fake_repo(tmp_path)
        probe = (
            "import sys; "
            f"sys.path.insert(0, r'{repo / 'skills' / 'exam-start' / 'scripts'}'); "
            "import _bootstrap; "
            "from src.sub.mod import VALUE; "
            "print('repo-ok', VALUE)"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert "repo-ok 42" in result.stdout

    @pytest.mark.parametrize("skill", VENDORED_SKILLS)
    def test_every_vendored_skill_ships_bootstrap_and_core(self, skill):
        assert (ROOT / "skills" / skill / "scripts" / "_bootstrap.py").exists()
        assert (ROOT / "skills" / skill / "_core" / "src" / "__init__.py").exists()
        assert not (ROOT / "skills" / skill / "_core" / "SKILL.md").exists()
