"""Tablas rich y formato de salida en terminal."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from . import relevance
from .vtex import Product, StoreResult

console = Console()


def _fmt_price(price: float | None) -> str:
    if price is None:
        return "—"
    return f"Q{price:,.2f}"


def _flatten(query: str, results: list[StoreResult]) -> list[Product]:
    """Producto RELEVANTE más barato de cada tienda (filtrado por relevancia)."""
    rows: list[Product] = []
    for res in results:
        if not res.ok:
            continue
        match = relevance.best_match(query, res.products)
        if match is not None:
            rows.append(match)
    return rows


def render_results(query: str, results: list[StoreResult]) -> Product | None:
    """Imprime la tabla ordenada por precio. Devuelve la fila más barata."""
    rows = _flatten(query, results)
    rows.sort(key=lambda p: p.price)  # type: ignore[arg-type,return-value]

    table = Table(title=f'Resultados para "{query}"', title_style="bold")
    table.add_column("Tienda", style="cyan", no_wrap=True)
    table.add_column("Producto", overflow="fold", max_width=44)
    table.add_column("Precio (GTQ)", justify="right")
    table.add_column("Disponible", justify="right")
    table.add_column("URL", overflow="fold", style="dim")

    cheapest = rows[0] if rows else None
    for p in rows:
        is_best = p is cheapest
        price_txt = _fmt_price(p.price)
        price_cell = f"[bold green]{price_txt}[/bold green]" if is_best else price_txt
        avail = "Sí" if p.available > 0 else "Agotado"
        table.add_row(p.store_name, p.name, price_cell, avail, p.url)

    # Tiendas sin coincidencia relevante o que no respondieron.
    matched_stores = {p.store_key for p in rows}
    for res in results:
        if res.store.key in matched_stores:
            continue
        if not res.ok:
            reason = res.error or "sin resultados"
        elif _has_priced(res):
            reason = "sin coincidencia para tu búsqueda"
        else:
            reason = "sin resultados"
        table.add_row(
            res.store.name,
            f"[dim]No disponible ({reason})[/dim]",
            "—", "—", "—",
        )

    console.print(table)
    return cheapest


def _has_priced(res: StoreResult) -> bool:
    return any(p.price is not None and p.price > 0 for p in res.products)


def render_batch_summary(savings: list[tuple[str, Product]]) -> None:
    """Resumen final del batch con ahorro potencial total."""
    if not savings:
        console.print("[yellow]Sin resultados para resumir.[/yellow]")
        return

    table = Table(title="Resumen — comprando cada ítem en la tienda más barata",
                  title_style="bold")
    table.add_column("Ítem")
    table.add_column("Tienda más barata", style="cyan")
    table.add_column("Precio (GTQ)", justify="right", style="green")

    total = 0.0
    for item, prod in savings:
        total += prod.price or 0.0
        table.add_row(item, prod.store_name, _fmt_price(prod.price))

    table.add_section()
    table.add_row("[bold]TOTAL[/bold]", "", f"[bold green]{_fmt_price(total)}[/bold green]")
    console.print(table)
