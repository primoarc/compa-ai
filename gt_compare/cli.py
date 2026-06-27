"""CLI de gt-compare (typer)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer
from rich.console import Console

from . import display, planner, vtex
from .stores import CONFIG_DIR, load_stores

app = typer.Typer(
    add_completion=False,
    help="Comparador de precios en tiendas VTEX de Guatemala.",
)
console = Console()

ERROR_LOG = CONFIG_DIR / "errors.log"


def _setup_logging(verbose: bool) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gt_compare")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler(ERROR_LOG, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(file_handler)

    if verbose:
        from rich.logging import RichHandler
        logger.addHandler(RichHandler(console=console, show_path=False))


def _run_search(query: str, store: str | None):
    stores = load_stores(only=store)
    if not stores:
        console.print(f"[red]No hay tiendas que coincidan con '{store}'.[/red]")
        raise typer.Exit(1)
    cfg_timeout = 8
    return asyncio.run(_run_search_async(stores, query, cfg_timeout))


async def _run_search_async(stores, query: str, timeout: int):
    plan = await planner.build_query_plan(query)
    results = await vtex.search_all(
        stores, query, timeout=timeout, ttl_seconds=30 * 60, plan=plan
    )
    return results, plan


@app.command()
def search(
    query: str = typer.Argument(..., help="Texto a buscar"),
    store: str = typer.Option(None, "--store", "-s", help="Limitar a una tienda (key)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Mostrar errores"),
):
    """Busca un producto en las tiendas y muestra la tabla por precio."""
    _setup_logging(verbose)
    with console.status(f"Buscando '{query}'..."):
        results, plan = _run_search(query, store)
    display.render_results(query, results, plan=plan)


@app.command()
def batch(
    file: Path = typer.Option(..., "-f", "--file", help="Archivo con un producto por línea"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Mostrar errores"),
):
    """Busca cada línea del archivo y muestra un resumen de ahorro."""
    _setup_logging(verbose)
    if not file.exists():
        console.print(f"[red]No existe el archivo: {file}[/red]")
        raise typer.Exit(1)

    items = [
        line.strip()
        for line in file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not items:
        console.print("[yellow]El archivo está vacío.[/yellow]")
        raise typer.Exit(1)

    savings: list[tuple[str, vtex.Product]] = []
    for item in items:
        console.rule(f"[bold]{item}[/bold]")
        with console.status(f"Buscando '{item}'..."):
            results, plan = _run_search(item, None)
        cheapest = display.render_results(item, results, plan=plan)
        if cheapest:
            savings.append((item, cheapest))
        console.print()

    display.render_batch_summary(savings)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host"),
    port: int = typer.Option(8000, help="Puerto"),
):
    """Levanta el frontend web (requiere extras: pip install -e '.[web]')."""
    try:
        import uvicorn
    except ModuleNotFoundError:
        console.print("[red]Faltan extras web. Instala:[/red] pip install -e '.[web]'")
        raise typer.Exit(1)
    console.print(f"[green]gt-compare web[/green] → http://{host}:{port}")
    uvicorn.run("gt_compare.web:app", host=host, port=port)


def main():
    app()


if __name__ == "__main__":
    main()
