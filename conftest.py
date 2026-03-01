import os

pytest_plugins = ["pytest_asyncio"]


def pytest_configure(config):
    os.environ["ENVIRONMENT"] = "test"
