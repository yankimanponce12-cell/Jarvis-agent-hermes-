"""El "cerebro" agente de Jarvis: en vez del brain.ask() plano de antes
(solo charla, sin poder hacer nada), este usa un modelo con tool-calling
(Qwen2.5:7b via Ollama) que decide por si solo si hace falta ejecutar una
accion real (WhatsApp, YouTube, clima, apps, volumen) o si alcanza con
responder charlando.

Se prueban Hermes3 (3b y 8b) y Phi-4-mini antes de esto: ninguno devolvia
tool_calls confiables via la API de Ollama (JSON roto o de plano ignoraban
el pedido). Qwen2.5:7b fue el unico que acerto 8/8 en la prueba empirica
(ver carpeta "prueba hermes agent")... con ESTA lista de 7 herramientas y
ESTE prompt corto. Se probo agregar 3 herramientas mas de memoria y un
prompt mas largo, y la confiabilidad se desplomo (empezo a "alucinar"
acciones sin ejecutarlas, o a devolver JSON roto) incluso repitiendo los
mismos pedidos que antes acertaba siempre. Un modelo local de 7B tolera
bien un puñado chico de herramientas, no un tool-set grande. Por eso la
memoria (jarvis/memory.py) NO se expone como herramienta aca: se maneja
aparte, por deteccion de frases en commands.py (ver _NAME_RE,
_MEMORY_SAVE_RE y _MEMORY_RECALL_RE; la respuesta a "que recordas" se
redacta con answer_from_memory() de este modulo).

Este modulo es el fallback de commands.py: las reglas fijas (regex) siguen
resolviendo los casos exactos de siempre, instantaneo, sin pasar por aca.
Solo lo que no matchea ninguna regla llega hasta el agente."""

import datetime
import json
import urllib.request

from jarvis import actions, memory, weather, whatsapp

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:7b"

MAX_TURNS = 8  # conversacion de corto plazo (se resetea con lobotomizar); lo de largo plazo vive en memory.py

# a proposito la misma redaccion corta que se valido en el benchmark: se
# probo una version mas larga/explicativa y por si sola (sin tocar las
# herramientas) ya bajaba la confiabilidad del tool-calling
BASE_SYSTEM_PROMPT = (
    "Tu nombre es Jarvis, un asistente de voz en español inspirado en el "
    "JARVIS de Iron Man: formal, te diriges al usuario como 'señor'. "
    "Cuando el usuario pide una accion concreta usa la herramienta "
    "correspondiente. Si es charla general, respondes vos mismo en 1-2 "
    "oraciones, sin markdown."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "enviar_whatsapp",
        "description": "Envia un mensaje de WhatsApp a un contacto",
        "parameters": {"type": "object", "properties": {
            "contacto": {"type": "string", "description": "Nombre del contacto"},
            "mensaje": {"type": "string", "description": "Texto del mensaje a enviar"},
        }, "required": ["contacto", "mensaje"]},
    }},
    {"type": "function", "function": {
        "name": "reproducir_youtube",
        "description": "Busca y reproduce un video o cancion en YouTube",
        "parameters": {"type": "object", "properties": {
            "busqueda": {"type": "string"},
        }, "required": ["busqueda"]},
    }},
    {"type": "function", "function": {
        "name": "consultar_clima",
        "description": "Da el clima actual de una ciudad (o la del usuario si no especifica)",
        "parameters": {"type": "object", "properties": {
            "ciudad": {"type": "string"},
        }, "required": []},
    }},
    {"type": "function", "function": {
        "name": "abrir_aplicacion",
        "description": "Abre una aplicacion en la PC",
        "parameters": {"type": "object", "properties": {
            "nombre": {"type": "string"},
        }, "required": ["nombre"]},
    }},
    {"type": "function", "function": {
        "name": "cerrar_aplicacion",
        "description": "Cierra una aplicacion en la PC",
        "parameters": {"type": "object", "properties": {
            "nombre": {"type": "string"},
        }, "required": ["nombre"]},
    }},
    {"type": "function", "function": {
        "name": "controlar_volumen",
        "description": "Sube, baja o silencia el volumen del sistema",
        "parameters": {"type": "object", "properties": {
            "accion": {"type": "string", "enum": ["subir", "bajar", "silenciar"]},
        }, "required": ["accion"]},
    }},
    {"type": "function", "function": {
        "name": "decir_hora",
        "description": "Dice la hora actual",
        "parameters": {"type": "object", "properties": {}},
    }},
]


def _sir(msg: str) -> str:
    return f"{msg.rstrip('.')}, señor."


