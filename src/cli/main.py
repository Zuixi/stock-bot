"""CLI entry point for stock-bot."""

import typer

from .universe import app as universe_app

app = typer.Typer(
    name="stock-bot",
    help="Stock universe fetching and clustering analysis tool.",
    no_args_is_help=True,
)

# Register subcommands
app.add_typer(universe_app, name="universe", help="Stock universe management")


if __name__ == "__main__":
    app()
