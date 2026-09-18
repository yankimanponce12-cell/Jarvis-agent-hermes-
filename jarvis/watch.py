import logging
import threading
from pathlib import Path

import requests
from zeroconf import Zeroconf

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

logger = logging.getLogger("jarvis.watch")


def _setup_logging():
    """Log a archivo, nunca a consola: mismo criterio que voice_output.py
    (ver _setup_logging ahi). Si el reloj esta apagado o fuera de rango,
    Jarvis debe seguir funcionando igual sin que el usuario vea nada raro
    en la consola."""
    if logger.handlers:
        return
    LOG_DIR.mkdir(exist_ok=True)
    handler = logging.FileHandler(LOG_DIR / "jarvis.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


_setup_logging()

# El firmware del reloj (ESP32-S3) se anuncia por mDNS con MDNS.begin("jarvis-watch")
# + MDNS.addService("http", "tcp", 80), lo que registra esta instancia de servicio.
SERVICE_TYPE = "_http._tcp.local."
SERVICE_NAME = "jarvis-watch._http._tcp.local."
WATCH_PORT = 80
WATCH_ROUTE = "/jarvis"
WEATHER_ROUTE = "/weather"  # separado de /jarvis: el clima no toca el estado de la conversacion
HTTP_TIMEOUT = 0.7       # sub-segundo: nunca debe sentirse lento si el reloj no responde
MDNS_TIMEOUT_MS = 1500

_zeroconf = None
_zeroconf_lock = threading.Lock()


def _get_zeroconf() -> Zeroconf:
    global _zeroconf
    if _zeroconf is None:
        with _zeroconf_lock:
            if _zeroconf is None:
                _zeroconf = Zeroconf()
    return _zeroconf


_cached_ip = None
_cache_lock = threading.Lock()


def _resolve_ip(force: bool = False):
    """IP del reloj (jarvis-watch.local) resuelta por mDNS, o None si no se
    encontro. Se cachea para no tener que resolver de nuevo en cada mensaje;
    solo se vuelve a resolver si el ultimo POST fallo (force=True, ver _post)."""
    global _cached_ip
    if _cached_ip and not force:
        return _cached_ip

    try:
        info = _get_zeroconf().get_service_info(SERVICE_TYPE, SERVICE_NAME, timeout=MDNS_TIMEOUT_MS)
        addresses = info.parsed_addresses() if info else []
        if not addresses:
            return None
        with _cache_lock:
            _cached_ip = addresses[0]
        return _cached_ip
    except Exception as exc:
        logger.error(f"No se pudo resolver jarvis-watch.local por mDNS: {exc}")
        return None


def _post(payload: dict, route: str = WATCH_ROUTE):
    """Manda el POST al reloj. Nunca lanza excepcion hacia el llamador: si
    el reloj esta apagado, fuera de rango, o la resolucion mDNS fallo, se
    loguea y listo (Jarvis debe seguir funcionando igual sin el reloj)."""
    global _cached_ip

    ip = _resolve_ip()
    if ip is None:
        return

    try:
        requests.post(f"http://{ip}:{WATCH_PORT}{route}", json=payload, timeout=HTTP_TIMEOUT)
        return
    except requests.RequestException:
        # la IP cacheada puede haber quedado vieja (el reloj se reinicio,
        # cambio de red, etc.): se invalida y se intenta una vez mas antes
        # de rendirse en silencio
        with _cache_lock:
            _cached_ip = None

    ip = _resolve_ip(force=True)
    if ip is None:
        return
    try:
        requests.post(f"http://{ip}:{WATCH_PORT}{route}", json=payload, timeout=HTTP_TIMEOUT)
    except requests.RequestException as exc:
        logger.error(f"No se pudo mandar el estado al reloj: {exc}")


def _send_async(payload: dict, route: str = WATCH_ROUTE):
    threading.Thread(target=_post, args=(payload, route), daemon=True).start()


def send_state(state: str):
    """Le avisa al reloj que Jarvis cambio de estado (cargando/inactivo/
    escuchando/pensando/hablando), sin tocar el texto que esta mostrando."""
    _send_async({"state": state})


def send_response(text: str):
    """Le manda al reloj lo que Jarvis acaba de decir, para que lo muestre
    (con scroll si no entra) mientras esta en estado 'hablando'."""
    _send_async({"state": "hablando", "text": text})


def send_weather(temp: int, precip: float, humidity: int, pressure: int, wind: int):
    """Le manda al reloj las condiciones actuales (temperatura, precipitacion,
    humedad, presion, viento) para que las muestre en la carátula de estatus.
    Va por su propio endpoint (/weather), separado de /jarvis, porque no
    tiene nada que ver con el estado de la conversacion."""
    _send_async(
        {"temp": temp, "precip": precip, "humidity": humidity, "pressure": pressure, "wind": wind},
        route=WEATHER_ROUTE,
    )
