import datetime
import json
import urllib.parse
import urllib.request
from pathlib import Path

# Ubicacion de respaldo: se usa si no se pudo ubicar al usuario por IP (sin
# internet, servicio caido, etc.) o si Jarvis corre desde otro lugar sin wifi
# real. Se lee de local_config.json en la raiz del proyecto (esta en
# .gitignore para no publicar la ubicacion de nadie; ver
# local_config.example.json). Sin ese archivo cae a Ciudad de Mexico.
_LOCAL_CONFIG_FILE = Path(__file__).resolve().parent.parent / "local_config.json"


def _load_default_location():
    try:
        data = json.loads(_LOCAL_CONFIG_FILE.read_text(encoding="utf-8"))
        return str(data["default_city"]), float(data["default_lat"]), float(data["default_lon"])
    except Exception:
        return "Ciudad de Mexico", 19.4326, -99.1332


DEFAULT_CITY, DEFAULT_LAT, DEFAULT_LON = _load_default_location()

# Codigos de clima de Open-Meteo (estandar WMO) traducidos a una
# descripcion corta en español.
_WEATHER_CODES = {
    0: "cielo despejado",
    1: "mayormente despejado",
    2: "parcialmente nublado",
    3: "nublado",
    45: "neblina",
    48: "neblina con escarcha",
    51: "llovizna ligera",
    53: "llovizna moderada",
    55: "llovizna intensa",
    56: "llovizna helada ligera",
    57: "llovizna helada intensa",
    61: "lluvia ligera",
    63: "lluvia moderada",
    65: "lluvia fuerte",
    66: "lluvia helada ligera",
    67: "lluvia helada fuerte",
    71: "nevada ligera",
    73: "nevada moderada",
    75: "nevada fuerte",
    77: "granizo pequeño",
    80: "chubascos ligeros",
    81: "chubascos moderados",
    82: "chubascos fuertes",
    85: "chubascos de nieve ligeros",
    86: "chubascos de nieve fuertes",
    95: "tormenta electrica",
    96: "tormenta con granizo ligero",
    99: "tormenta con granizo fuerte",
}


def _get_json(url: str, timeout: int = 6) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-voice-assistant"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _detect_location_by_ip():
    """Ubica al usuario por su IP publica (a traves de su conexion a
    internet/wifi actual), para no tener que preguntarle la ciudad cada
    vez. Devuelve None si no hay internet o el servicio no respondio, y
    quien llama cae a la ubicacion por defecto (DEFAULT_CITY)."""
    try:
        data = _get_json("https://ipapi.co/json/")
        lat, lon = data.get("latitude"), data.get("longitude")
        city = data.get("city")
        if lat is None or lon is None:
            return None
        return {"city": city or DEFAULT_CITY, "lat": lat, "lon": lon}
    except Exception:
        return None


def _geocode_city(city: str):
    """Convierte un nombre de ciudad dicho por voz a coordenadas, para
    cuando se pregunta el clima de un lugar especifico distinto al
    detectado por IP (p.ej. 'clima en Guadalajara')."""
    url = (
        "https://geocoding-api.open-meteo.com/v1/search?"
        f"name={urllib.parse.quote(city)}&count=1&language=es"
    )
    try:
        data = _get_json(url)
        results = data.get("results") or []
        if not results:
            return None
        r = results[0]
        return {"city": r["name"], "lat": r["latitude"], "lon": r["longitude"]}
    except Exception:
        return None


# subconjunto de _WEATHER_CODES que efectivamente moja (para no avisar de
# "lluvia" por una simple neblina o cielo nublado que tambien traen su
# propio codigo de clima pero no ameritan aviso proactivo)
_RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}


def _resolve_location(city: str | None):
    if city:
        return _geocode_city(city)
    return _detect_location_by_ip() or {"city": DEFAULT_CITY, "lat": DEFAULT_LAT, "lon": DEFAULT_LON}


