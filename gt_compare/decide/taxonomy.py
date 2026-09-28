"""Taxonomía propia de dos niveles y clasificador por palabras clave.

Dos niveles porque una sola lista plana supera lo razonable para una Choice
(el límite de Jev es 255 opciones, pero la precisión cae con listas largas):
primero se elige el nivel 1 (~16 opciones) y después el nivel 2 dentro de él.
"""

from __future__ import annotations

import re
from typing import Optional

from ..relevance import normalize

# nivel 1 -> {nivel 2 -> palabras clave (normalizadas, sin tildes)}
TAXONOMY: dict[str, dict[str, list[str]]] = {
    "televisores_video": {
        "televisores": ["televisor", "smart tv", "pantalla led", "qled", "oled", "tv "],
        "proyectores": ["proyector"],
        "streaming_tv": ["roku", "fire tv", "chromecast", "tv box", "soporte para tv", "rack tv"],
    },
    "audio": {
        "audifonos": ["audifono", "auricular", "earbuds", "airpods", "headset"],
        "bocinas": ["bocina", "parlante", "speaker"],
        "barras_sonido": ["barra de sonido", "soundbar", "teatro en casa"],
        "equipos_sonido": ["minicomponente", "equipo de sonido", "tornamesa", "microfono"],
    },
    "celulares_tablets": {
        "celulares": ["celular", "smartphone", "iphone", "galaxy s", "galaxy a", "redmi", "motorola moto"],
        "tablets": ["tablet", "ipad", "galaxy tab"],
        "smartwatch": ["smartwatch", "reloj inteligente", "apple watch", "smart band", "galaxy watch"],
        "accesorios_celular": ["cargador", "cable usb", "power bank", "protector de pantalla", "funda", "case para"],
    },
    "computacion": {
        "laptops": ["laptop", "notebook", "macbook", "chromebook"],
        "computadoras_escritorio": ["computadora de escritorio", "desktop", "all in one", "imac", "mini pc"],
        "monitores": ["monitor"],
        "impresoras": ["impresora", "multifuncional", "tinta", "toner", "cartucho"],
        "componentes": ["tarjeta de video", "procesador", "memoria ram", "tarjeta madre", "fuente de poder", "gabinete"],
        "almacenamiento": ["disco duro", "ssd", "memoria usb", "memoria micro sd", "usb flash"],
        "redes": ["router", "repetidor", "switch de red", "access point", "mesh"],
        "perifericos": ["teclado", "mouse", "webcam", "ups", "regulador"],
    },
    "videojuegos": {
        "consolas": ["consola", "playstation 5", "ps5", "xbox series", "nintendo switch"],
        "juegos": ["videojuego", "juego para ps", "juego ps5", "juego xbox", "juego nintendo"],
        "accesorios_gaming": ["control inalambrico", "control ps5", "control xbox", "silla gamer", "gamer"],
    },
    "linea_blanca": {
        "refrigeradoras": ["refrigeradora", "refrigerador", "refri", "side by side"],
        "lavadoras_secadoras": ["lavadora", "secadora", "centro de lavado"],
        "estufas_hornos": ["estufa", "cocina a gas", "horno electrico", "horno empotrable", "campana extractora", "cooktop"],
        "aire_acondicionado": ["aire acondicionado", "minisplit", "mini split", "inverter split"],
        "congeladores": ["congelador", "freezer", "enfriador", "oasis"],
    },
    "electrodomesticos_pequenos": {
        "microondas": ["microondas"],
        "licuadoras_procesadores": ["licuadora", "procesador de alimentos", "batidora", "extractor de jugo"],
        "cafeteras": ["cafetera", "espresso"],
        "freidoras": ["freidora", "air fryer"],
        "planchas": ["plancha de ropa", "plancha a vapor", "vaporizador"],
        "aspiradoras": ["aspiradora", "robot aspirador"],
        "ventiladores": ["ventilador", "calentador", "purificador", "humidificador", "deshumidificador"],
        "otros_electro": ["olla arrocera", "sandwichera", "tostadora", "waflera", "olla electrica", "olla de presion electrica"],
        "cuidado_personal_electrico": ["secadora de pelo", "secador de cabello", "plancha de pelo", "plancha para cabello", "rasuradora", "afeitadora", "cortadora de pelo"],
    },
    "hogar_muebles": {
        "muebles": ["sofa", "sillon", "comedor", "escritorio", "silla", "mesa de", "gavetero", "ropero", "mueble", "librera", "centro de entretenimiento"],
        "colchones_camas": ["colchon", "cama", "almohada", "base para colchon", "box spring"],
        "decoracion": ["cuadro", "espejo", "florero", "cortina", "alfombra", "adorno"],
        "iluminacion": ["lampara", "foco", "bombillo", "luminaria", "plafon"],
        "textiles_hogar": ["sabana", "edredon", "toalla", "cobertor", "juego de cama"],
        "organizacion": ["organizador", "caja plastica", "canasta", "zapatera", "perchero"],
    },
    "cocina_mesa": {
        "ollas_sartenes": ["olla", "sarten", "bateria de cocina", "cacerola"],
        "utensilios": ["cuchillo", "utensilio", "espatula", "tabla para picar", "rallador", "colador"],
        "vajilla": ["vajilla", "plato", "vaso", "taza", "cubiertos", "copa", "termo", "botella"],
    },
    "ferreteria_herramientas": {
        "herramientas_electricas": ["taladro", "rotomartillo", "sierra", "pulidora", "esmeriladora", "atornillador", "hidrolavadora", "compresor"],
        "herramientas_manuales": ["martillo", "desarmador", "llave", "alicate", "juego de herramientas", "caja de herramientas", "cinta metrica"],
        "pintura": ["pintura", "brocha", "rodillo", "esmalte", "impermeabilizante"],
        "plomeria_electricidad": ["tuberia", "grifo", "llave de paso", "inodoro", "lavamanos", "ducha", "cable electrico", "tomacorriente", "interruptor", "breaker", "extension electrica"],
        "jardin": ["podadora", "manguera", "maceta", "jardin", "fertilizante", "desbrozadora"],
    },
    "moda_accesorios": {
        "ropa": ["camisa", "playera", "pantalon", "blusa", "vestido", "chaqueta", "sudadero", "jeans", "short", "ropa interior", "calcetin", "pijama"],
        "calzado": ["zapato", "tenis", "sandalia", "bota", "zapatilla"],
        "bolsos_accesorios": ["mochila", "bolso", "cartera", "maleta", "billetera", "lentes de sol", "gorra", "cinturon"],
        "relojes_joyeria": ["reloj", "collar", "arete", "pulsera", "anillo"],
    },
    "belleza_salud": {
        "maquillaje": ["maquillaje", "labial", "rimel", "mascara de pestanas", "base de maquillaje", "sombra", "delineador", "corrector"],
        "perfumes": ["perfume", "eau de parfum", "eau de toilette", "colonia", "fragancia"],
        "cuidado_piel_cabello": ["crema", "serum", "shampoo", "acondicionador", "protector solar", "desodorante", "jabon", "tratamiento capilar"],
        "salud": ["vitamina", "suplemento", "tabletas", "capsulas", "medicamento", "termometro", "tensiometro", "glucometro"],
    },
    "bebes_juguetes": {
        "juguetes": ["juguete", "muneca", "lego", "peluche", "carro a control", "rompecabezas", "juego de mesa", "hot wheels", "barbie"],
        "bebes": ["bebe", "panal", "carruaje", "cuna", "biberon", "pacha", "silla para carro", "portabebe"],
    },
    "deportes_aire_libre": {
        "fitness": ["mancuerna", "caminadora", "banda elastica", "yoga", "pesas", "bicicleta estacionaria", "eliptica"],
        "bicicletas": ["bicicleta", "casco para bici", "scooter", "patineta"],
        "camping": ["tienda de campana", "sleeping", "hielera", "camping", "parrilla", "asador"],
        "deportes": ["balon", "pelota de", "raqueta", "guantes de box", "futbol", "basquetbol"],
    },
    "supermercado": {
        "alimentos": ["arroz", "frijol", "cereal", "galleta", "aceite de cocina", "azucar", "cafe molido", "cafe en grano", "leche", "queso", "carne", "pollo", "atun", "pasta", "salsa", "snack", "chocolate"],
        "bebidas": ["agua pura", "gaseosa", "jugo", "cerveza", "vino", "whisky", "ron", "bebida"],
        "limpieza": ["detergente", "cloro", "suavizante", "desinfectante", "papel higienico", "servilleta", "lavaplatos", "limpiador"],
        "mascotas": ["perro", "gato", "mascota", "croqueta", "arena para gato"],
    },
    "automotriz": {
        "automotriz": ["llanta", "bateria para carro", "aceite para motor", "aceite de motor", "limpiaparabrisas", "autoestereo", "car audio", "cargador de carro"],
    },
}

