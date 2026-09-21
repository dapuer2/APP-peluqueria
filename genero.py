"""
Inferencia de sexo (Hombre / Mujer) a partir del nombre de pila.

No es infalible: se basa en un diccionario de nombres espanoles habituales
(incluidos apodos locales) mas la regla de terminacion -a/-o.
Los que no se pueden determinar se devuelven como 'Desconocido' y en la web
se excluyen del porcentaje (mostrando cuantos quedan sin clasificar).
"""

import unicodedata


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


HOMBRE = {
    "jose", "juan", "javier", "david", "daniel", "manuel", "miguel", "angel",
    "carlos", "luis", "antonio", "francisco", "sergio", "ruben", "adrian",
    "ivan", "oscar", "raul", "victor", "alberto", "alejandro", "jorge", "pablo",
    "pedro", "fernando", "ramon", "vicente", "vicent", "jesus", "marcos",
    "mario", "hector", "diego", "cristian", "gabriel", "santiago", "rafael",
    "salvador", "joaquin", "andres", "enrique", "martin", "nicolas", "alvaro",
    "arturo", "borja", "bruno", "cesar", "eduardo", "gonzalo", "guillermo",
    "ignacio", "ismael", "julian", "lucas", "roberto", "rodrigo", "samuel",
    "sebastian", "tomas", "xavier", "toni", "joseph", "felix", "alexis", "pau",
    "josep", "ximo", "nando", "quique", "kike", "aitor", "unai", "marc",
    "aaron", "abel", "agustin", "alfredo", "emilio", "isaac", "joel", "matias",
    "moises", "pau", "hugo", "izan", "dario", "cristobal", "domingo", "felipe",
    "gregorio", "jaime", "juanjo", "juanma", "chema", "curro", "paco", "lucho",
    "edu", "pepe", "joan", "joanvi", "asier", "aitor", "iker", "gorka", "mikel",
    "pau", "biel", "arnau", "aleix", "genis", "roger", "ferran", "lluis",
    # Nombres, apodos y variantes de hombre habituales (para reducir los
    # "desconocidos" al inferir el sexo).
    "abdon", "andreu", "arthur", "artur", "besfort", "dylan", "edgar", "elias",
    "erik", "fede", "fran", "guillem", "jaume", "javi", "jonathan", "jordi",
    "lionel", "migue", "miquel", "nestor", "roel", "russell", "solomon",
    "walter", "xavi", "wen", "alex", "adria",
}

MUJER = {
    "maria", "ana", "carmen", "laura", "marta", "cristina", "sara", "paula",
    "lucia", "elena", "silvia", "raquel", "pilar", "rosa", "isabel", "nuria",
    "beatriz", "patricia", "sandra", "andrea", "alba", "irene", "julia",
    "clara", "nerea", "noelia", "rocio", "celia", "angela", "amparo", "esther",
    "yolanda", "mercedes", "montse", "montserrat", "inmaculada", "inma",
    "consuelo", "fina", "lupe", "helga", "sylvia", "sofia", "claudia", "carla",
    "emma", "martina", "valeria", "daniela", "vera", "olga", "eva", "susana",
    "teresa", "gloria", "dolores", "manuela", "antonia", "vanessa", "veronica",
    "natalia", "miriam", "tania", "lorena", "carolina", "victoria", "adriana",
    "monica", "gema", "alicia", "estefania", "elisa", "aida", "pepa", "conchi",
    "mar", "merche", "maite", "paqui", "reme", "cati", "marian", "marivi",
    "mari", "loli", "charo", "rosi", "tere", "mavi", "puri", "nati", "asun",
    "encarna", "vicenta", "kitty", "sol", "pepi", "chelo", "toñi", "toni",
    "nieves", "rosario", "concepcion", "remedios", "encarnacion", "milagros",
    "aurora", "blanca", "diana", "ines", "leire", "lidia", "marina", "nora",
    "abril", "aroa", "candela", "carmina", "desiree", "elvira", "erika",
    "estela", "eugenia", "fatima", "gemma", "ingrid", "iris", "jessica",
    "josefa", "juana", "laia", "lourdes", "macarena", "magdalena", "mireia",
    "nadia", "noemi", "noa", "ondina", "paloma", "pepita", "pura", "ruth",
    "sonia", "susi", "vicky", "virginia", "yaiza", "yolena", "africa", "amaia",
    "empar", "belen", "maribel", "presentacion", "karen", "aitana", "meritxell",
    "aroa", "ainhoa", "nagore", "izaskun", "itziar", "amparin", "lledo", "neus",
    "empar", "carmen", "consol", "roser", "montserrat", "pepa", "maria jose",
    # Nombres, apodos y variantes de mujer habituales (para reducir los
    # "desconocidos" al inferir el sexo).
    "alison", "anabel", "angeles", "angi", "angie", "anne", "asuncion", "carol",
    "catherine", "christiane", "conchin", "dacil", "dafne", "debbie", "eli",
    "elisabet", "elisabeth", "guiomar", "hajar", "ibtissam", "irune", "jennifer",
    "jenny", "joanne", "judith", "judidt", "juncal", "katy", "laly", "leonor",
    "leyre", "loles", "lolin", "luli", "luz", "mamen", "manoli", "mapi", "mariam",
    "mariapi", "marie", "mayte", "megan", "naomi", "nardi", "natali", "nathalie",
    "nelly", "nicole", "pauline", "remei", "sally", "sigourney", "soledad",
    "susan", "tati", "trini", "tanin", "mati", "emi",
}

# Primer token que indica "Maria ..." (Mª Carmen, M. Jose, etc.) -> mujer
MARIA_ABBR = {"m", "ma", "mª", "maria", "m.", "mari"}


def inferir_genero(nombre_completo: str) -> str:
    tokens = _norm(nombre_completo).replace(".", " ").split()
    if not tokens:
        return "Desconocido"
    n = tokens[0]
    if n in MARIA_ABBR:                 # "Mª Carmen", "M Jose"...
        return "Mujer"
    if n in HOMBRE:
        return "Hombre"
    if n in MUJER:
        return "Mujer"
    if n.endswith("a"):
        return "Mujer"
    if n.endswith(("o", "os")):
        return "Hombre"
    return "Desconocido"
