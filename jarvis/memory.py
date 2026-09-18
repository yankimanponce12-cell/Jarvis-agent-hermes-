"""Memoria persistente estilo Letta/MemGPT, pero liviana: sin base
vectorial ni modelo de embeddings extra (la VRAM ya esta al limite con
Whisper+XTTS+el agente). Dos niveles:

- Memoria "core": datos fijos sobre el usuario que se inyectan enteros en
  cada prompt del agente. Chica a proposito. El agente NO la actualiza por
  su cuenta (se probo con una herramienta de tool-calling para esto y
  bajaba mucho la confiabilidad, ver notas en agent.py): se guarda por
  deteccion de frase en commands.py. El nombre tiene su propio campo
  (get_name/set_name) separado de la lista de datos sueltos
  (append_usuario_fact), porque es el dato mas basico de todos y no
  deberia poder quedar afuera si la lista de datos sueltos se llena.
- Memoria "archival": bitacora de recuerdos puntuales (anecdotas, cosas
  dichas en el pasado), sin limite de tamaño, buscable por palabras clave
  (sin embeddings) via search_archival.

Todo vive en un unico archivo SQLite (jarvis_memory.db en la raiz del
proyecto, gitignoreado), separado de la conversacion en curso de agent.py
(esa es de corto plazo y se resetea con lobotomizar; esto no)."""

import contextlib
import datetime
import re
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "jarvis_memory.db"

MAX_USUARIO_FACTS = 25  # tope de la memoria core: se descartan los mas viejos
_STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "de", "del", "que", "y", "a",
    "en", "es", "con", "por", "para", "se", "su", "sus", "lo", "le", "me",
    "mi", "tu", "no", "si", "esta", "esto", "ese", "eso",
}


def _init_schema(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS core_memory (block TEXT PRIMARY KEY, content TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS archival_memory ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, content TEXT NOT NULL)"
    )


@contextlib.contextmanager
def _db():
    """Conexion SQLite de un solo uso: commitea (o revierte, si hubo
    excepcion) y SIEMPRE cierra la conexion antes de salir. Dejar
    conexiones sin cerrar es justo el tipo de lock de archivo en Windows
    que ya dio problemas en este proyecto (ver notas de whatsapp_profile/
    y CosyVoice2)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        _init_schema(conn)
        with conn:
            yield conn
    finally:
        conn.close()


def get_name() -> str:
    """El nombre del usuario, o "" si todavia no lo dijo."""
    with _db() as conn:
        row = conn.execute(
            "SELECT content FROM core_memory WHERE block = 'nombre'"
        ).fetchone()
    return row[0].strip() if row else ""


def set_name(nombre: str):
    nombre = nombre.strip()
    if not nombre:
        return
    with _db() as conn:
        conn.execute(
            "INSERT INTO core_memory (block, content) VALUES ('nombre', ?) "
            "ON CONFLICT(block) DO UPDATE SET content = excluded.content",
            (nombre,),
        )


def get_core_memory_text() -> str:
    """Texto listo para pegar en el system prompt del agente. Vacio (string
    vacio) si todavia no se aprendio nada del usuario."""
    partes = []
    nombre = get_name()
    if nombre:
        partes.append(f"El usuario se llama {nombre}.")

    with _db() as conn:
        row = conn.execute(
            "SELECT content FROM core_memory WHERE block = 'usuario'"
        ).fetchone()
    if row and row[0].strip():
        partes.append(f"Otros datos que ya sabes del usuario:\n{row[0]}")

    if not partes:
        return ""
    return "\n\n".join(partes)


def append_usuario_fact(dato: str):
    """Agrega un dato nuevo a la memoria core del usuario. Si ya hay
    MAX_USUARIO_FACTS guardados, se descarta el mas viejo (la memoria core
    tiene que quedarse chica: se manda entera en cada prompt)."""
    dato = dato.strip()
    if not dato:
        return
    with _db() as conn:
        row = conn.execute(
            "SELECT content FROM core_memory WHERE block = 'usuario'"
        ).fetchone()
        hechos = row[0].split("\n") if row and row[0].strip() else []
        hechos.append(f"- {dato}")
        hechos = hechos[-MAX_USUARIO_FACTS:]
        conn.execute(
            "INSERT INTO core_memory (block, content) VALUES ('usuario', ?) "
            "ON CONFLICT(block) DO UPDATE SET content = excluded.content",
            ("\n".join(hechos),),
        )


def add_archival(texto: str):
    texto = texto.strip()
    if not texto:
        return
    ahora = datetime.datetime.now().isoformat(timespec="seconds")
    with _db() as conn:
        conn.execute(
            "INSERT INTO archival_memory (created_at, content) VALUES (?, ?)",
            (ahora, texto),
        )


def _keywords(texto: str) -> set[str]:
    palabras = re.findall(r"\w+", texto.lower())
    return {p for p in palabras if p not in _STOPWORDS and len(p) > 2}


def search_archival(consulta: str, limit: int = 5) -> list[str]:
    """Busqueda simple por coincidencia de palabras clave (sin embeddings:
    no hay VRAM de sobra para un modelo mas). Ordena por cantidad de
    palabras en comun y, a igualdad, por lo mas reciente primero. Alcanza
    para el volumen de datos de un asistente personal."""
    claves = _keywords(consulta)
    if not claves:
        return []
    with _db() as conn:
        filas = conn.execute(
            "SELECT created_at, content FROM archival_memory ORDER BY id DESC"
        ).fetchall()

    puntuadas = []
    for created_at, content in filas:
        score = len(claves & _keywords(content))
        if score > 0:
            puntuadas.append((score, created_at, content))
    # mayor coincidencia primero; a igualdad de score, lo mas reciente
    # primero (los timestamps ISO ordenan igual lexicografica que
    # cronologicamente, asi que un solo sort con reverse=True alcanza)
    puntuadas.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [f"[{created_at}] {content}" for _, created_at, content in puntuadas[:limit]]
