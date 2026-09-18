import datetime
import ipaddress
import secrets
import socket
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from jarvis import agent, brain, voice_output
from jarvis.commands import handle_command

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"
TOKEN_FILE = PROJECT_ROOT / "server_token.txt"
CERT_FILE = PROJECT_ROOT / "server_cert.pem"
KEY_FILE = PROJECT_ROOT / "server_key.pem"
DEFAULT_PORT = 8765


def _load_or_create_token() -> str:
    """El token protege el servidor frente a cualquier otro dispositivo en la
    misma red wifi (no solo tu telefono): sin esto, cualquiera conectado a la
    misma red podria abrir/cerrar programas o mandar mensajes de WhatsApp
    desde tu PC con solo saber la IP. Se genera una sola vez y se guarda en
    disco para no tener que pegarlo de nuevo en el telefono en cada arranque.

    Es un PIN de 4 digitos (no un token largo random) a proposito: Jarvis lo
    muestra en el HUD y lo dice en voz alta al pedirle el QR (ver
    commands.py), y un PIN corto es mucho mas facil de tipear a mano en el
    celular si hace falta. A cambio de esa comodidad, _token_ok() frena los
    intentos fallidos (ver _record_failed_attempt) para que no sea trivial
    probar las 10000 combinaciones por fuerza bruta en la red local."""
    if TOKEN_FILE.exists():
        existing = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = f"{secrets.randbelow(10000):04d}"
    TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


TOKEN = _load_or_create_token()

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="/static")


