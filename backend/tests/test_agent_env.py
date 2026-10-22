import pytest

from app.core.config import agent_global_env, get_settings, parse_env_lines
from app.services.sandbox import SandboxService
from app.services.sandbox_providers.base import SandboxProvider


def test_parse_env_lines_skips_comments_and_malformed_lines() -> None:
    raw = """
    # Claude proxy
    ANTHROPIC_BASE_URL=https://proxy.example
    ANTHROPIC_CUSTOM_HEADERS=X-Team: a=b
    not-a-pair
    =missing-key
    """
    assert parse_env_lines(raw) == {
        "ANTHROPIC_BASE_URL": "https://proxy.example",
        "ANTHROPIC_CUSTOM_HEADERS": "X-Team: a=b",
    }


def test_agent_global_env_forwards_set_passthrough_vars_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "AGENT_ENV", "EXTRA=1\nANTHROPIC_AUTH_TOKEN=line")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "from-env")
    # Compose declares unset vars as empty strings — those must not leak through
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    env = agent_global_env(settings)

    assert env == {
        "ANTHROPIC_BASE_URL": "https://proxy.example",
        # An explicit AGENT_ENV line wins over the passthrough value
        "ANTHROPIC_AUTH_TOKEN": "line",
        "EXTRA": "1",
    }


def test_user_env_vars_override_global_agent_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "AGENT_ENV", "ANTHROPIC_BASE_URL=https://global")

    env = SandboxService.build_env_vars(
        [{"key": "ANTHROPIC_BASE_URL", "value": "https://mine"}],
        None,
        "docker",
    )

    assert env["ANTHROPIC_BASE_URL"] == "https://mine"


def test_pty_command_inherits_env_by_name_without_values() -> None:
    cmd = SandboxProvider.build_pty_shell_command(
        "main", "bash", inherit_env_names=["ANTHROPIC_AUTH_TOKEN"]
    )

    assert "set -g update-environment" in cmd
    assert "SSH_AUTH_SOCK" in cmd
    assert "ANTHROPIC_AUTH_TOKEN" in cmd
    assert "=" not in cmd.split("update-environment", 1)[1].split("\;", 1)[0]
