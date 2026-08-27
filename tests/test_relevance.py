"""Casos reales que llegaron a encabezar una búsqueda en producción.

Se ejecuta sin dependencias:  python tests/test_relevance.py

Cada entrada marcada False es un falso positivo que de verdad apareció como
"precio más bajo" en el sitio. Ese puesto es el más visible de la página, así
que un accesorio o un homónimo ahí cuesta credibilidad.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_compare.relevance import is_relevant  # noqa: E402

CASES = [
    # --- juguetes y adornos vendidos como el aparato de verdad -------------
    ("laptop", "Winfun Laptop Unicornio Juniorr", False),
    ("laptop", "Laptop Kids Montessori juguete modelo W12B303", False),
    ("laptop", "Laptop Bilingüe Winfun Infantil", False),
    ("televisor", "Villa navideña televisor con luz y música", False),
    # pero si el usuario los pide, salen
    ("laptop juguete", "Laptop Kids Montessori juguete modelo W12B303", True),
    ("laptop infantil", "Laptop Bilingüe Winfun Infantil", True),

    # --- "portatil" es adjetivo, no sustantivo ----------------------------
    ("laptop", "Mesa portátil de computadora 67x34.5x27cm azul wengué", False),
    ("laptop", "Estación de energía portátil de 90 Wh con inversor", False),
    ('laptop', 'Harkn 39.62 cm / 15.6" Pulgadas Monitor Portátil FHD', False),
    ("laptop", "Stand Targus Portable laptop stand", False),
    ("monitor portatil", 'Harkn 15.6" Monitor Portátil FHD', True),

    # --- "pantalla" es homónimo: tulipa, cámara, reloj, celular -----------
    ("televisor", "Lámpara Colgante 1 Luz Satín Níquel Pantalla Opal Blanco", False),
    ("televisor", "Cámara inteligente para interior con pantalla para videollamadas", False),
    ("televisor samsung", 'Celular Samsung Galaxy A07 Pantalla De 6.7"', False),
    # y sigue siendo sinónimo legítimo de televisor
    ("televisor 55", 'Pantalla Samsung 55" LED Crystal UHD 4K', True),
    ("pantalla 55", 'Pantalla Samsung 55" LED Crystal UHD 4K', True),
    # cuando el usuario pide ese otro aparato, aparece
    ("lampara colgante", "Lámpara Colgante Pantalla Opal Blanco", True),
    ("camara", "Cámara inteligente para interior con pantalla", True),
    ("celular samsung", 'Celular Samsung Galaxy A07 Pantalla De 6.7"', True),

    # --- tomas y conectores de pared no son el aparato --------------------
    ("televisor", "TOMA TV COAXIAL", False),
    ("toma coaxial", "TOMA TV COAXIAL", True),

    # --- accesorios clásicos ----------------------------------------------
    ("licuadora", "Vaso para licuadora Oster", False),
    ("licuadora", "Empaque licuadora 4900-011 oster", False),
    ("mini split", "Control remoto para mini split", False),
    ("mochila", "Mochila para Laptop Durham", True),

    # --- electrodomésticos: homónimos y aparatos de otra categoría --------
    # "refri" (5 letras) calzaba dentro de "REFRIgerante para radiador", y el
    # anticongelante de carro salía como la refrigeradora más barata.
    ("refrigeradora", "Refrigerante para radiador 946 ml", False),
    ("refrigeradora", "Traba plástica para puerta refrigeradora Joy", False),
    ("refrigeradora", "Refrigeradora Whirlpool 14 pies No Frost", True),
    ("refrigeradora", "Refrigeradora French Door de 2 puertas Samsung 22 pies", True),
    ("refri", "Refrigeradora Mabe 11 pies", True),
    # una hidrolavadora lleva la palabra "lavadora" y no lava ropa
    ("lavadora", "Yato Hidro lavadora 1400W", False),
    ("lavadora", "Lavadora de presión bare tool 550psi 20v", False),
    ("lavadora", "Lavadora de Tapicería Aksi Home 450w", False),
    ("lavadora", "Sylvanian Families Set de Lavadora y Aspiradora", False),
    ("lavadora", "Lavadora LG Superior Digital 25 kg WT25EWTX6", True),
    ("lavadora", "Lavadora Mabe Superior Análogo 19 kg LMA79113VBAB0", True),
    ("hidrolavadora", "Yato Hidro lavadora 1400W", True),
    ("lavadora de presion", "Lavadora de presión bare tool 550psi 20v", True),
    # la regla "para X" admite palabras en medio sin comerse productos reales
    ("licuadora", "Licuadora para batidos Hamilton Beach 1.4L blanca", True),

    # --- productos reales que deben seguir apareciendo --------------------
    ("laptop", "Laptop Lenovo IdeaPad Slim 3 15AMN8 AMD Ryzen 3 7320U 8GB", True),
    ("laptop", 'Dell 33.8 cm / 13.3" Pulgadas Computadora Portátil Intel Core i3', True),
    ("laptop", 'Laptop HP 15.6" Core i5 16GB teclado retroiluminado pantalla FHD', True),
    ("televisor", 'Televisor Compaq HD LED Modelo QWS32PHD - 32"', True),
    ("televisor 55", 'Televisor Smart de 55" LED Ultra HD 4K con HDR', True),
    ("televisor samsung", 'Televisor Samsung 32" LED HD UN32H5000FPXPA', True),
    ("mini split", "Aire Acondicionado Mini Split Inverter MSAFB-12CR 12,000 BTU", True),
    ("licuadora", "Licuadora Black and Decker plástica de 2 velocidades", True),
    ("audifonos bluetooth", "Audífonos Bluetooth In Ear Sony WF-C510", True),
]

failures = []
for query, name, want in CASES:
    got = is_relevant(query, name)
    if got != want:
        failures.append(
            f"q={query!r}\n    name={name!r}\n    esperado={want} obtenido={got}"
        )

if failures:
    print(f"\n{len(failures)} FALLA(S) de {len(CASES)}:\n")
    for f in failures:
        print("  " + f + "\n")
    sys.exit(1)
print(f"relevancia: {len(CASES)}/{len(CASES)} casos OK")
