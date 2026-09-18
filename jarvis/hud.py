import json
import logging
import threading
from pathlib import Path

import webview

from jarvis import watch

UI_DIR = Path(__file__).resolve().parent / "ui"
logger = logging.getLogger("jarvis.hud")


class Hud:
    """Ventana HUD flotante (estilo JARVIS de Iron Man) que muestra el estado
    actual de Jarvis (escuchando/pensando/hablando) y subtitulos con lo que se
    dijo y lo que respondio. Corre sobre pywebview; si la ventana aun no esta
    lista o pywebview falla, los métodos no lanzan excepcion (el asistente
    debe poder seguir funcionando sin interfaz)."""

    def __init__(self):
        self.window = None

    def create_window(self, js_api=None):
        # pywebview/WebView2 no soporta transparencia real hacia el escritorio
        # en Windows (queda en blanco en vez de dejar ver lo de atras: issue
        # r0x0r/pywebview#1611), asi que en vez de eso se usa un panel oscuro
        # tipo "visor" que combina con el color de fondo del propio HTML.
        #
        # No se fija x/y a mano: webview.screens todavia no refleja el
        # monitor real en este punto (el toolkit nativo no esta inicializado
        # hasta webview.start()), lo que en pantallas con escalado de Windows
        # hacia terminar la ventana fuera de la pantalla. easy_drag=True deja
        # que el usuario la arrastre a la esquina que prefiera.
        #
        # js_api expone metodos de Python (minimizar/maximizar/cerrar) que
        # los botones de la barra de titulo (ver ui-src/src/App.jsx) llaman via
        # window.pywebview.api.*, ya que al ser frameless no hay barra nativa
        # de Windows con esos controles.
        #
        # resizable=True (antes False) para que maximizar tenga sentido; el
        # CSS (ver ui/style.css) usa unidades responsivas (clamp/vmin) para
        # que el HUD se vea bien tanto en la ventana chica de siempre como
        # maximizado a pantalla completa, no solo estirado con espacio vacio.
        self.window = webview.create_window(
            "Jarvis",
            str(UI_DIR / "index.html"),
            width=420,
            height=400,
            min_size=(380, 360),
            frameless=True,
            easy_drag=True,
            on_top=True,
            background_color="#05080a",
            resizable=True,
            js_api=js_api,
        )
        return self.window

    def _run_js(self, code: str):
        if self.window is None:
            return
        try:
            self.window.evaluate_js(code)
        except Exception as exc:
            logger.error(f"No se pudo actualizar el HUD: {exc}")

    def set_state(self, state: str):
        """state: 'cargando' | 'inactivo' | 'escuchando' | 'pensando' | 'hablando'"""
        self._run_js(f"setState({json.dumps(state)})")
        watch.send_state(state)

    def set_heard(self, text: str):
        self._run_js(f"setHeard({json.dumps(text)})")

    def set_response(self, text: str):
        self._run_js(f"setResponse({json.dumps(text)})")
        watch.send_response(text)

    def set_weather(self, temp: int, desc: str):
        self._run_js(f"setWeather({json.dumps(temp)}, {json.dumps(desc)})")

    def show_qr(self, data_uri: str, token: str = "", seconds: float = 40):
        """Muestra el QR (data URI base64, ver qr_share.py) como overlay
        sobre el HUD, junto con el PIN en texto grande (por si el usuario
        prefiere tipearlo a mano en vez de escanear). Se oculta solo
        despues de `seconds`, para que no se quede tapando el anillo/
        subtitulos indefinidamente si el usuario se olvida de cerrarlo."""
        self._run_js(f"showQr({json.dumps(data_uri)}, {json.dumps(token)})")
        if seconds:
            threading.Timer(seconds, self.hide_qr).start()

    def hide_qr(self):
        self._run_js("hideQr()")
