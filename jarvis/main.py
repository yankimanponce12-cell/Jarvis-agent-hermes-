import os
import sys
import threading

# la consola de Windows usa cp1252 por defecto, que no puede imprimir
# muchos caracteres (emojis/simbolos en titulos de YouTube, respuestas de
# la IA, etc.) y eso hacia crashear todo el programa en un print() suelto;
# se fuerza utf-8 con reemplazo silencioso de lo que no se pueda mostrar.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import webview

from jarvis import agent, brain
from jarvis import server as jarvis_server
from jarvis import whatsapp
from jarvis.commands import handle_command
from jarvis.hud import Hud
from jarvis.listener import VoiceListener
from jarvis.proactive import ProactiveSpeaker, greeting_for_time
from jarvis import voice_output
from jarvis.voice_output import hablar, iniciar
from jarvis.wakeword import extract_command


def run_wake_word_mode(listener: VoiceListener, hud: Hud):
    def respond(msg: str):
        print(msg)
        hud.set_state("hablando")
        hud.set_response(msg)
        hablar(msg)
        hud.set_state("inactivo")

    def escuchar():
        hud.set_state("escuchando")
        respuesta = listener.listen_and_transcribe(on_partial=hud.set_heard)
        print(f"Escuche: {respuesta}")
        hud.set_heard(respuesta)
        return respuesta

    print("Jarvis escuchando en segundo plano. Di 'Jarvis, <comando>' (Ctrl+C para salir).")
    # el saludo no debe bloquear el arranque de la escucha: respond() llama
    # a hablar(), que espera a que XTTS termine de cargar (ver _run_jarvis
    # en main.py) si todavia no esta listo. En un hilo aparte, el saludo se
    # dice apenas XTTS este listo pero Jarvis ya queda escuchando la wake
    # word de inmediato en vez de esperarlo tambien.
    threading.Thread(target=respond, args=(greeting_for_time(),), daemon=True).start()
    listener.flush_pending_audio()

    proactive = ProactiveSpeaker(respond, listener.flush_pending_audio, hud=hud)
    proactive.start()

    try:
        for text in listener.listen_forever(on_partial=hud.set_heard):
            command = extract_command(text)
            if command is None:
                continue  # no empezo con la wake word, se ignora

            proactive.notify_activity()
            print(f"Escuche: {text}")
            hud.set_heard(text)
            if not command:
                respond("Dígame, señor, ¿en qué puedo ayudarle?")
                listener.flush_pending_audio()
                continue

            hud.set_state("pensando")
            keep_going = handle_command(command, respond=respond, escuchar=escuchar, hud=hud)
            hud.set_state("inactivo")
            listener.flush_pending_audio()  # evita que se escuche a si mismo
            if not keep_going:
                break
    except KeyboardInterrupt:
        print("\nCerrando Jarvis.")
    finally:
        proactive.stop()


def run_push_to_talk_mode(listener: VoiceListener, hud: Hud):
    def respond(msg: str):
        print(msg)
        hud.set_state("hablando")
        hud.set_response(msg)
        hablar(msg)
        hud.set_state("inactivo")

    def escuchar():
        hud.set_state("escuchando")
        respuesta = listener.listen_and_transcribe(on_partial=hud.set_heard)
        print(f"Escuche: {respuesta}")
        hud.set_heard(respuesta)
        return respuesta

    print("Jarvis listo (modo push-to-talk).")
    hud.set_state("inactivo")
    while True:
        key = input("\n[Enter] para hablar (o 'q' para salir): ")
        if key.strip().lower() == "q":
            print("Cerrando Jarvis.")
            break
        text = escuchar()
        hud.set_state("pensando")
        keep_going = handle_command(text, respond=respond, escuchar=escuchar, hud=hud)
        hud.set_state("inactivo")
        if not keep_going:
            break


def _shutdown(hud: Hud):
    """Se llama al despedirse de Jarvis (palabra de salida) o si el
    programa se corta por cualquier otro motivo (Ctrl+C, error): cierra
    todo lo que Jarvis pudo haber dejado corriendo en segundo plano (el
    Brave de WhatsApp Web) y la ventana del HUD, para que no quede nada
    huerfano despues de decir 'adios'."""
    print("Cerrando procesos de Jarvis...")
    whatsapp.close()
    voice_output.shutdown()
    if hud.window is not None:
        try:
            hud.window.destroy()
        except Exception:
            pass


