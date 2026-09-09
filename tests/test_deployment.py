from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


def test_compose_has_required_isolation() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["daily-digest"]
    assert service["user"] == "99:100"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "ports" not in service
    assert compose["networks"]["proxynet"]["external"] is True
    sources = [item.get("source", "") for item in service["volumes"]]
    assert not any("docker.sock" in source for source in sources)
    read_only_targets = {
        item["target"] for item in service["volumes"] if item.get("read_only") is True
    }
    assert "/data/rclone-logs" in read_only_targets
    assert "/run/secrets/daily-digest" in read_only_targets
    assert "/data/vellum-exports" in read_only_targets
    assert "/data/codex-exports" in read_only_targets


def test_docker_build_context_excludes_sensitive_inputs() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored
    assert "config.yaml" in ignored
    assert "samples" in ignored
    assert ".opencode" in ignored
