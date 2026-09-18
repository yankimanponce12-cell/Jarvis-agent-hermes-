import json
import urllib.request

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.2:3b"

SYSTEM_PROMPT = (
    # "Tu nombre es X" en vez de arrancar con "Eres Jarvis": con el modelo
    # chico que se usa aca (llama3.2:3b) esa segunda forma se presta a que
    # el modelo entienda "Eres" como si fuera el nombre y "Jarvis" un
    # apellido/descripcion aparte (se repitio consistente en pruebas:
    # "Me llamo Eres, señor..."). La aclaracion explicita entre parentesis
    # es lo que termino de asegurar que no vuelva a pasar.
    "Tu nombre es Jarvis (nunca respondas otro nombre distinto a este, ni "
    "'Eres', ni ningun otro). Sos un asistente de voz en español inspirado "
    "en el JARVIS de Tony Stark (Iron Man): formal, ingenioso y siempre te "
    "diriges al usuario como 'señor'. Respondes de forma breve y natural, "
    "en una o dos oraciones como maximo porque tu respuesta se lee en voz "
    "alta. No uses markdown. Recuerdas lo que se hablo antes en la "
    "conversacion y puedes hacer referencia a ello de forma natural."
)

MAX_TURNS = 12  # pares usuario/asistente que se guardan en la memoria


class Brain:
    """Mantiene el historial de la conversacion para que Jarvis responda con
    contexto (recuerde nombres, temas previos, etc.), no cada pregunta de
    forma aislada."""

    def __init__(self):
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def ask(self, question: str) -> str:
        self.messages.append({"role": "user", "content": question})
        payload = json.dumps(
            {
                "model": MODEL,
                "messages": self.messages,
                "stream": False,
                "keep_alive": "30m",
                # el prompt ya le pide 1-2 oraciones porque la respuesta se
                # lee en voz alta; se limita el generado a un puñado de
                # tokens para que no tarde de mas generando de mas y luego
                # se descarte al leerla.
                "options": {"num_predict": 120},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_CHAT_URL, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
            respuesta = data.get("message", {}).get("content", "").strip()
        except Exception:
            self.messages.pop()  # no se guarda el turno que fallo
            return "No pude conectarme con mi modelo de IA local. Revisa que Ollama este corriendo."

        self.messages.append({"role": "assistant", "content": respuesta})
        self._trim_history()
        return respuesta

    def _trim_history(self):
        # se conserva el system prompt + los ultimos MAX_TURNS intercambios,
        # para que el prompt no crezca sin limite en sesiones largas
        max_messages = 1 + MAX_TURNS * 2
        if len(self.messages) > max_messages:
            self.messages = [self.messages[0]] + self.messages[-(MAX_TURNS * 2):]

    def reset(self):
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]


_brain = Brain()


def ask(question: str) -> str:
    return _brain.ask(question)


def classify_yes_no(text: str):
    """Le pregunta al modelo si una respuesta corta del usuario significa
    'si' o 'no', para confirmaciones (p.ej. '¿lo reproduzco?') cuando la
    respuesta no usa ninguna de las palabras clave conocidas. No toca el
    historial de la conversacion principal. Devuelve True/False, o None si
    no se pudo determinar (IA caida o respuesta ambigua)."""
    prompt = (
        "El usuario respondio esto a una pregunta de confirmacion "
        f'(si quiere que se haga algo o no): "{text}"\n'
        "¿Su respuesta significa que si quiere, o que no quiere? "
        "Respondé con una sola palabra, sin explicacion: si o no."
    )
    payload = json.dumps(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "keep_alive": "30m",
            # se espera una sola palabra ("si"/"no"); capar el generado
            # evita que el modelo se ponga a explicar antes de contestar.
            "options": {"num_predict": 8},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_CHAT_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        respuesta = data.get("message", {}).get("content", "").strip().lower()
    except Exception:
        return None

    respuesta = respuesta.translate(str.maketrans("áéíóúñ", "aeioun"))
    if respuesta.startswith("si"):
        return True
    if respuesta.startswith("no"):
        return False
    return None


def reset():
    _brain.reset()
