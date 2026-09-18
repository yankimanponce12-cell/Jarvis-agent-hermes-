import datetime
import re

from . import actions, agent, brain, memory, weather, whatsapp

EXIT_WORDS = [
    "adios", "adios jarvis", "hasta luego", "cierra jarvis",
    "termina jarvis", "nos vemos", "bye", "chao", "ya me voy",
    "ya termine", "apagate",
]
LEADING_ARTICLES = re.compile(r"^(el|la|los|las|un|una)\s+")
_MSG_VERB_RE = re.compile(r"^(?:mandale|manda|enviale|envia)\s+(?:un\s+)?mensaje\s+a\s+(.+)$")
_MSG_WRITE_RE = re.compile(r"^escribele\s+(?:un\s+mensaje\s+)?a\s+(.+)$")
_WEATHER_CITY_RE = re.compile(r"\bclima\b.*?\ben\s+(.+)$")
# la memoria (jarvis/memory.py) NO pasa por el agente con tool-calling: se
# probo y con muchas herramientas a la vez (el agente ya tiene 7 propias)
# Qwen2.5:7b se vuelve poco confiable (ver notas en agent.py). Se detecta
# por frase directamente aca en vez de dejar que el modelo decida.
# el nombre tiene su propio patron (no alcanza con esperar un "acordate
# que..." explicito: en la practica la gente simplemente dice "me llamo
# X" al pasar, y ese dato es demasiado basico como para poder perderse)
_NAME_RE = re.compile(r"\b(?:me llamo|mi nombre es|llamame)\s+([a-z]+(?:\s+[a-z]+){0,2})\b")
_MEMORY_SAVE_RE = re.compile(r"^(?:acordate|acuerdate|recorda|recuerda|anota|apunta)\s+que\s+(.+)$")
_MEMORY_RECALL_RE = re.compile(
    r"\bque\s+(?:recordas|recuerdas|sabes)\b.*"
    r"\b(?:de mi|sobre mi|de lo que|que te dije|que dije|que hablamos|que paso|que conversamos|acerca de mi)\b"
)
DEMO_PHRASES = [
    "que puedes hacer", "que sabes hacer", "hazme una demo", "hagamos una demo",
    "muestrame que puedes hacer", "muestrame tus funciones", "que acciones tienes",
    "cuales son tus funciones", "dame una demostracion", "hazme una demostracion",
]
# guion fijo (no generado por IA): para una demo importa que sea preciso
# sobre lo que Jarvis realmente puede hacer, no que suene mas natural a
# costa de arriesgarse a que el agente invente una funcion que no existe
DEMO_TEXT = (
    "Buenas, señor. Le muestro rápido lo que puedo hacer. Puedo mandar "
    "mensajes de WhatsApp por usted, dictándolos por voz. Busco y "
    "reproduzco lo que quiera en YouTube, o hago búsquedas en Google. "
    "Abro y cierro aplicaciones de la computadora, controlo el volumen, y "
    "le digo la hora o la fecha cuando la necesite. También consulto el "
    "clima, de aquí o de cualquier ciudad que me diga. Si quiere "
    "conectarse desde el celular, le muestro un código QR para "
    "controlarme desde ahí. Y tengo memoria: me acuerdo de su nombre, de "
    "lo que me pida que anote, y puedo buscarlo después si se lo "
    "pregunta. Todo lo demás, si no encaja en ninguna de estas acciones, "
    "lo converso con usted directamente, como ahora mismo. Esa es la "
    "demostración, señor. A sus órdenes."
)
_DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
_MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
_ACCENTS = str.maketrans("áéíóúñ", "aeioun")
AFFIRMATIVE_WORDS = [
    "si", "sii", "siii", "dale", "va", "claro", "obvio", "porfavor",
    "afirmativo", "ok", "okay", "bueno", "correcto", "exacto",
    "efectivamente", "adelante", "hazlo", "simon", "sale", "de una",
    "por supuesto", "claro que si", "asi es", "eso es", "va que va",
]
NEGATIVE_WORDS = [
    "no", "nel", "negativo", "para nada", "mejor no", "no gracias",
    "ni loco", "paso",
]