OTHER = "otros"

LEVEL1 = list(TAXONOMY) + [OTHER]

# Descripciones para la Choice de nivel 1 (en inglés: Jev rinde mejor en inglés).
LEVEL1_DESCRIPTIONS: dict[str, str] = {
    "televisores_video": "TVs, projectors, streaming devices and TV mounts",
    "audio": "headphones, speakers, soundbars, stereo systems, microphones",
    "celulares_tablets": "mobile phones, tablets, smartwatches and phone accessories",
    "computacion": "laptops, desktops, monitors, printers and ink, PC components, storage, networking, keyboards and mice",
    "videojuegos": "video game consoles, games and gaming accessories",
    "linea_blanca": "large appliances: refrigerators, washers, dryers, stoves, ovens, air conditioners, freezers",
    "electrodomesticos_pequenos": "small appliances: microwaves, blenders, coffee makers, air fryers, irons, vacuums, fans, hair dryers",
    "hogar_muebles": "furniture, mattresses, bedding, home decor, lighting, storage organizers",
    "cocina_mesa": "cookware, kitchen utensils, tableware, glasses, bottles",
    "ferreteria_herramientas": "tools, paint, plumbing, electrical supplies, garden",
    "moda_accesorios": "clothing, shoes, bags, luggage, watches and jewelry",
    "belleza_salud": "makeup, perfume, skin and hair care, vitamins, health devices",
    "bebes_juguetes": "toys and baby products",
    "deportes_aire_libre": "fitness, bicycles, camping, sports equipment",
    "supermercado": "groceries, beverages, cleaning supplies, pet food",
    "automotriz": "car parts and car accessories",
    OTHER: "anything that does not fit the other categories",
}


