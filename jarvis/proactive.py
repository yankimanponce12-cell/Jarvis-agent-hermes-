import datetime
import random
import threading
import time

from jarvis import watch, weather

IDLE_CHECKIN_SECONDS = 20 * 60  # avisa si pasaron 20 min sin ningun comando
CHECK_INTERVAL_SECONDS = 30  # cada cuanto revisa si ya paso el tiempo de espera
WEATHER_CHECK_INTERVAL_SECONDS = 15 * 60  # cada cuanto se consulta el pronostico

CHECKIN_PHRASES = [
    "¿Sigue ahí, señor? Aquí estoy si necesita algo.",
    "Sigo despierto, señor, por si se le ofrece algo.",
    "Un momento tranquilo, señor. Avíseme si necesita algo.",
]


def greeting_for_time(now: datetime.datetime | None = None) -> str:
    hour = (now or datetime.datetime.now()).hour
    if 5 <= hour < 12:
        momento = "Buenos días"
    elif 12 <= hour < 20:
        momento = "Buenas tardes"
    else:
        momento = "Buenas noches"
    return f"{momento}, señor. Jarvis en línea y a sus órdenes."


class ProactiveSpeaker:
    """Hilo en segundo plano que hace que Jarvis hable por su cuenta sin que
    se le pregunte, de dos formas: (1) un comentario generico si pasa mucho
    tiempo sin actividad, y (2) avisos con contenido util de verdad (por
    ahora, lluvia inminente segun el pronostico) - esto ultimo es la parte
    de 'proactividad con contenido real' inspirada en el Jarvis de las
    peliculas, en vez de solo un '¿sigue ahi?'. No interrumpe la escucha:
    usa el mismo respond()/flush que el resto de la conversacion."""

    def __init__(self, respond, flush_pending_audio, hud=None):
        self._respond = respond
        self._flush_pending_audio = flush_pending_audio
        self._hud = hud
        self._last_activity = time.time()
        self._last_weather_check = 0.0
        self._rain_alert_date = None  # dia (date) en que ya se aviso de lluvia, para no repetir el mismo dia
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def notify_activity(self):
        self._last_activity = time.time()

    def _check_rain(self):
        hoy = datetime.date.today()
        if self._rain_alert_date == hoy:
            return  # ya se aviso hoy, no repetir el mismo aviso todo el dia
        try:
            aviso = weather.check_rain_soon()
        except Exception:
            # un aviso proactivo nunca debe poder tirar este hilo entero
            # (si eso pasara, tambien se perderia el "¿sigue ahi?" de mas
            # abajo para siempre, no solo el aviso de lluvia)
            aviso = None
        if aviso:
            self._respond(aviso)
            self._flush_pending_audio()
            self._rain_alert_date = hoy
            self.notify_activity()

    def _push_weather_to_watch(self):
        # no habla nada por voz, solo actualiza el reloj y el HUD de la PC;
        # si falla (sin internet, reloj apagado, etc.) simplemente no actualiza
        try:
            datos = weather.get_current_compact()
        except Exception:
            datos = None
        if not datos:
            return
        watch.send_weather(datos["temp"], datos["precip"], datos["humidity"], datos["pressure"], datos["wind"])
        if self._hud:
            self._hud.set_weather(datos["temp"], datos["desc"])

    def _run(self):
        while not self._stop_event.wait(CHECK_INTERVAL_SECONDS):
            now = time.time()
            if now - self._last_weather_check >= WEATHER_CHECK_INTERVAL_SECONDS:
                self._last_weather_check = now
                self._check_rain()
                self._push_weather_to_watch()

            if time.time() - self._last_activity >= IDLE_CHECKIN_SECONDS:
                self._respond(random.choice(CHECKIN_PHRASES))
                self._flush_pending_audio()
                self.notify_activity()  # no repetir de inmediato
