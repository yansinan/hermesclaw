import json
from pathlib import Path

from hermesclaw import AgentRegistry, load_agents_config


def test_load_agents_config_from_file(tmp_path, monkeypatch):
    cfg = {
        "default_route": "beta",
        "groups": {"all": ["alpha", "beta"]},
        "agents": [
            {
                "name": "alpha",
                "host": "127.0.0.1",
                "port": 21001,
                "tag": "[A]",
                "enabled": True,
            },
            {
                "name": "beta",
                "host": "127.0.0.1",
                "port": 21002,
                "tag": "[B]",
                "enabled": True,
            },
        ],
    }
    fp = tmp_path / "agents.json"
    fp.write_text(json.dumps(cfg), encoding="utf-8")

    monkeypatch.setenv("AGENTS_CONFIG_FILE", str(fp))
    loaded = load_agents_config(base_dir=tmp_path)
    reg = AgentRegistry(loaded)

    assert reg.default_route == "beta"
    assert reg.enabled_names == ["alpha", "beta"]
    assert reg.route_targets("both") == ["alpha", "beta"]


def test_load_agents_config_legacy_env_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_CONFIG_FILE", raising=False)
    monkeypatch.delenv("AGENTS_CONFIG", raising=False)
    monkeypatch.setenv("HERMES_PROXY_PORT", "19998")
    monkeypatch.setenv("OPENCLAW_PROXY_PORT", "19999")
    monkeypatch.setenv("HERMES_ENABLED", "true")
    monkeypatch.setenv("OPENCLAW_ENABLED", "false")

    loaded = load_agents_config(base_dir=tmp_path)
    reg = AgentRegistry(loaded)

    assert reg.enabled_names == ["hermes"]
    assert reg.default_route == "hermes"


def test_load_agents_config_prefers_script_dir_agents_json(tmp_path, monkeypatch):
    default_cfg = {
        "default_route": "alpha",
        "agents": [
            {"name": "alpha", "host": "127.0.0.1", "port": 22001, "enabled": True}
        ],
    }
    env_cfg = {
        "default_route": "beta",
        "agents": [
            {"name": "beta", "host": "127.0.0.1", "port": 22002, "enabled": True}
        ],
    }
    (tmp_path / "agents.json").write_text(json.dumps(default_cfg), encoding="utf-8")
    env_fp = tmp_path / "env_agents.json"
    env_fp.write_text(json.dumps(env_cfg), encoding="utf-8")

    monkeypatch.setenv("AGENTS_CONFIG_FILE", str(env_fp))
    loaded = load_agents_config(base_dir=tmp_path)
    reg = AgentRegistry(loaded)

    assert reg.default_route == "alpha"
    assert reg.enabled_names == ["alpha"]


def test_load_agents_config_reads_ilink_settings_from_json(tmp_path, monkeypatch):
    cfg = {
        "ilink_base_url": "https://example.invalid",
        "ilink_token": "tok-json",
        "admin_ilink_uid": "uid-json",
        "agents": [
            {"name": "alpha", "host": "127.0.0.1", "port": 23001, "enabled": True}
        ],
    }
    (tmp_path / "agents.json").write_text(json.dumps(cfg), encoding="utf-8")

    monkeypatch.setenv("ILINK_BASE_URL", "https://env.invalid")
    monkeypatch.setenv("ILINK_TOKEN", "tok-env")
    monkeypatch.setenv("ADMIN_ILINK_UID", "uid-env")

    loaded = load_agents_config(base_dir=tmp_path)

    assert loaded.get("ilink_base_url") == "https://example.invalid"
    assert loaded.get("ilink_token") == "tok-json"
    assert loaded.get("admin_ilink_uid") == "uid-json"
