import pytest

from fv import config


def test_refuses_default_credentials(monkeypatch):
    monkeypatch.delenv("FV_RUNNER", raising=False)
    monkeypatch.setenv("AWS_PROFILE", "default")
    with pytest.raises(RuntimeError, match="AWS_PROFILE"):
        config.require_sandbox()


def test_accepts_lab_profile(monkeypatch):
    monkeypatch.delenv("FV_RUNNER", raising=False)
    monkeypatch.setenv("AWS_PROFILE", "fv-lab")
    config.require_sandbox()


def test_accepts_runner(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("FV_RUNNER", "1")
    config.require_sandbox()


def test_env_names_missing_variable(monkeypatch):
    monkeypatch.delenv("FV_DATA_BUCKET", raising=False)
    with pytest.raises(KeyError, match="FV_DATA_BUCKET"):
        config.env("DATA_BUCKET")


def test_refuses_lookalike_sandbox_profile(monkeypatch):
    monkeypatch.delenv("FV_RUNNER", raising=False)
    monkeypatch.setenv("AWS_PROFILE", "x-sandbox")
    with pytest.raises(RuntimeError, match="AWS_PROFILE"):
        config.require_sandbox()
