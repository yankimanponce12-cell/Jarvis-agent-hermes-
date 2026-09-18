import ctypes
import re
import subprocess
import urllib.parse
import urllib.request
import webbrowser

# nombre hablado -> (target para abrir, nombre de proceso para cerrar)
APPS = {
    "bloc de notas": ("notepad.exe", "notepad.exe"),
    "blog de notas": ("notepad.exe", "notepad.exe"),  # Whisper a veces confunde "bloc" con "blog"
    "notas": ("notepad.exe", "notepad.exe"),
    # calc.exe redirige mal en algunas instalaciones de Windows 11 (abre
    # Configuracion en vez de la Calculadora); se lanza por su AppsFolder path.
    "calculadora": ("shell:AppsFolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", "CalculatorApp.exe"),
    "explorador": ("explorer.exe", "explorer.exe"),
    "paint": ("mspaint.exe", "mspaint.exe"),
    "cmd": ("cmd.exe", "cmd.exe"),
    "consola": ("cmd.exe", "cmd.exe"),
    "terminal": ("wt.exe", "WindowsTerminal.exe"),
}

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF


def search_web(query: str):
    url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
    webbrowser.open(url)


_VIDEO_RESULT_RE = re.compile(
    r'"videoRenderer":\{"videoId":"([^"]+)".*?"title":\{"runs":\[\{"text":"([^"]+)"'
)


def open_youtube_search(query: str):
    """Abre en el navegador la pagina de resultados de busqueda de YouTube."""
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    webbrowser.open(url)


def search_youtube_first_result(query: str):
    """Busca en YouTube y devuelve (video_id, titulo) del primer resultado
    de video, o None si no se encontro nada o fallo la busqueda."""
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    match = _VIDEO_RESULT_RE.search(html)
    if not match:
        return None
    return match.group(1), match.group(2)


def play_youtube_video(video_id: str):
    webbrowser.open(f"https://www.youtube.com/watch?v={video_id}")


def open_app(app_name: str) -> bool:
    entry = APPS.get(app_name.strip().lower())
    if not entry:
        return False
    target, _ = entry
    if target.startswith("shell:"):
        subprocess.Popen(["explorer.exe", target])
    else:
        subprocess.Popen(target, shell=True)
    return True


def close_app(app_name: str) -> bool:
    entry = APPS.get(app_name.strip().lower())
    if not entry:
        return False
    _, process_name = entry
    subprocess.run(["taskkill", "/IM", process_name, "/F"], capture_output=True)
    return True


def _press_key(vk_code: int):
    ctypes.windll.user32.keybd_event(vk_code, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk_code, 0, 2, 0)  # KEYEVENTF_KEYUP


def volume_up(steps: int = 2):
    for _ in range(steps):
        _press_key(VK_VOLUME_UP)


def volume_down(steps: int = 2):
    for _ in range(steps):
        _press_key(VK_VOLUME_DOWN)


def volume_mute():
    _press_key(VK_VOLUME_MUTE)
