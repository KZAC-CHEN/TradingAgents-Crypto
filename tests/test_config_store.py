"""本地配置存储的安全性与写回行为测试。"""

from __future__ import annotations

import os

import pytest
from dotenv import dotenv_values

from tradingagents.config_store import ConfigStore, ConfigValidationError


@pytest.fixture(autouse=True)
def clean_managed_environment():
    """避免开发机环境变量影响配置文件断言。"""
    names = (
        "OPENAI_API_KEY",
        "ROOTDATA_API_KEY",
        "COINGECKO_API_KEY",
        "COINGECKO_API_PLAN",
        "TRADINGAGENTS_LLM_PROVIDER",
        "TRADINGAGENTS_CRYPTO_NEWS_ARTICLE_LIMIT",
    )
    originals = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ.pop(name, None)
    yield
    for name, value in originals.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def test_public_snapshot_never_returns_secret(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        'OPENAI_API_KEY="sk-private-value"\nTRADINGAGENTS_LLM_PROVIDER="openai"\n',
        encoding="utf-8",
    )

    snapshot = ConfigStore(env_path).public_snapshot()
    serialized = repr(snapshot)

    assert "sk-private-value" not in serialized
    fields = {
        field["name"]: field
        for group in snapshot["groups"]
        for field in group["fields"]
    }
    assert fields["OPENAI_API_KEY"]["configured"] is True
    assert fields["OPENAI_API_KEY"]["value"] == ""
    assert fields["TRADINGAGENTS_LLM_PROVIDER"]["value"] == "openai"


def test_apply_changes_preserves_unrelated_lines_and_round_trips_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# 原有注释\nOTHER=value\nOPENAI_API_KEY=old\n",
        encoding="utf-8",
    )
    store = ConfigStore(env_path)

    store.apply_changes(
        {
            "OPENAI_API_KEY": 'new-$value-"quoted"',
            "TRADINGAGENTS_LLM_PROVIDER": "deepseek",
        }
    )

    content = env_path.read_text(encoding="utf-8")
    values = dotenv_values(env_path)
    assert "# 原有注释" in content
    assert "OTHER=value" in content
    assert values["OPENAI_API_KEY"] == 'new-$value-"quoted"'
    assert values["TRADINGAGENTS_LLM_PROVIDER"] == "deepseek"
    assert os.environ["OPENAI_API_KEY"] == 'new-$value-"quoted"'


def test_delete_removes_only_requested_managed_value(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ROOTDATA_API_KEY=secret\nOTHER=value\n",
        encoding="utf-8",
    )
    store = ConfigStore(env_path)
    os.environ["ROOTDATA_API_KEY"] = "secret"

    store.apply_changes(deletes=["ROOTDATA_API_KEY"])

    content = env_path.read_text(encoding="utf-8")
    assert "ROOTDATA_API_KEY" not in content
    assert "OTHER=value" in content
    assert "ROOTDATA_API_KEY" not in os.environ


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("UNKNOWN_KEY", "value", "不支持的配置项"),
        ("JIN10_API_URL", "file:///tmp/token", r"HTTP\(S\)"),
        ("JIN10_API_URL", "https://user:secret@example.com/feed", r"HTTP\(S\)"),
        ("JIN10_API_URL", "https://example.com/bad path", r"HTTP\(S\)"),
        ("TRADINGAGENTS_LLM_PROVIDER", "unknown", "不支持的选项"),
        ("TRADINGAGENTS_ROOTDATA_FALLBACK_MODE", "scrape", "不支持的选项"),
        ("COINGECKO_API_PLAN", "enterprise", "不支持的选项"),
        ("TRADINGAGENTS_CRYPTO_NEWS_ARTICLE_LIMIT", "1.5", "整数"),
        ("OPENAI_API_KEY", "first\nsecond", "格式无效"),
    ],
)
def test_invalid_values_are_rejected(tmp_path, name, value, message):
    store = ConfigStore(tmp_path / ".env")

    with pytest.raises(ConfigValidationError, match=message):
        store.apply_changes({name: value})

    assert not store.env_path.exists()


def test_update_and_delete_same_field_is_rejected(tmp_path):
    store = ConfigStore(tmp_path / ".env")

    with pytest.raises(ConfigValidationError, match="不能同时更新和清除"):
        store.apply_changes({"OPENAI_API_KEY": "new"}, ["OPENAI_API_KEY"])