def check_rain_soon(city: str | None = None, hours_ahead: int = 1, threshold: int = 55) -> str | None:
    """Para avisos proactivos (ver proactive.py): si el pronostico por hora
    marca alta probabilidad de lluvia dentro de `hours_ahead` horas,
    devuelve una frase lista para hablar. Si no hay riesgo o algo fallo
    (sin internet, etc.), devuelve None en vez de lanzar excepcion, para
    que el llamador simplemente no diga nada esa vez."""
    location = _resolve_location(city)
    if location is None:
        return None

    url = (
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={location['lat']}&longitude={location['lon']}"
        "&hourly=precipitation_probability,weather_code&forecast_days=1&timezone=auto"
    )
    try:
        data = _get_json(url)
        hourly = data["hourly"]
        tiempos = hourly["time"]
        probabilidades = hourly["precipitation_probability"]
        codigos = hourly["weather_code"]
    except Exception:
        return None

    ahora = datetime.datetime.now()
    for tiempo_str, prob, codigo in zip(tiempos, probabilidades, codigos):
        if prob is None or codigo is None:
            continue  # Open-Meteo a veces deja huecos puntuales sin dato
        try:
            hora = datetime.datetime.fromisoformat(tiempo_str)
        except ValueError:
            continue
        horas_para_llegar = (hora - ahora).total_seconds() / 3600
        if 0 <= horas_para_llegar <= hours_ahead and prob >= threshold and codigo in _RAIN_CODES:
            descripcion = _WEATHER_CODES.get(codigo, "lluvia")
            return (
                f"Señor, hay {round(prob)} por ciento de probabilidad de {descripcion} "
                f"en {location['city']} dentro de la próxima hora, quizás quiera llevar paraguas"
            )
    return None


def get_weather(city: str | None = None) -> str:
    """Devuelve una descripcion en español, lista para leerse en voz alta,
    del clima actual. Si no se pide una ciudad especifica, se usa la
    ubicacion detectada por la IP de la conexion actual (o DEFAULT_CITY si la
    deteccion falla)."""
    if city:
        location = _geocode_city(city)
        if location is None:
            return f"No pude encontrar el clima de {city}"
    else:
        location = _detect_location_by_ip() or {
            "city": DEFAULT_CITY,
            "lat": DEFAULT_LAT,
            "lon": DEFAULT_LON,
        }

    url = (
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={location['lat']}&longitude={location['lon']}"
        "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
        "&timezone=auto"
    )
    try:
        data = _get_json(url)
        current = data["current"]
        temp = current["temperature_2m"]
        humedad = current["relative_humidity_2m"]
        viento = current["wind_speed_10m"]
        descripcion = _WEATHER_CODES.get(current["weather_code"], "condiciones variables")
    except Exception:
        return "No pude consultar el clima ahorita. Revise su conexion a internet"

    return (
        f"En {location['city']} hay {round(temp)} grados y {descripcion}, "
        f"con {round(humedad)} por ciento de humedad y viento de "
        f"{round(viento)} kilometros por hora"
    )


def get_current_compact(city: str | None = None) -> dict | None:
    """Condiciones actuales compactas (temperatura, precipitacion, humedad,
    presion, viento), pensadas para mostrarse en poco espacio (la pantalla
    del reloj) en vez de leerse en voz alta. Nunca lanza excepcion: si algo
    fallo (sin internet, etc.), devuelve None."""
    location = _resolve_location(city)
    if location is None:
        return None

    url = (
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={location['lat']}&longitude={location['lon']}"
        "&current=temperature_2m,precipitation,relative_humidity_2m,surface_pressure,"
        "wind_speed_10m,weather_code&timezone=auto"
    )
    try:
        data = _get_json(url)
        current = data["current"]
        return {
            "temp": round(current["temperature_2m"]),
            "precip": round(current["precipitation"], 1),
            "humidity": round(current["relative_humidity_2m"]),
            "pressure": round(current["surface_pressure"]),
            "wind": round(current["wind_speed_10m"]),
            "desc": _WEATHER_CODES.get(current["weather_code"], "variable"),
        }
    except Exception:
        return None