def _clean(s: str) -> str:
    s = s.strip().strip(".,!?¿¡")
    return LEADING_ARTICLES.sub("", s).strip()


def _sir(msg: str) -> str:
    """Le da el tono formal del JARVIS de Iron Man, tratando al usuario de
    'señor'."""
    return f"{msg.rstrip('.')}, señor."


def _matches_any(cleaned: str, phrases: list[str]) -> bool:
    # las frases con espacio se buscan como substring; las palabras sueltas
    # se comparan por token completo para no confundir "va" con "estaba"
    tokens = cleaned.split()
    return any(p in cleaned if " " in p else p in tokens for p in phrases)


def _is_affirmative(text: str):
    """True/False si la respuesta suena claramente a 'si'/'no' por palabras
    clave. None si no se pudo determinar asi, y hay que preguntarle a la IA
    para que entienda el contexto (p.ej. sinonimos no listados)."""
    cleaned = text.lower().strip().strip(".,!?¿¡").translate(_ACCENTS)
    if _matches_any(cleaned, NEGATIVE_WORDS):
        return False
    if _matches_any(cleaned, AFFIRMATIVE_WORDS):
        return True
    return None


def _confirmed(text: str) -> bool:
    """Version final usada al confirmar acciones: si las palabras clave no
    alcanzan, se le pregunta al modelo local. Si tampoco se pudo determinar
    (IA caida, respuesta ambigua, no se entendio nada), se trata como 'no'
    para no reproducir algo sin estar seguro de que el usuario lo pidio."""
    resultado = _is_affirmative(text)
    if resultado is None:
        resultado = brain.classify_yes_no(text)
    return bool(resultado)


def _extract_message_contact(t: str):
    """Si el texto pide mandar un mensaje ('manda/envia/escribele... a
    <contacto>'), devuelve el nombre del contacto. Si no, None."""
    m = _MSG_VERB_RE.match(t) or _MSG_WRITE_RE.match(t)
    return _clean(m.group(1)) if m else None


