"""CLI module for stock-bot."""

__all__ = ["app"]


def __getattr__(name: str):
	if name == "app":
		from .main import app

		return app
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
