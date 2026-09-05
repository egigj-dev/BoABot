"""BoABot core package."""

from .env import load_project_env


# Must run before submodules capture configuration from ``os.environ``.
load_project_env()