def handle_command(text: str, respond=print, escuchar=None, hud=None) -> bool:
    """Ejecuta el comando reconocido. respond() se usa para cada mensaje de
    salida (por defecto solo imprime; main.py le pasa una version que ademas
    habla). escuchar(), si se pasa, graba y transcribe una respuesta corta
    del usuario (se usa para la confirmacion de YouTube). hud, si se pasa,
    permite mostrar cosas visuales en la ventana (p.ej. el QR del celular).
    Devuelve False si el usuario pidio salir."""
    # se quitan tildes porque el reconocimiento de voz no siempre las pone
    # igual que el texto con el que comparamos (p.ej. "que hora es" vs "qué hora es")
    t = text.lower().strip().strip(".,!?¿¡").translate(_ACCENTS)

    if not t:
        respond(_sir("No entendí nada, intente de nuevo"))
        return True

    if _matches_any(t, EXIT_WORDS):
        respond(_sir("Hasta luego"))
        return False

    if _matches_any(t, DEMO_PHRASES):
        respond(DEMO_TEXT)
        return True

    if "qr" in t.split():
        if hud is None:
            respond(_sir("No puedo mostrar el código QR en este modo"))
        else:
            try:
                from jarvis import server as jarvis_server
                from jarvis.qr_share import mobile_qr_data_uri

                hud.show_qr(mobile_qr_data_uri(), jarvis_server.TOKEN)
                pin_hablado = " ".join(jarvis_server.TOKEN)
                respond(_sir(f"Ahí tiene el código QR. Si prefiere tipearlo, el PIN es {pin_hablado}"))
            except Exception:
                respond(_sir("No pude generar el código QR"))
        return True

    contacto = _extract_message_contact(t)
    if contacto:
        if escuchar is None:
            respond(_sir("No puedo tomar dictado en este modo"))
            return True
        respond(f"¿Qué le digo a {contacto}, señor?")
        mensaje = escuchar()
        if not mensaje.strip():
            respond(_sir("No escuché ningún mensaje, cancelo el envío"))
            return True
        respond(_sir(f"Enviando a {contacto} por WhatsApp"))
        if whatsapp.send_message(contacto, mensaje):
            respond(_sir("Mensaje enviado"))
        else:
            respond(_sir(f"No pude encontrar a {contacto} o enviar el mensaje en WhatsApp Web"))
        return True

    if t.startswith("pon ") or t.startswith("reproduce ") or "video de" in t or "cancion de" in t:
        query = t.split(" ", 1)[1] if " " in t else t
        respond(_sir(f"Buscando en YouTube: {query}"))
        actions.open_youtube_search(query)

        if escuchar is not None:
            resultado = actions.search_youtube_first_result(query)
            if resultado is None:
                respond(_sir("No encontré resultados para reproducir"))
                return True
            _video_id, titulo = resultado
            respond(f"Encontré: {titulo}. ¿Lo reproduzco, señor?")
            respuesta = escuchar()
            if _confirmed(respuesta):
                actions.play_youtube_video(_video_id)
                respond(_sir("Reproduciendo"))
            else:
                respond(_sir("Entendido, no lo reproduzco"))
        return True

    if t.startswith("busca "):
        query = t[len("busca "):].replace("en google", "").strip()
        respond(_sir(f"Buscando en Google: {query}"))
        actions.search_web(query)
        return True

    if t.startswith("abre "):
        app = _clean(t[len("abre "):])
        if actions.open_app(app):
            respond(_sir(f"Abriendo {app}"))
        else:
            respond(_sir(f"No conozco la app '{app}', buscando en internet"))
            actions.search_web(app)
        return True

    if t.startswith("cierra "):
        app = _clean(t[len("cierra "):])
        if actions.close_app(app):
            respond(_sir(f"Cerrando {app}"))
        else:
            respond(_sir(f"No conozco la app '{app}'"))
        return True

    if "sube" in t and "volumen" in t:
        actions.volume_up()
        respond(_sir("Subiendo volumen"))
        return True

    if "baja" in t and "volumen" in t:
        actions.volume_down()
        respond(_sir("Bajando volumen"))
        return True

    if "silencia" in t or "mutea" in t:
        actions.volume_mute()
        respond(_sir("Silenciando"))
        return True

    if "que hora es" in t or "dime la hora" in t or "que horas son" in t:
        now = datetime.datetime.now().strftime("%H:%M")
        respond(_sir(f"Son las {now}"))
        return True

    if "que dia es" in t or "que fecha es" in t or "en que fecha estamos" in t or "dime la fecha" in t:
        now = datetime.datetime.now()
        respond(_sir(f"Hoy es {_DIAS[now.weekday()]} {now.day} de {_MESES[now.month - 1]}"))
        return True

    if "clima" in t or "temperatura" in t or "va a llover" in t or "esta lloviendo" in t:
        city_match = _WEATHER_CITY_RE.search(t)
        city = _clean(city_match.group(1)) if city_match else None
        respond(_sir("Consultando el clima"))
        respond(_sir(weather.get_weather(city)))
        return True

    name_match = _NAME_RE.search(t)
    if name_match:
        original_stripped = text.strip().strip(".,!?¿¡")
        nombre = original_stripped[name_match.start(1):name_match.end(1)].strip().title()
        if nombre:
            memory.set_name(nombre)
            respond(_sir(f"Mucho gusto, {nombre}. Lo voy a recordar"))
            return True

    save_match = _MEMORY_SAVE_RE.match(t)
    if save_match:
        # se corta el texto ORIGINAL en la misma posicion (no `t`), para no
        # perder tildes/mayusculas del dato guardado (mismo truco que se
        # usaba para los recordatorios de calendario)
        dato = text.strip().strip(".,!?¿¡")[save_match.start(1):].strip()
        if not dato:
            respond(_sir("¿Qué quiere que recuerde?"))
            return True
        memory.add_archival(dato)
        memory.append_usuario_fact(dato)
        respond(_sir("Anotado"))
        return True

    if _MEMORY_RECALL_RE.search(t):
        resultados = memory.search_archival(text)
        respond(agent.answer_from_memory(text, resultados))
        return True

    # ningun comando fijo coincidio: se lo pasamos al agente (Qwen2.5:7b
    # con tool-calling, ver agent.py), que decide solo si hace falta
    # ejecutar una accion real o si alcanza con responder charlando
    agent.ask(text, respond=respond, escuchar=escuchar, hud=hud)
    return True
