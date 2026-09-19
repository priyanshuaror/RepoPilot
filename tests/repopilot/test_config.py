"""Tests for repopilot.config.settings."""

import json

import pytest

from repopilot.config.settings import (
    DEFAULT_CONFIG_FILE,
    RepoPilotConfig,
    api_key_is_available,
    config_from_mapping,
    load_config,
)

YAML = """
repopilot:
  approval_mode: auto
  max_fix_attempts: 5
  test_timeout: 60
  ignored_dirs: [.git, node_modules]
agent:
  step_limit: 40
model:
  model_kwargs: {drop_params: true}
"""


def test_defaults_are_conservative():
    config = RepoPilotConfig()
    assert config.approval_mode == "interactive"
    assert config.max_fix_attempts == 3
    assert config.modifies_repository is True


def test_dry_run_mode_does_not_modify():
    assert RepoPilotConfig(approval_mode="dry-run").modifies_repository is False


def test_invalid_approval_mode_is_rejected():
    with pytest.raises(ValueError):
        RepoPilotConfig(approval_mode="whenever")


def test_negative_fix_attempts_rejected():
    with pytest.raises(ValueError):
        RepoPilotConfig(max_fix_attempts=-1)


def test_load_config_reads_the_repopilot_block(tmp_path):
    path = tmp_path / "repopilot.yaml"
    path.write_text(YAML)

    config = load_config(path)

    assert config.approval_mode == "auto"
    assert config.max_fix_attempts == 5
    assert config.ignored_dirs == [".git", "node_modules"]


def test_load_config_ignores_mini_swe_agent_blocks(tmp_path):
    path = tmp_path / "repopilot.yaml"
    path.write_text(YAML)
    config = load_config(path)
    assert str(config.agent_config_file) == str(path)


def test_cli_overrides_win(tmp_path):
    path = tmp_path / "repopilot.yaml"
    path.write_text(YAML)

    config = load_config(path, approval_mode="dry-run", model_name="anthropic/claude-x")

    assert config.approval_mode == "dry-run"
    assert config.model_name == "anthropic/claude-x"


def test_none_overrides_do_not_clobber_file_values(tmp_path):
    path = tmp_path / "repopilot.yaml"
    path.write_text(YAML)
    assert load_config(path, approval_mode=None).approval_mode == "auto"


def test_missing_config_file_falls_back_to_defaults(tmp_path):
    config = load_config(tmp_path / "nope.yaml")
    assert config.approval_mode == "interactive"


def test_shipped_config_file_is_valid():
    config = load_config(DEFAULT_CONFIG_FILE)
    assert config.approval_mode in {"interactive", "auto", "dry-run"}
    assert config.max_fix_attempts >= 1


def test_unknown_keys_are_ignored():
    config = config_from_mapping({"approval_mode": "auto", "not_a_setting": 1})
    assert config.approval_mode == "auto"


def test_config_is_json_serializable():
    json.dumps(RepoPilotConfig().to_dict())


def test_api_key_detection_reads_the_environment():
    import os

    original = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        assert api_key_is_available() is True
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if original is not None:
            os.environ["ANTHROPIC_API_KEY"] = original


def test_no_secret_is_ever_stored_in_config():
    """The config object must have no field that could hold a key."""
    fields = RepoPilotConfig().to_dict()
    assert not any("key" in name or "token" in name or "secret" in name for name in fields)
