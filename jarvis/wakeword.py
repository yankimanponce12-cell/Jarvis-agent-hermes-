import difflib

WAKE_VARIANTS = ["jarvis", "yarvis", "harvis", "yarves", "jarves"]
GREETING_WORDS = ["oye", "hey", "ey", "hola"]
SIMILARITY_THRESHOLD = 0.65

_ACCENTS = str.maketrans("áéíóúñ", "aeioun")


def _normalize(word: str) -> str:
    return word.lower().strip(".,!?¿¡").translate(_ACCENTS)


def _matches_wake_word(word: str) -> bool:
    norm = _normalize(word)
    best = max(
        (difflib.SequenceMatcher(None, norm, variant).ratio() for variant in WAKE_VARIANTS),
        default=0.0,
    )
    return best >= SIMILARITY_THRESHOLD


def extract_command(text: str):
    """Si el texto empieza con 'Jarvis' (o una variante mal transcrita, o
    'oye/hey/hola Jarvis'), devuelve el resto como comando. Si no hay wake
    word al inicio, devuelve None."""
    words = text.strip().split()
    if not words:
        return None

    if _matches_wake_word(words[0]):
        return " ".join(words[1:]).strip()

    if len(words) >= 2 and _normalize(words[0]) in GREETING_WORDS and _matches_wake_word(words[1]):
        return " ".join(words[2:]).strip()

    return None
