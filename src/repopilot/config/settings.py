"""
RepoPilot run configuration.

A single, explicit, serializable settings object that every stage of the pipeline
reads from, so behaviour (ignored directories, repair budget, approval mode,
timeouts, output location) is configured in one place rather than scattered as
magic numbers through the code.

Secrets are deliberately *not* part of this object: model API keys are read from
the environment by the underlying model layer (litellm / mini-swe-agent), never
from `repopilot.yaml`.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_FILE = Path(__file__).resolve().parent / "repopilot.yaml"

#: Directories that are never interesting for repository understanding: they are
#: generated, vendored, or version-control internals.
DEFAULT_IGNORED_DIRS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    ".vscode",
    "site-packages",
    "target",
    ".next",
)

#: Approval modes for the human-in-the-loop checkpoint (see repopilot.approval).
APPROVAL_MODES = ("interactive", "auto", "dry-run")


@dataclass
class RepoPilotConfig:
    """Everything that controls a RepoPilot run.

    Attributes
    ----------
    model_name: Model identifier passed through to mini-swe-agent (e.g.
        ``anthropic/claude-sonnet-4-5-20250929``). ``None`` means "use whatever
        mini-swe-agent is already configured with".
    repository / branch: The target of the run. Usually supplied on the CLI.
    approval_mode: ``interactive`` asks a human, ``auto`` approves every plan,
        ``dry-run`` never approves (analysis/planning only, no modification).
    max_fix_attempts: Hard upper bound on repair iterations after a failing test
        run. Prevents an unbounded autonomous loop.
    test_timeout: Seconds before a single test command is killed.
    run_timeout: Soft wall-clock budget for the whole verification phase.
    test_commands: Explicit test commands. Empty means "detect from the repo".
    ignored_dirs: Directories skipped during repository analysis.
    output_dir: Where run artifacts (trajectory, diff, report) are written.
    agent_config_file: Path to the YAML holding the agent/model/environment
        blocks handed to mini-swe-agent.
    """

    model_name: str | None = None
    repository: str | None = None
    branch: str | None = None
    approval_mode: str = "interactive"
    max_fix_attempts: int = 3
    test_timeout: int = 900
    run_timeout: int = 3600
    clone_depth: int | None = 1
    test_commands: list[str] = field(default_factory=list)
    ignored_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORED_DIRS))
    output_dir: Path | None = None
    agent_config_file: Path = DEFAULT_CONFIG_FILE
    run_broader_tests: bool = True

    def __post_init__(self) -> None:
        if self.approval_mode not in APPROVAL_MODES:
            raise ValueError(f"approval_mode must be one of {APPROVAL_MODES}, got {self.approval_mode!r}")
        if self.max_fix_attempts < 0:
            raise ValueError("max_fix_attempts must be >= 0")
        if self.output_dir is not None:
            self.output_dir = Path(self.output_dir)
        self.agent_config_file = Path(self.agent_config_file)

    @property
    def modifies_repository(self) -> bool:
        """False in dry-run mode, where RepoPilot only analyses and plans."""
        return self.approval_mode != "dry-run"

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view (paths become strings) for the run report."""
        data = asdict(self)
        data["output_dir"] = str(self.output_dir) if self.output_dir else None
        data["agent_config_file"] = str(self.agent_config_file)
        return data


def _known_field_names() -> set[str]:
    return {f.name for f in fields(RepoPilotConfig)}


def config_from_mapping(data: dict[str, Any], **overrides: Any) -> RepoPilotConfig:
    """Build a config from a plain mapping, ignoring unknown keys.

    Unknown keys are ignored rather than fatal so that `repopilot.yaml` can also
    carry the ``agent:`` / ``model:`` / ``environment:`` blocks that belong to
    mini-swe-agent, not to RepoPilot.
    """
    known = _known_field_names()
    kwargs = {key: value for key, value in (data or {}).items() if key in known}
    kwargs.update({key: value for key, value in overrides.items() if value is not None})
    return RepoPilotConfig(**kwargs)


def load_config(path: Path | None = None, **overrides: Any) -> RepoPilotConfig:
    """Load RepoPilot settings from a YAML file, applying CLI overrides on top.

    The ``repopilot:`` block of the YAML holds RepoPilot's own settings; the rest
    of the file is left for mini-swe-agent. Missing files fall back to defaults,
    so RepoPilot still works with no configuration at all.
    """
    import yaml  # imported lazily: keeps this module importable without PyYAML

    config_path = Path(path) if path else DEFAULT_CONFIG_FILE
    raw: dict[str, Any] = {}
    if config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        raw = loaded.get("repopilot", {}) or {}
    overrides.setdefault("agent_config_file", config_path)
    return config_from_mapping(raw, **overrides)


def api_key_is_available() -> bool:
    """True if at least one recognised model API key is present in the environment.

    RepoPilot never stores or logs the key itself - it only reports whether the
    LLM-backed stages can run at all, so the CLI can fail early with a clear
    message instead of deep inside the agent loop.
    """
    return any(
        os.environ.get(name)
        for name in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "GEMINI_API_KEY",
            "AZURE_API_KEY",
        )
    )
