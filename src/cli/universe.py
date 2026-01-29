"""Universe fetch CLI commands."""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import orjson
import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from src.config import load_config
from src.fetchers.sse import SseFetcher
from src.models.config import SseConfig
from src.normalizers.sse import normalize_sse_record
from src.storage import UniverseStorage


app = typer.Typer(help="Stock universe fetching commands")
console = Console()


def setup_logging(verbose: bool = False) -> None:
    """Setup logging with rich handler."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


@app.command("fetch")
def fetch_universe(
    exchange: Annotated[
        str,
        typer.Option("--exchange", "-e", help="Exchange to fetch: sse, sze, bse")
    ] = "sse",
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Output directory (default: data/universe)")
    ] = None,
    stock_type: Annotated[
        str,
        typer.Option("--stock-type", "-t", help="Stock type filter (SSE: 1=主板A股, 2=主板B股, 8=科创板)")
    ] = "1",
    page_size: Annotated[
        int,
        typer.Option("--page-size", "-p", help="Records per page")
    ] = 25,
    include_raw: Annotated[
        bool,
        typer.Option("--include-raw", help="Include raw data in output")
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose logging")
    ] = False,
) -> None:
    """Fetch stock universe from exchange.

    Example:
        stock-bot universe fetch --exchange sse --stock-type 1
    """
    setup_logging(verbose)
    logger = logging.getLogger(__name__)

    # Validate exchange
    exchange_lower = exchange.lower()
    if exchange_lower not in ("sse", "sze", "bse"):
        console.print(f"[red]Error:[/red] Unknown exchange: {exchange}")
        console.print("Supported exchanges: sse, sze, bse")
        raise typer.Exit(1)

    if exchange_lower != "sse":
        console.print(f"[yellow]Warning:[/yellow] Exchange '{exchange}' not yet implemented")
        console.print("Currently only SSE is supported in M0")
        raise typer.Exit(1)

    # Load config
    try:
        config_data = load_config("sse")
        config = SseConfig.from_yaml(config_data)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # Override config from CLI args
    config.filters["STOCK_TYPE"] = stock_type
    config.pagination.page_size = page_size

    # Setup storage
    output_dir = output or Path("data/universe")
    storage = UniverseStorage(output_dir)

    asof = datetime.now(timezone.utc)
    console.print(f"\n[bold]Stock Universe Fetch[/bold]")
    console.print(f"  Exchange: {exchange.upper()}")
    console.print(f"  Stock Type: {stock_type}")
    console.print(f"  Page Size: {page_size}")
    console.print(f"  Output: {output_dir}")
    console.print(f"  Timestamp: {asof.isoformat()}")
    console.print()

    start_time = time.time()
    total_records = 0
    failed_pages = 0
    errors: list[dict] = []

    try:
        with SseFetcher(config) as fetcher:
            with storage.open_writer(asof, "Shanghai_Stocks") as writer:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("Fetching...", total=None)

                    for raw_record, source_url, record_asof in fetcher.iter_raw_records(asof):
                        try:
                            normalized = normalize_sse_record(
                                raw_record,
                                source_url,
                                record_asof,
                                stock_type=stock_type,
                                include_raw=include_raw,
                            )
                            writer.write_record(normalized)
                            total_records += 1

                            progress.update(
                                task,
                                description=f"Fetching... {total_records} records"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to normalize record: {e}")
                            if len(errors) < 10:
                                errors.append({
                                    "type": "normalize_error",
                                    "error": str(e),
                                })

                # Build and write manifest
                duration = time.time() - start_time
                manifest = storage.build_manifest(
                    exchange="Shanghai_Stocks",
                    asof=asof,
                    config=config,
                    writer=writer,
                    duration_seconds=duration,
                    failed_pages=failed_pages,
                    errors=errors,
                )
                manifest.stats.total_pages = fetcher.client._last_request_time > 0 and 1 or 0
                storage.write_manifest(asof, manifest)

        # Print summary
        duration = time.time() - start_time
        console.print()
        console.print("[bold green]✓ Fetch completed[/bold green]")
        console.print(f"  Records: {total_records}")
        console.print(f"  Categories: {len(manifest.stats.categories)}")
        console.print(f"  Duration: {duration:.1f}s")
        console.print(f"  Output: {storage.create_snapshot_dir(asof)}")

        if errors:
            console.print(f"  [yellow]Errors: {len(errors)}[/yellow]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        raise typer.Exit(130)
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        logger.exception("Fetch failed")
        raise typer.Exit(1)


@app.command("list")
def list_snapshots(
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Universe directory (default: data/universe)")
    ] = None,
) -> None:
    """List available universe snapshots."""
    output_dir = output or Path("data/universe")

    if not output_dir.exists():
        console.print(f"[yellow]No snapshots found in {output_dir}[/yellow]")
        return

    snapshots = sorted(output_dir.glob("snapshot=*"))
    if not snapshots:
        console.print(f"[yellow]No snapshots found in {output_dir}[/yellow]")
        return

    console.print(f"\n[bold]Available Snapshots[/bold] ({output_dir})\n")
    for snapshot in snapshots:
        manifest_path = snapshot / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path, "rb") as f:
                manifest = orjson.loads(f.read())
            stats = manifest.get("stats", {})
            console.print(f"  {snapshot.name}")
            console.print(f"    Exchange: {manifest.get('exchange', 'N/A')}")
            console.print(f"    Records: {stats.get('unique_records', 'N/A')}")
            console.print(f"    Duration: {stats.get('duration_seconds', 0):.1f}s")
        else:
            console.print(f"  {snapshot.name} (no manifest)")
        console.print()


if __name__ == "__main__":
    app()
