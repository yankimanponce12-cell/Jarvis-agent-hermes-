import logging
import threading
from pathlib import Path

from . import voice_config
from .tts_openvoice import OpenVoiceClient
from .tts_xtts import XTTSSpeaker

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

logger = logging.getLogger("jarvis.voice")


def _setup_logging():
    """Log a archivo, nunca a consola: el usuario no debe ver estos avisos
    tecnicos, solo lo que Jarvis dice (eso se imprime aparte, en main.py)."""
    if logger.handlers:
        return
    LOG_DIR.mkdir(exist_ok=True)
    handler = logging.FileHandler(LOG_DIR / "jarvis.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class VoiceEngine:
    """Motor de voz con DOS opciones intercambiables: XTTS v2 (local,
    clonado end-to-end, mas fiel pero pesado: ~2GB VRAM, ~3s por oracion) y
    OpenVoice V2 (subprocess aparte, ~4x mas rapido pero se parece un poco
    menos a la voz de referencia - ver benchmarks/).

    A proposito, NINGUNO de los dos se carga hasta que hace falta: solo el
    motor ACTIVO (segun voice_config) se carga en segundo plano al llamar
    iniciar(), y el otro se queda sin cargar (ni VRAM ni tiempo de arranque
    gastados en el que no se esta usando) hasta que el usuario cambia de
    voz desde el boton de configuracion (ver switch_engine)."""

    def __init__(self):
        _setup_logging()
        self._lock = threading.Lock()
        self._xtts: XTTSSpeaker | None = None
        self._openvoice: OpenVoiceClient | None = None
        self._active_engine: str | None = None
        self._loading_engine: str | None = None
        self._load_error: str | None = None

    def _load(self, engine: str):
        if engine == "xtts":
            if self._xtts is None:
                self._xtts = XTTSSpeaker()
        else:
            if self._openvoice is None:
                self._openvoice = OpenVoiceClient()
            self._openvoice.ensure_ready()

    def ensure_loaded(self, engine: str):
        """Bloquea hasta que `engine` este listo (lo carga si hace falta).
        Idempotente: si ya es el activo, vuelve al toque."""
        if self._active_engine == engine and self._load_error is None:
            return
        with self._lock:
            if self._active_engine == engine and self._load_error is None:
                return
            self._loading_engine = engine
            self._load_error = None
            try:
                self._load(engine)
                self._active_engine = engine
            except Exception as exc:
                self._load_error = str(exc)
                logger.error(f"No se pudo cargar el motor de voz '{engine}': {exc}")
                raise
            finally:
                self._loading_engine = None

    def switch_engine(self, engine: str):
        """Persiste la eleccion y dispara la carga en un hilo de fondo (no
        bloquea al llamador; el front sondea status() para la barra de
        progreso)."""
        voice_config.set_engine(engine)
        threading.Thread(target=self._safe_ensure_loaded, args=(engine,), daemon=True).start()

    def _safe_ensure_loaded(self, engine: str):
        try:
            self.ensure_loaded(engine)
        except Exception:
            pass  # el error ya quedo logueado y en status() via _load_error

    def status(self) -> dict:
        return {
            "current": self._active_engine,
            "configured": voice_config.get_engine(),
            "loading": self._loading_engine,
            "error": self._load_error,
        }

    def hablar(self, texto: str):
        if not texto:
            return
        engine = voice_config.get_engine()
        try:
            self.ensure_loaded(engine)
            if engine == "xtts":
                self._xtts.speak(texto)
            else:
                self._openvoice.speak(texto)
        except Exception as exc:
            logger.error(f"Motor de voz ({engine}) fallo, este mensaje no se pudo hablar: {exc}")

    def synthesize_wav_bytes(self, texto: str) -> bytes:
        engine = voice_config.get_engine()
        try:
            self.ensure_loaded(engine)
            if engine == "xtts":
                return self._xtts.synthesize_wav_bytes(texto)
            else:
                return self._openvoice.synthesize_wav_bytes(texto)
        except Exception as exc:
            logger.error(f"Motor de voz ({engine}) fallo al sintetizar audio para el telefono: {exc}")
            return b""

    def shutdown(self):
        if self._openvoice is not None:
            self._openvoice.stop()


_engine: VoiceEngine | None = None
# el hilo de voz (mic/wake word) y el del servidor HTTP para el telefono
# pueden llegar a llamar a iniciar() casi al mismo tiempo al arrancar; sin
# este lock ambos podrian ver _engine en None y crear dos VoiceEngine.
_engine_lock = threading.Lock()


def iniciar() -> VoiceEngine:
    """Crea el motor de voz y carga el ACTIVO (segun voice_config) una sola
    vez. Llamar al arrancar el programa, antes de usar hablar()."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = VoiceEngine()
                threading.Thread(
                    target=_engine._safe_ensure_loaded,
                    args=(voice_config.get_engine(),),
                    daemon=True,
                ).start()
    return _engine


def hablar(texto: str):
    """Funcion central para que cualquier parte de Jarvis hable. Si iniciar()
    no se llamo antes, se inicializa aqui mismo (mas lento la primera vez)."""
    iniciar().hablar(texto)


def synthesize_wav_bytes(texto: str) -> bytes:
    """Sintetiza `texto` con la voz de Jarvis y devuelve los bytes de un WAV
    (no lo reproduce en la PC). La usa el servidor del telefono."""
    return iniciar().synthesize_wav_bytes(texto)


def switch_engine(engine: str):
    """Cambia el motor de voz activo (llamado desde el boton de
    configuracion, via server.py). No bloquea: la carga pasa en un hilo de
    fondo, sondear status() para la barra de progreso."""
    iniciar().switch_engine(engine)


def status() -> dict:
    return iniciar().status()


def shutdown():
    """Corta el subprocess de OpenVoice si estaba corriendo, para no dejarlo
    huerfano al cerrar Jarvis."""
    if _engine is not None:
        _engine.shutdown()
