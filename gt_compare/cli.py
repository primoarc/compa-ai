"""CLI de gt-compare (typer)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer
from rich.console import Console

from . import deals as deals_mod, display, planner, vtex
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


# Búsquedas que se revisan por defecto al cazar errores de precio. Son
# categorías caras, donde una equivocación de la tienda vale la pena.
DEFAULT_WATCHLIST = [
    "televisor 65", "televisor 55", "televisor 75",
    "laptop", "refrigeradora", "lavadora", "mini split",
    "playstation 5", "airpods", "monitor",
]


@app.command()
def deals(
    queries: str = typer.Option(
        "", "--queries", "-q",
        help="Búsquedas separadas por coma. Por defecto usa la watchlist.",
    ),
    min_discount: float = typer.Option(
        0.0, "--min", help="Descuento mínimo para reportar (0.5 = 50%)."
    ),
    cohorte: bool = typer.Option(
        False, "--cohorte",
        help="Incluir la señal por cohorte de tamaño (ruidosa: compara gamas "
             "distintas, una laptop Celeron parece 'rebajada' frente a un i7).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Salida JSON."),
    post: bool = typer.Option(
        False, "--post", help="Imprime el borrador de publicación de cada hallazgo."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Busca precios anómalos (posibles errores de precio) en la watchlist."""
    import json as _json

    _setup_logging(verbose)
    watch = [q.strip() for q in queries.split(",") if q.strip()] or DEFAULT_WATCHLIST
    stores = load_stores()

    found: list = []
    # Un mismo producto aparece en varias búsquedas de la lista ("televisor" y
    # "televisor 55" devuelven la misma tele), así que se deduplica entre
    # queries y no solo dentro de cada una.
    vistos: set = set()
    for q in watch:
        if not as_json:
            console.print(f"[dim]revisando[/dim] {q}…")
        try:
            rows = asyncio.run(_deal_rows(stores, q))
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]  {q}: {exc}[/yellow]")
            continue
        for a in deals_mod.find_anomalies(q, rows):
            if a.discount < min_discount:
                continue
            # Por defecto, las dos señales que comparan contra una referencia
            # legítima: el mismo modelo en otra tienda, o el precio de lista de
            # la tienda misma. La cohorte confunde gama baja con rebaja, así
            # que va detrás de un flag.
            if not cohorte and a.kind == "cohorte":
                continue
            clave = (a.store_key, a.name[:90], round(a.price, 2))
            if clave in vistos:
                continue
            vistos.add(clave)
            found.append(a)

    found.sort(key=lambda a: a.savings, reverse=True)

    if as_json:
        console.print_json(_json.dumps([
            {
                "query": a.query, "kind": a.kind, "confidence": a.confidence,
                "store": a.store, "name": a.name, "price": a.price,
                "reference": a.reference, "discount": round(a.discount, 3),
                "savings": round(a.savings, 2), "url": a.url,
                "peers": a.peers, "model": a.model,
            } for a in found
        ], ensure_ascii=False))
        return

    if not found:
        console.print("\n[green]Nada anómalo por ahora.[/green]")
        if not cohorte:
            console.print("[dim]Probá --cohorte para una señal más amplia (y más ruidosa).[/dim]")
        return

    from rich.table import Table
    table = Table(title=f"Precios anómalos ({len(found)})", show_lines=False)
    table.add_column("Conf", width=5)
    table.add_column("Tienda", width=16)
    table.add_column("Producto", overflow="fold")
    table.add_column("Precio", justify="right")
    table.add_column("Normal", justify="right")
    table.add_column("Desc", justify="right")
    for a in found:
        color = "bold green" if a.confidence == "alta" else "yellow"
        table.add_row(
            f"[{color}]{a.confidence[:4]}[/{color}]",
            a.store,
            a.name[:70],
            f"Q{a.price:,.0f}",
            f"Q{a.reference:,.0f}",
            f"[{color}]-{a.discount*100:.0f}%[/{color}]",
        )
    console.print(table)
    for a in found:
        console.print(f"[dim]{a.url}[/dim]")
    if post:
        console.print("\n[bold]Borradores de publicación[/bold]")
        for a in found:
            console.print(f"\n[dim]---[/dim]\n{deals_mod.post_text(a)}")


async def _deal_rows(stores, query: str):
    """Filas por tienda para una búsqueda, con el mismo criterio que la web."""
    from .web import _best_per_store

    plan = planner.local_plan(query)
    results = await vtex.search_all(
        stores, query, timeout=12, ttl_seconds=30 * 60, plan=plan
    )
    return _best_per_store(query, results, plan=plan)


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