LEVEL1_LABELS: dict[str, str] = {
    "televisores_video": "Televisores y video",
    "audio": "Audio",
    "celulares_tablets": "Celulares y tablets",
    "computacion": "Computación",
    "videojuegos": "Videojuegos",
    "linea_blanca": "Línea blanca",
    "electrodomesticos_pequenos": "Electrodomésticos",
    "hogar_muebles": "Hogar y muebles",
    "cocina_mesa": "Cocina y mesa",
    "ferreteria_herramientas": "Ferretería",
    "moda_accesorios": "Moda",
    "belleza_salud": "Belleza y salud",
    "bebes_juguetes": "Bebés y juguetes",
    "deportes_aire_libre": "Deportes",
    "supermercado": "Supermercado",
    "automotriz": "Automotriz",
    OTHER: "Otros",
}


def level2_options(level1: str) -> list[str]:
    return list(TAXONOMY.get(level1, {}))


def _compile() -> list[tuple[re.Pattern, str, str, int]]:
    out = []
    for l1, subs in TAXONOMY.items():
        for l2, words in subs.items():
            for w in words:
                pat = re.compile(r"(?<![a-z0-9])" + re.escape(w.strip()) + r"(?:s|es)?(?![a-z0-9])")
                out.append((pat, l1, l2, len(w.strip())))
    return out


_RULES = _compile()


def classify_rules(name: str, store_category: Optional[str] = None) -> tuple[str, Optional[str], float]:
    """(nivel1, nivel2, peso) por palabra clave más larga. Peso 0 si no hubo match.

    La palabra más larga gana: "plancha de pelo" le gana a "plancha".
    El nombre pesa más que la categoría de tienda, que a veces es genérica.
    """
    best: tuple[str, Optional[str], float] = (OTHER, None, 0.0)
    for text, bonus in ((normalize(name or ""), 1.0), (normalize(store_category or ""), 0.5)):
        if not text:
            continue
        padded = f" {text} "
        for pat, l1, l2, size in _RULES:
            if pat.search(padded):
                weight = size * bonus
                if weight > best[2]:
                    best = (l1, l2, weight)
    return best