def _chat(messages, tools=None):
    system = BASE_SYSTEM_PROMPT
    memoria = memory.get_core_memory_text()
    if memoria:
        system = f"{system}\n\n{memoria}"
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": False,
        "keep_alive": "30m",
        "options": {"num_predict": 150},
    }
    if tools:
        payload["tools"] = tools
    req = urllib.request.Request(
        OLLAMA_CHAT_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return data["message"]


class Agent:
    def __init__(self):
        self.messages = []

    def reset(self):
        self.messages = []

    def _trim_history(self):
        max_messages = MAX_TURNS * 2
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]

    def _execute_tool(self, name: str, args: dict, respond, escuchar, hud):
        """Ejecuta la herramienta de verdad y confirma con respond(). Todas
        las herramientas de este agente son autocontenidas (no hace falta
        volver a llamar al modelo para redactar la respuesta final)."""
        if name == "enviar_whatsapp":
            contacto = str(args.get("contacto", "")).strip()
            mensaje = str(args.get("mensaje", "")).strip()
            if not contacto or not mensaje:
                respond(_sir("No entendí bien a quién o qué mandar"))
                return ""
            respond(_sir(f"Enviando a {contacto} por WhatsApp"))
            if whatsapp.send_message(contacto, mensaje):
                respond(_sir("Mensaje enviado"))
            else:
                respond(_sir(f"No pude encontrar a {contacto} o enviar el mensaje en WhatsApp Web"))
            return ""

        if name == "reproducir_youtube":
            busqueda = str(args.get("busqueda", "")).strip()
            if not busqueda:
                respond(_sir("No entendí qué quiere que reproduzca"))
                return ""
            respond(_sir(f"Buscando en YouTube: {busqueda}"))
            actions.open_youtube_search(busqueda)
            if escuchar is not None:
                resultado = actions.search_youtube_first_result(busqueda)
                if resultado is None:
                    respond(_sir("No encontré resultados para reproducir"))
                    return ""
                video_id, titulo = resultado
                respond(f"Encontré: {titulo}. ¿Lo reproduzco, señor?")
                from jarvis.commands import _confirmed  # evita import circular al nivel de modulo

                if _confirmed(escuchar()):
                    actions.play_youtube_video(video_id)
                    respond(_sir("Reproduciendo"))
                else:
                    respond(_sir("Entendido, no lo reproduzco"))
            return ""

        if name == "consultar_clima":
            ciudad = str(args.get("ciudad") or "").strip() or None
            respond(_sir("Consultando el clima"))
            respond(_sir(weather.get_weather(ciudad)))
            return ""

        if name == "abrir_aplicacion":
            app = str(args.get("nombre", "")).strip()
            if actions.open_app(app):
                respond(_sir(f"Abriendo {app}"))
            else:
                respond(_sir(f"No conozco la app '{app}', buscando en internet"))
                actions.search_web(app)
            return ""

        if name == "cerrar_aplicacion":
            app = str(args.get("nombre", "")).strip()
            if actions.close_app(app):
                respond(_sir(f"Cerrando {app}"))
            else:
                respond(_sir(f"No conozco la app '{app}'"))
            return ""

        if name == "controlar_volumen":
            accion = args.get("accion")
            if accion == "subir":
                actions.volume_up()
                respond(_sir("Subiendo volumen"))
            elif accion == "bajar":
                actions.volume_down()
                respond(_sir("Bajando volumen"))
            else:
                actions.volume_mute()
                respond(_sir("Silenciando"))
            return ""

        if name == "decir_hora":
            ahora = datetime.datetime.now().strftime("%H:%M")
            respond(_sir(f"Son las {ahora}"))
            return ""

        return ""  # herramienta desconocida (no deberia pasar)

    def ask(self, text: str, respond=print, escuchar=None, hud=None):
        """Punto de entrada: le pasa el texto al agente y deja que decida.
        Si algo falla (Ollama caido, respuesta rara), no lanza excepcion:
        cae a una respuesta generica, igual que hacia brain.ask()."""
        self.messages.append({"role": "user", "content": text})
        try:
            msg = _chat(self.messages, tools=TOOLS)
        except Exception:
            self.messages.pop()
            respond(_sir("No pude conectarme con mi modelo de IA local. Revise que Ollama esté corriendo"))
            return

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            texto = (msg.get("content") or "").strip()
            respond(texto or _sir("No entendí bien, intente de nuevo"))
            self.messages.append({"role": "assistant", "content": texto})
            self._trim_history()
            return

        # se ejecuta como mucho la primera herramienta que pidio: en la
        # practica el modelo casi siempre pide una sola, y encadenar varias
        # sin supervision es mas riesgo (acciones reales tipo WhatsApp) que
        # beneficio
        call = tool_calls[0]
        name = call["function"]["name"]
        args = call["function"].get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                args = {}

        self.messages.append({"role": "assistant", "content": "", "tool_calls": [call]})

        try:
            resultado = self._execute_tool(name, args, respond, escuchar, hud)
        except Exception:
            respond(_sir("Algo falló al ejecutar esa acción"))
            resultado = ""

        self.messages.append({"role": "tool", "content": resultado or ""})
        self._trim_history()


_agent = Agent()


def ask(text: str, respond=print, escuchar=None, hud=None):
    _agent.ask(text, respond=respond, escuchar=escuchar, hud=hud)


def reset():
    _agent.reset()


def answer_from_memory(pregunta: str, resultados: list[str]) -> str:
    """Redacta una respuesta hablada a partir de recuerdos ya encontrados
    (ver memory.search_archival). Es una llamada SIN herramientas (solo
    charla, como brain.ask()): no hace falta que el modelo decida nada,
    solo que lea los datos y conteste en un par de oraciones - por eso no
    sufre el mismo problema de confiabilidad que el tool-calling con
    muchas herramientas."""
    contexto = "\n".join(resultados) if resultados else "(no se encontro nada relacionado)"
    prompt = (
        f"El usuario pregunto: \"{pregunta}\"\n\n"
        f"Esto es lo que tenés guardado de conversaciones anteriores:\n{contexto}\n\n"
        "Respondé en 1-2 oraciones, tono Jarvis, usando esa informacion si es "
        "relevante. Si no hay nada relacionado, decilo con naturalidad."
    )
    try:
        msg = _chat([{"role": "user", "content": prompt}])
        return (msg.get("content") or "").strip() or "No encontré nada relacionado, señor."
    except Exception:
        return "No pude buscar en mi memoria ahorita, señor."
