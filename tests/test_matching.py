"""Casos reales de catálogos guatemaltecos para gt_compare.matching.

Se ejecuta sin dependencias:  python tests/test_matching.py

Todos los títulos salen de respuestas reales de las tiendas. Los que están
marcados como NO deben agruparse son falsos positivos que sí ocurrieron.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare.matching import (  # noqa: E402
    brands, capacities, cpu_model, group_offers, model_codes, screen_size,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    esperado: {want!r}\n    obtenido: {got!r}")


def offer(key, price, name):
    return dict(store=key.title(), store_key=key, price=price, name=name)


def grouped(*offers):
    """(nº de grupos, tiendas del primero) para aserciones legibles."""
    gs = group_offers(list(offers))
    return (len(gs), gs[0]["stores"] if gs else 0)


# --- especificaciones que NO son códigos de modelo -------------------------
for spec in ["HDR10", "HDR10PLUS", "1080P", "2160P", "3840X2160", "700W",
             "12000BTU", "60HZ", "802", "11AC", "USB3", "DDR4"]:
    check(f"'{spec}' no debe ser código de modelo", model_codes(spec), set())

# --- extracción ------------------------------------------------------------
check("modelo Samsung",
      model_codes('Televisor Samsung 32" LED HD UN32H5000FPXPA'),
      {"UN32H5000FPXPA"})
check("pulgadas con comilla", screen_size('Televisor Samsung 32" LED HD'), 32.0)
check("pulgadas escritas", screen_size("Samsung Smart TV 32 Pulgadas HD"), 32.0)
check("prefiere pulgadas sobre cm",
      screen_size('Samsung 140 cm / 55" Pulgadas Smart Tizen'), 55.0)
check("CPU con símbolo TM",
      cpu_model("Lenovo IdeaPad AMD Ryzen™ 3 7320U 8GB"), "3-7320U")
check("CPU Intel con sufijo letra+dígito",
      cpu_model("Intel Core i5-1135G7 8GB"), "5-1135G7")
check("el CPU no cuenta como modelo",
      model_codes("Laptop HP Pavilion 15-eg0500la Intel Core i5-1135G7 8GB 256GB"),
      {"EG0500LA"})
check("capacidades en orden",
      capacities("8GB RAM + 512GB SSD"), (8, 512))
check("Gigabytes escrito",
      capacities("8 Gigabytes / 512 Gigabytes"), (8, 512))
check("marca detectada", brands("Laptop Dell DC15250"), {"dell"})

# --- SÍ son el mismo producto ---------------------------------------------
check("código corto contenido en el extendido (H5000F / UN32H5000FPXPA)",
      grouped(
          offer("curacao", 1397.0, 'Televisor Samsung 32" LED HD UN32H5000FPXPA'),
          offer("kemik", 1349.0, "Samsung Smart TV 32 Pulgadas HD H5000F (2025)"),
      ), (1, 2))
check("mismo TV en tres tiendas",
      grouped(
          offer("siman", 5699.0, 'Pantalla Samsung 75" LED Crystal UHD UN75U8000FPXPA.'),
          offer("curacao", 5997.0, 'Televisor Samsung 75" LED 4K UHD UN75U8000FPXPA'),
          offer("radioshack", 10499.0, 'Televisor Samsung 75" LED 4K UHD UN75U8000FPXPA'),
      ), (1, 3))
check("variante regional BN701 / BN701LA",
      grouped(
          offer("pricesmart", 629.70, "Ninja Licuadora Profesional Auto-iQ BN701LA 2.13 L"),
          offer("walmart", 820.0, "Licuadora NINJA Pro Plus BN701"),
      ), (1, 2))

# --- NO son el mismo producto (falsos positivos reales) --------------------
check("mismo código de serie, distinta diagonal (U8000H 43\" vs 85\")",
      grouped(
          offer("cemaco", 2499.0, 'Televisor Samsung de 43" Smart TV Crystal UHD 4K U8000H'),
          offer("cemaco2", 8999.0, "Smart TV Samsung Crystal UHD U8000H 4k de 85 Plg"),
      ), (0, 0))
check("marcas distintas con código compartido (HP vs Lenovo)",
      grouped(
          offer("kemik", 5327.0, "Laptop Lenovo IdeaPad 3 XG500LA i5-1135G7 8GB 512GB"),
          offer("radioshack", 5999.0, "Laptop HP Pavilion XG500LA i5-1135G7 8GB 512GB"),
      ), (0, 0))
check("mismo chasis, distinta RAM (DC15250 8GB vs 16GB)",
      grouped(
          offer("kemik", 5233.0, "Laptop Dell DC15250 i5-1334U 8GB RAM + 512GB SSD"),
          offer("siman", 6999.0, "Laptop Dell DC15250 i5-1334U 16GB RAM 512GB SSD"),
      ), (0, 0))
check("mismo chasis, distinto CPU (15AMN8 Ryzen 3 vs Ryzen 5)",
      grouped(
          offer("curacao", 3997.0, "Laptop Lenovo IdeaPad Slim 3 15AMN8 AMD Ryzen™ 3 7320U 8GB 512GB"),
          offer("radioshack", 4299.0, "Laptop Lenovo IdeaPad Slim 3 15AMN8 AMD Ryzen™ 5 7520U 8GB 512GB"),
      ), (0, 0))
check("dos ofertas de la misma tienda no son comparación",
      grouped(
          offer("a", 10.0, "TV UN32H5000FPXPA"),
          offer("a", 20.0, "TV UN32H5000FPXPA"),
      ), (0, 0))
check("mismo precio no aporta nada",
      grouped(
          offer("a", 10.0, "TV UN32H5000FPXPA"),
          offer("b", 10.0, "TV UN32H5000FPXPA"),
      ), (0, 0))
check("sin código de modelo no se agrupa",
      grouped(
          offer("a", 10.0, "Licuadora personal negra"),
          offer("b", 20.0, "Licuadora personal blanca"),
      ), (0, 0))
check("lista vacía", group_offers([]), [])

if failures:
    print(f"\n{len(failures)} FALLA(S):\n")
    for f in failures:
        print("  " + f + "\n")
    sys.exit(1)
print("matching: todos los casos OK")
