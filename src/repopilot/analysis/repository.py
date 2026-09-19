"""
Phase 2 - repository understanding.

Walks a checkout once and builds a lightweight repository map: what languages
are present, which files are source vs. test vs. configuration, which package
managers and test commands the project uses, and where the entry points are.

This is deliberately *static and deterministic*: no LLM call is needed to find
out that a repository has a ``tests/`` directory and a ``pyproject.toml``. The
expensive reasoning is saved for the stages that actually need judgement.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from repopilot.config.settings import DEFAULT_IGNORED_DIRS

#: File extension -> language name. Only extensions we can say something useful about.
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Shell",
}

#: Config filename -> the package manager / toolchain it implies.
PACKAGE_MANAGER_BY_FILE: dict[str, str] = {
    "pyproject.toml": "pip/pyproject",
    "setup.py": "setuptools",
    "setup.cfg": "setuptools",
    "requirements.txt": "pip",
    "Pipfile": "pipenv",
    "poetry.lock": "poetry",
    "uv.lock": "uv",
    "package.json": "npm",
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
    "Cargo.toml": "cargo",
    "go.mod": "go modules",
    "Gemfile": "bundler",
    "pom.xml": "maven",
    "build.gradle": "gradle",
}

CONFIG_FILENAMES: frozenset[str] = frozenset(
    {
        *PACKAGE_MANAGER_BY_FILE,
        "tox.ini",
        "pytest.ini",
        "Makefile",
        "makefile",
        "noxfile.py",
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        ".pre-commit-config.yaml",
        "mkdocs.yml",
        "tsconfig.json",
    }
)

ENTRY_POINT_NAMES: frozenset[str] = frozenset(
    {"__main__.py", "main.py", "manage.py", "app.py", "cli.py", "server.py", "index.js", "index.ts", "main.go"}
)

#: Extensions we never want to read or count as source.
BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
        ".pdf", ".zip", ".gz", ".tar", ".whl", ".so", ".dll", ".dylib",
        ".exe", ".bin", ".pyc", ".pyo", ".class", ".jar", ".woff", ".woff2",
        ".ttf", ".eot", ".mp3", ".mp4", ".mov", ".db", ".sqlite",
    }
)

#: Never read a file larger than this during analysis (bytes).
MAX_READ_BYTES = 512_000

_TEST_FILE_RE = re.compile(r"(^test_.*\.py$|.*_test\.py$|.*\.test\.[jt]sx?$|.*\.spec\.[jt]sx?$|^.*_test\.go$)")


@dataclass
class RepositoryAnalysis:
    """A lightweight, serializable map of a repository."""

    root: Path
    languages: dict[str, int] = field(default_factory=dict)
    source_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    important_directories: list[str] = field(default_factory=list)
    total_files: int = 0
    skipped_files: int = 0

    @property
    def primary_language(self) -> str | None:
        """The language with the most files, or None for an empty/unknown repo."""
        return max(self.languages, key=lambda k: self.languages[k]) if self.languages else None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["root"] = str(self.root)
        data["primary_language"] = self.primary_language
        return data


def iter_repository_files(
    root: Path, *, ignored_dirs: Iterable[str] = DEFAULT_IGNORED_DIRS
) -> Iterable[Path]:
    """Yield every interesting file under ``root``, skipping ignored directories.

    Ignored directories are pruned during the walk (not filtered afterwards), so
    a ``node_modules`` with 40k files costs nothing.
    """
    root = Path(root)
    ignored = set(ignored_dirs)
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in ignored:
                    stack.append(entry)
            elif entry.is_file():
                yield entry


def is_test_path(relative_path: str) -> bool:
    """True if a path looks like a test file by name or by directory."""
    parts = Path(relative_path).parts
    if any(part in {"tests", "test", "__tests__", "testing"} for part in parts[:-1]):
        return True
    return bool(_TEST_FILE_RE.match(Path(relative_path).name))


def read_text_safely(path: Path, *, max_bytes: int = MAX_READ_BYTES) -> str:
    """Read a text file, returning "" for binary, oversized, or unreadable files."""
    try:
        if path.suffix.lower() in BINARY_EXTENSIONS or path.stat().st_size > max_bytes:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _detect_test_commands(root: Path, config_files: list[str], has_python_tests: bool) -> list[str]:
    """Guess how this project runs its tests, from its config files."""
    commands: list[str] = []
    names = {Path(name).name for name in config_files}

    pyproject = root / "pyproject.toml"
    pyproject_text = read_text_safely(pyproject) if pyproject.is_file() else ""
    if has_python_tests or "pytest" in pyproject_text or "pytest.ini" in names or "tox.ini" in names:
        commands.append("python -m pytest")
    if "package.json" in names:
        package_text = read_text_safely(root / "package.json")
        if '"test"' in package_text:
            commands.append("npm test")
    if "Cargo.toml" in names:
        commands.append("cargo test")
    if "go.mod" in names:
        commands.append("go test ./...")
    if "Makefile" in names or "makefile" in names:
        makefile_text = read_text_safely(root / "Makefile") or read_text_safely(root / "makefile")
        if re.search(r"^test\s*:", makefile_text, re.MULTILINE):
            commands.append("make test")
    return commands


def _important_directories(relative_paths: list[str], limit: int = 8) -> list[str]:
    """Top-level (and src/*) directories holding the most tracked files."""
    counter: Counter[str] = Counter()
    for rel in relative_paths:
        parts = Path(rel).parts
        if len(parts) < 2:
            continue
        top = parts[0]
        key = f"{parts[0]}/{parts[1]}" if top in {"src", "lib", "packages"} and len(parts) > 2 else top
        counter[key] += 1
    return [name for name, _ in counter.most_common(limit)]


def analyze_repository(
    root: Path, *, ignored_dirs: Iterable[str] = DEFAULT_IGNORED_DIRS
) -> RepositoryAnalysis:
    """Build a :class:`RepositoryAnalysis` for the checkout at ``root``.

    One filesystem walk, no file contents read except for a handful of known
    config files, so this stays fast even on large repositories.
    """
    root = Path(root)
    analysis = RepositoryAnalysis(root=root)
    languages: Counter[str] = Counter()
    all_relative: list[str] = []

    for path in iter_repository_files(root, ignored_dirs=ignored_dirs):
        relative = path.relative_to(root).as_posix()
        analysis.total_files += 1

        suffix = path.suffix.lower()
        if suffix in BINARY_EXTENSIONS:
            analysis.skipped_files += 1
            continue

        all_relative.append(relative)

        if path.name in CONFIG_FILENAMES:
            analysis.config_files.append(relative)

        language = LANGUAGE_BY_EXTENSION.get(suffix)
        if language is None:
            continue
        languages[language] += 1

        if is_test_path(relative):
            analysis.test_files.append(relative)
        else:
            analysis.source_files.append(relative)
            if path.name in ENTRY_POINT_NAMES:
                analysis.entry_points.append(relative)

    analysis.languages = dict(languages.most_common())
    analysis.source_files.sort()
    analysis.test_files.sort()
    analysis.config_files.sort()
    analysis.entry_points.sort()
    analysis.package_managers = sorted(
        {
            PACKAGE_MANAGER_BY_FILE[Path(name).name]
            for name in analysis.config_files
            if Path(name).name in PACKAGE_MANAGER_BY_FILE
        }
    )
    has_python_tests = any(name.endswith(".py") for name in analysis.test_files)
    analysis.test_commands = _detect_test_commands(root, analysis.config_files, has_python_tests)
    analysis.important_directories = _important_directories(all_relative)
    return analysis
