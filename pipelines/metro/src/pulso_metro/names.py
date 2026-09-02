"""Turn CRTM's shouting into something you can put on a map.

CRTM publishes station names upper case and only partly accented: 'PEÑAGRANDE' and
'ARGÜELLES' keep their ñ and ü, but 'NUÑEZ DE BALBOA' has lost its ú, and only 30 of 592
source rows carry any accent at all.

Title casing is a rule. Accents are not — 'Martínez' cannot be derived from 'MARTINEZ' —
so they come from the override table below, which is data rather than logic and is meant
to be read and corrected by someone who knows Madrid. Same split as the Cercanias
pipeline's station_display_names.csv, and for the same reason.
"""
from __future__ import annotations

# Words that stay lower case inside a name, but not at the start of one.
PARTICLES = {"de", "del", "la", "las", "los", "el", "y", "a", "en"}

# Kept upper: roman numerals and initialisms.
UPPER = {"XII", "XIII", "T1-T2-T3", "T-4"}

# Accents that cannot be derived. Source name -> what a passenger should read.
# Reviewed by: <unreviewed — Cole to check>
ACCENTS = {
    "ALCORCON CENTRAL": "Alcorcón Central",
    "ALONSO MARTINEZ": "Alonso Martínez",
    "ANTON MARTIN": "Antón Martín",
    "AVENIDA DE AMERICA": "Avenida de América",
    "AVENIDA DE LA ILUSTRACION": "Avenida de la Ilustración",
    "AVIACION ESPAÑOLA": "Aviación Española",
    "BAMBU": "Bambú",
    "BARRIO DE LA CONCEPCION": "Barrio de la Concepción",
    "BATAN": "Batán",
    "CHAMARTIN": "Chamartín",
    "CIUDAD DE LOS ANGELES": "Ciudad de los Ángeles",
    "COLON": "Colón",
    "COLONIA JARDIN": "Colonia Jardín",
    "DIEGO DE LEON": "Diego de León",
    "ESTACION DEL ARTE": "Estación del Arte",
    "FRANCOS RODRIGUEZ": "Francos Rodríguez",
    "GARCIA NOBLEJAS": "García Noblejas",
    "GRAN VIA": "Gran Vía",
    "GREGORIO MARAÑON": "Gregorio Marañón",
    "GUZMAN EL BUENO": "Guzmán el Bueno",
    "HOSPITAL DE MOSTOLES": "Hospital de Móstoles",
    "HOSPITAL INFANTA SOFIA": "Hospital Infanta Sofía",
    "JOAQUIN VILUMBRALES": "Joaquín Vilumbrales",
    "JULIAN BESTEIRO": "Julián Besteiro",
    "LAVAPIES": "Lavapiés",
    "LEGANES CENTRAL": "Leganés Central",
    "MARQUES DE LA VALDAVIA": "Marqués de la Valdavia",
    "MARQUES DE VADILLO": "Marqués de Vadillo",
    "MENDEZ ALVARO": "Méndez Álvaro",
    "MENENDEZ PELAYO": "Menéndez Pelayo",
    "MIGUEL HERNANDEZ": "Miguel Hernández",
    "MOSTOLES CENTRAL": "Móstoles Central",
    "NUÑEZ DE BALBOA": "Núñez de Balboa",
    "OPERA": "Ópera",
    "PACIFICO": "Pacífico",
    "PACO DE LUCIA": "Paco de Lucía",
    "PARQUE DE SANTA MARIA": "Parque de Santa María",
    "PINAR DE CHAMARTIN": "Pinar de Chamartín",
    "PIO XII": "Pío XII",
    "PIRAMIDES": "Pirámides",
    "PLAZA ELIPTICA": "Plaza Elíptica",
    "PRINCIPE DE VERGARA": "Príncipe de Vergara",
    "PRINCIPE PIO": "Príncipe Pío",
    "PUERTA DEL ANGEL": "Puerta del Ángel",
    "REPUBLICA ARGENTINA": "República Argentina",
    "REYES CATOLICOS": "Reyes Católicos",
    "RIOS ROSAS": "Ríos Rosas",
    "RONDA DE LA COMUNICACION": "Ronda de la Comunicación",
    "RUBEN DARIO": "Rubén Darío",
    "SAN CRISTOBAL": "San Cristóbal",
    "SAN FERMIN-ORCASUR": "San Fermín-Orcasur",
    "SANTIAGO BERNABEU": "Santiago Bernabéu",
    "TETUAN": "Tetuán",
    "VELAZQUEZ": "Velázquez",
    "VENTURA RODRIGUEZ": "Ventura Rodríguez",
    "VICALVARO": "Vicálvaro",
}


def _word(w: str, first: bool) -> str:
    if w.upper() in UPPER:
        return w.upper()
    low = w.lower()
    if not first and low in PARTICLES:
        return low
    # Hyphens and apostrophes each start a new word: O'DONNELL, SAN FERMIN-ORCASUR.
    for sep in ("-", "'"):
        if sep in w:
            return sep.join(_word(part, True) for part in w.split(sep))
    return low[:1].upper() + low[1:]


def format_station_name(source_name: str) -> str:
    """Display form of a CRTM station name.

    An override wins outright; otherwise title case with Spanish particles left lower.
    """
    name = source_name.strip()
    if name in ACCENTS:
        return ACCENTS[name]
    return " ".join(_word(w, i == 0) for i, w in enumerate(name.split()))