def _run_jarvis(hud: Hud):
    hud.set_state("cargando")
    # El motor de voz activo (XTTS u OpenVoice, ver voice_config.py/el boton
    # de configuracion) tarda de unos segundos a ~20s en cargar segun cual
    # sea, contra ~1-2s de Whisper, y no hace falta tenerlo listo para poder
    # ESCUCHAR: se carga en un hilo aparte para que la escucha de la wake
    # word (y el servidor del celular) arranquen apenas Whisper esta listo,
    # sin esperar tambien a la voz. Se dispara ANTES de construir
    # VoiceListener() (no despues) para que las dos cargas pesadas se
    # solapen en paralelo en vez de sumarse una atras de la otra - ahorra
    # varios segundos del arranque total. hablar() ya espera a iniciar()
    # (que es idempotente, ver voice_output.py) si la primera respuesta
    # llega antes de que termine de cargar esa unica vez. El motor que NO
    # esta activo no se toca aca (se carga recien si se cambia de voz).
    print("Inicializando motor de voz en segundo plano...")
    threading.Thread(target=iniciar, daemon=True).start()
    listener = VoiceListener()
    hud.set_state("inactivo")
    try:
        if "--push-to-talk" in sys.argv:
            run_push_to_talk_mode(listener, hud)
        else:
            run_wake_word_mode(listener, hud)
    finally:
        _shutdown(hud)


class Api:
    """Metodos expuestos al HUD (jarvis/ui-src/src/App.jsx, compilado a ui/) via
    window.pywebview.api.*, ya que al ser una ventana frameless no hay
    controles nativos de Windows (minimizar/cerrar) ni forma de escribirle
    a Jarvis salvo por este puente."""

    def __init__(self, hud: Hud):
        self.hud = hud
        self._maximized = False

    def minimize(self):
        if self.hud.window is not None:
            try:
                self.hud.window.minimize()
            except Exception:
                pass

    def toggle_maximize(self):
        if self.hud.window is None:
            return
        try:
            if self._maximized:
                self.hud.window.restore()
            else:
                self.hud.window.maximize()
            self._maximized = not self._maximized
        except Exception:
            pass

    def close(self):
        # Mismo camino que al despedirse por voz: mata el Brave/chromedriver
        # de WhatsApp y destruye la ventana antes de terminar el proceso,
        # para que el boton de cerrar no deje nada de Jarvis corriendo en
        # segundo plano.
        _shutdown(self.hud)
        os._exit(0)

    def send_text(self, text: str):
        """Comando escrito desde el campo de texto del HUD: se procesa con
        la misma logica que un comando dicho por voz (handle_command), y
        Jarvis responde hablando por los parlantes de la PC igual que
        siempre. escuchar=None porque tipear no graba audio (los flujos que
        piden dictado, como mandar un WhatsApp, ya avisan que no pueden
        tomarlo en este modo en vez de romperse, ver commands.py)."""
        text = (text or "").strip()
        if not text:
            return

        def respond(msg: str):
            print(msg)
            self.hud.set_state("hablando")
            self.hud.set_response(msg)
            hablar(msg)
            self.hud.set_state("inactivo")

        self.hud.set_heard(text)
        self.hud.set_state("pensando")
        keep_going = handle_command(text, respond=respond, escuchar=None, hud=self.hud)
        self.hud.set_state("inactivo")
        if not keep_going:
            self.close()

    def get_voice_status(self):
        """Motor de voz activo/configurado ahora mismo, para el panel de
        configuracion del HUD (ver App.jsx). pywebview serializa el dict
        devuelto y lo entrega como resultado de la Promise de JS."""
        return voice_output.status()

    def set_voice_engine(self, engine: str):
        """Cambia el motor de voz (panel de configuracion del HUD). No
        bloquea: dispara la carga en un hilo de fondo, el HUD sondea
        get_voice_status() para la barra de progreso."""
        if engine not in ("xtts", "openvoice"):
            return
        voice_output.switch_engine(engine)

    def lobotomizar(self):
        """Reinicia la conversacion de corto plazo (brain.py y agent.py) sin
        cerrar Jarvis: para poder cambiar de tema de golpe sin que arrastre
        contexto de lo que se hablo antes ('que no se le vaya el rollo').
        No borra la memoria de largo plazo (jarvis/memory.py): eso es
        informacion persistente del usuario, no contexto de la charla
        actual, y no tiene sentido perderla solo por cambiar de tema."""
        brain.reset()
        agent.reset()
        msg = "Memoria reiniciada, señor. Empecemos de cero."
        print(msg)
        self.hud.set_state("hablando")
        self.hud.set_response(msg)
        hablar(msg)
        self.hud.set_state("inactivo")


def main():
    hud = Hud()
    api = Api(hud)
    hud.create_window(js_api=api)
    # servidor HTTP para controlar Jarvis desde el celular (misma red wifi):
    # no depende de XTTS/Whisper, solo de la logica de commands.py, asi que
    # se puede levantar en un hilo aparte sin retrasar el arranque del HUD.
    # daemon=True para que no impida cerrar el proceso al salir.
    threading.Thread(target=jarvis_server.run_server, daemon=True).start()
    webview.start(_run_jarvis, (hud,))
    # _shutdown() ya cerro WhatsApp y la ventana del HUD para cuando
    # webview.start() regresa aqui; se fuerza el cierre del proceso por si
    # queda algun hilo o handle de WebView2 vivo, para que "adios" de
    # verdad cierre todo (no solo deje de escuchar).
    os._exit(0)


if __name__ == "__main__":
    main()
