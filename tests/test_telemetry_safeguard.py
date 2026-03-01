import os

import pytest

from shared.telemetry import bootstrap


def test_bootstrap_does_nothing_in_test_env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("OTEL_ENABLED", "true")
    bootstrap(None)
    assert os.getenv("OTEL_ENABLED") == "false"
