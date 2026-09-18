"""Persiste que motor de voz esta activo (xtts u openvoice), para que el
boton de configuracion del HUD/telefono sobreviva a reinicios de Jarvis."""

import json
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent / "voice_config.json"
VALID_ENGINES = ("xtts", "openvoice")
DEFAULT_ENGINE = "openvoice"


def get_engine() -> str:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            engine = data.get("engine")
            if engine in VALID_ENGINES:
                return engine
        except Exception:
            pass
    return DEFAULT_ENGINE


def set_engine(engine: str):
    if engine not in VALID_ENGINES:
        raise ValueError(f"motor de voz invalido: {engine!r} (valen: {VALID_ENGINES})")
    CONFIG_FILE.write_text(json.dumps({"engine": engine}), encoding="utf-8")