def _local_ip() -> str:
    """IP de la PC en la red local (no 127.0.0.1), para mostrarla al
    arrancar. El connect() UDP no manda ningun paquete real: solo le
    pregunta al sistema operativo que interfaz usaria para salir a
    internet, para saber cual es la IP 'de verdad' de esta maquina en la
    LAN aunque tenga varias tarjetas de red."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def mobile_url() -> str:
    """URL de la pagina movil con el token ya incluido, para el QR (ver
    qr_share.py) y para el mensaje que se imprime al arrancar el servidor."""
    return f"https://{_local_ip()}:{DEFAULT_PORT}/?token={TOKEN}"


def _get_or_create_cert() -> tuple[str, str]:
    """Certificado autofirmado para servir por HTTPS. Hace falta porque los
    navegadores (Chrome, Safari) solo permiten usar el microfono
    (getUserMedia, y por lo tanto la Web Speech API que usa app.js) en
    'contextos seguros': HTTPS, o http://localhost. Una pagina servida por
    http://<ip-de-la-lan> se trata como insegura y el navegador bloquea el
    microfono en silencio (el boton no hacia nada visible: esto es lo que
    estaba rompiendo el reconocimiento de voz en el celular).

    Se genera una sola vez y se reusa entre arranques (en vez de uno nuevo
    cada vez, como hace el modo 'adhoc' de Werkzeug) para que el navegador
    del celular no tenga que volver a aceptar la advertencia de seguridad
    en cada sesion."""
    if CERT_FILE.exists() and KEY_FILE.exists():
        return str(CERT_FILE), str(KEY_FILE)

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "jarvis.local")])

    san_names = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    try:
        san_names.append(x509.IPAddress(ipaddress.ip_address(_local_ip())))
    except ValueError:
        pass

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_names), critical=False)
        .sign(key, hashes.SHA256())
    )

    KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(CERT_FILE), str(KEY_FILE)


# PIN de 4 digitos = solo 10000 combinaciones posibles; este freno global
# (no hace falta por-IP, es una herramienta personal de un solo usuario)
# hace que probarlas todas por fuerza bruta tarde horas en vez de segundos.
_FAIL_LOCK_THRESHOLD = 8
_FAIL_LOCK_SECONDS = 30
_fail_state = {"count": 0, "locked_until": 0.0}


def _token_ok() -> bool:
    now = time.time()
    if now < _fail_state["locked_until"]:
        return False

    supplied = request.headers.get("X-Jarvis-Token") or request.args.get("token")
    if supplied == TOKEN:
        _fail_state["count"] = 0
        return True

    _fail_state["count"] += 1
    if _fail_state["count"] >= _FAIL_LOCK_THRESHOLD:
        _fail_state["locked_until"] = now + _FAIL_LOCK_SECONDS
    return False


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/api/health")
def api_health():
    if not _token_ok():
        return jsonify({"error": "token invalido"}), 401
    return jsonify({"ok": True})


@app.route("/api/command", methods=["POST"])
def api_command():
    if not _token_ok():
        return jsonify({"error": "token invalido"}), 401

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "falta 'text'"}), 400

    responses = []
    # escuchar=None: los flujos que piden dictado por microfono (mensajes de
    # WhatsApp, confirmar reproducir un video) ya manejan ese caso sin
    # romperse (ver commands.py), solo avisan que no pueden tomar dictado en
    # este modo. El microfono del telefono lo maneja el navegador (Web
    # Speech API en app.js), no este servidor.
    keep_going = handle_command(text, respond=responses.append, escuchar=None)
    return jsonify({"responses": responses, "exit": not keep_going})


@app.route("/api/voice-engine", methods=["GET"])
def api_voice_engine_get():
    """Motor de voz activo/configurado ahora mismo (xtts u openvoice), para
    que el boton de configuracion del telefono sepa que mostrar marcado y
    pueda sondear esto mientras el elegido todavia esta cargando (ver
    voice_output.status())."""
    if not _token_ok():
        return jsonify({"error": "token invalido"}), 401
    return jsonify(voice_output.status())


@app.route("/api/voice-engine", methods=["POST"])
def api_voice_engine_set():
    """Cambia el motor de voz (boton de configuracion del telefono). No
    bloquea: dispara la carga en un hilo de fondo y devuelve al toque; el
    telefono sondea GET /api/voice-engine para la barra de progreso."""
    if not _token_ok():
        return jsonify({"error": "token invalido"}), 401
    data = request.get_json(silent=True) or {}
    engine = data.get("engine")
    if engine not in ("xtts", "openvoice"):
        return jsonify({"error": "motor invalido, debe ser 'xtts' u 'openvoice'"}), 400
    voice_output.switch_engine(engine)
    return jsonify({"ok": True})


@app.route("/api/speak", methods=["POST"])
def api_speak():
    """Sintetiza texto con la voz clonada de Jarvis (el motor activo, ver
    /api/voice-engine) y devuelve el WAV para que el celular lo reproduzca.
    Se llama por separado de /api/command para que el telefono pueda
    mostrar la respuesta de texto de inmediato y recien despues esperar el
    audio (que puede tardar unos segundos en generarse)."""
    if not _token_ok():
        return jsonify({"error": "token invalido"}), 401

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "falta 'text'"}), 400

    audio = voice_output.synthesize_wav_bytes(text)
    if not audio:
        return jsonify({"error": "no se pudo generar el audio"}), 500
    return Response(audio, mimetype="audio/wav")


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Reinicia la memoria de conversacion de corto plazo (brain.py y
    agent.py), la misma que comparten la voz/texto de la PC y el celular (un
    solo proceso, un solo Brain y un solo Agent). Hay que resetear los dos:
    la charla pasa por el agente (agent.ask), y brain solo guarda su propio
    historial. El boton 'Lobotomizar' de la pagina movil llama aca."""
    if not _token_ok():
        return jsonify({"error": "token invalido"}), 401
    brain.reset()
    agent.reset()
    return jsonify({"ok": True})


def run_server(host="0.0.0.0", port=DEFAULT_PORT):
    cert_file, key_file = _get_or_create_cert()
    print(f"[jarvis.server] Servidor local escuchando en https://{_local_ip()}:{port}")
    print(f"[jarvis.server] Abrí esto en el navegador del celular (misma wifi):")
    print(f"[jarvis.server]   {mobile_url()}")
    print(f"[jarvis.server] (o decile a Jarvis 'mándame el QR' para escanearlo desde el HUD)")
    print(f"[jarvis.server] El certificado es autofirmado: el navegador va a avisar que la")
    print(f"[jarvis.server] conexion 'no es privada' la primera vez. Es esperado (no hay una")
    print(f"[jarvis.server] autoridad certificadora detras) -> 'Avanzado' > 'Continuar'. Sin")
    print(f"[jarvis.server] HTTPS el navegador bloquea el microfono por completo.")
    app.run(
        host=host,
        port=port,
        threaded=True,
        debug=False,
        use_reloader=False,
        ssl_context=(cert_file, key_file),
    )


if __name__ == "__main__":
    run_server()
