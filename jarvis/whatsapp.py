import os
import subprocess
import threading
import time
import urllib.request

from selenium import webdriver
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType

# perfil de Brave dedicado y persistente: la sesion de WhatsApp Web (QR
# escaneado) queda guardada aca entre ejecuciones, como al usar WhatsApp Web
# normal en un navegador.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = os.path.join(PROJECT_ROOT, "whatsapp_profile")

_BRAVE_PATHS = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
]


def _find_brave_binary() -> str:
    for path in _BRAVE_PATHS:
        if os.path.isfile(path):
            return path
    raise RuntimeError(
        "No encontre el ejecutable de Brave. Revisa que este instalado en una "
        "de estas rutas: " + ", ".join(_BRAVE_PATHS)
    )


_SEARCH_BOX = (By.CSS_SELECTOR, 'input[aria-label="Buscar un chat o iniciar uno nuevo"]')
_MESSAGE_BOX = (By.XPATH, '//div[starts-with(@aria-label, "Escribir un mensaje para")]')

_DEBUG_PORT = 9223

_driver = None
_brave_process = None
_service = None
_lock = threading.Lock()


def _debugger_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1):
            return True
    except Exception:
        return False


def _launch_brave(port: int):
    """Lanza Brave nosotros mismos (en vez de dejar que lo haga chromedriver):
    dejar que chromedriver spawnee Brave directamente resulto no ser fiable en
    esta maquina (fallaba de forma intermitente con 'DevToolsActivePort file
    doesn't exist' / 'chrome not reachable'). Lanzandolo a mano y luego
    conectando Selenium por el puerto de depuracion evita ese problema."""
    os.makedirs(PROFILE_DIR, exist_ok=True)
    args = [
        _find_brave_binary(),
        f"--user-data-dir={PROFILE_DIR}",
        "--profile-directory=Default",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    end = time.time() + 30
    while time.time() < end:
        if _debugger_ready(port):
            return process
        time.sleep(0.5)
    raise RuntimeError("Brave no abrio el puerto de depuracion a tiempo")


def _get_driver():
    global _driver, _brave_process, _service
    if _driver is not None:
        try:
            _ = _driver.current_url  # sigue vivo?
            return _driver
        except WebDriverException:
            _driver = None

    if not _debugger_ready(_DEBUG_PORT):
        _brave_process = _launch_brave(_DEBUG_PORT)

    options = Options()
    options.debugger_address = f"127.0.0.1:{_DEBUG_PORT}"
    _service = Service(ChromeDriverManager(chrome_type=ChromeType.BRAVE).install())
    _driver = webdriver.Chrome(service=_service, options=options)
    if "web.whatsapp.com" not in _driver.current_url:
        _driver.get("https://web.whatsapp.com")
    return _driver


def _wait_login(driver, timeout=90):
    """Espera a que aparezca el buscador de chats: senal de que la sesion ya
    esta iniciada (o de que se acaba de escanear el QR)."""
    WebDriverWait(driver, timeout).until(EC.presence_of_element_located(_SEARCH_BOX))


def _find_chat(driver, contact: str, timeout=10):
    """Busca en la lista de chats filtrada uno cuyo titulo contenga el
    nombre del contacto (case-insensitive). Se compara en Python en vez de
    meter el texto del usuario en el XPath, para no depender de escapar
    comillas u otros caracteres raros dichos por voz."""
    contacto_l = contact.strip().lower()
    end = time.time() + timeout
    while time.time() < end:
        try:
            for el in driver.find_elements(By.XPATH, '//span[@title]'):
                title = (el.get_attribute("title") or "").strip().lower()
                if contacto_l in title:
                    return el
        except StaleElementReferenceException:
            pass  # la lista se re-renderizo mientras la recorriamos, se reintenta
        time.sleep(0.4)
    return None


def _strip_unsupported_chars(text: str) -> str:
    """ChromeDriver no puede escribir caracteres fuera del BMP (la mayoria de
    los emojis modernos -robot, aplausos, etc.- viven en U+1F300 en
    adelante). Si se intentan mandar, send_keys revienta con
    WebDriverException ('only supports characters in the BMP') y se pierde
    el mensaje completo. Se filtran esos caracteres puntuales en vez de
    fallar; tildes, ñ y emojis clasicos como ❤ o ☺ (que si estan en el BMP)
    se mandan igual."""
    return "".join(ch for ch in text if ord(ch) <= 0xFFFF)


def _click_when_ready(driver, locator, timeout=10):
    """find_element().click() a veces revienta con
    ElementNotInteractableException justo despues de que WhatsApp Web
    termina de renderizar una vista nueva (el elemento ya esta en el DOM
    pero todavia no es clickeable). Se reintenta un rato en vez de fallar
    a la primera."""
    end = time.time() + timeout
    last_error = None
    while time.time() < end:
        try:
            el = driver.find_element(*locator)
            el.click()
            return el
        except (ElementNotInteractableException, StaleElementReferenceException, NoSuchElementException) as error:
            last_error = error
            time.sleep(0.3)
    raise last_error or TimeoutException(f"No se pudo hacer click en {locator}")


def send_message(contact: str, message: str) -> bool:
    """Abre (o reutiliza) WhatsApp Web, busca el contacto y le manda el
    mensaje. Devuelve True si se pudo enviar. El navegador se deja abierto
    entre llamadas para no tener que volver a cargar/loguear cada vez."""
    with _lock:
        try:
            driver = _get_driver()
            _wait_login(driver)

            search_box = _click_when_ready(driver, _SEARCH_BOX)
            search_box.send_keys(Keys.CONTROL, "a")
            search_box.send_keys(Keys.DELETE)
            search_box.send_keys(contact)

            chat = _find_chat(driver, contact)
            if chat is None:
                print(f"[whatsapp] No encontre un chat que contenga '{contact}'")
                return False
            chat.click()

            message_box = WebDriverWait(driver, 10).until(EC.presence_of_element_located(_MESSAGE_BOX))
            message_box.click()
            clean_message = _strip_unsupported_chars(message)
            for i, line in enumerate(clean_message.split("\n")):
                if i > 0:
                    message_box.send_keys(Keys.SHIFT, Keys.ENTER)
                message_box.send_keys(line)
            message_box.send_keys(Keys.ENTER)
            return True
        except (TimeoutException, NoSuchElementException, StaleElementReferenceException, WebDriverException) as error:
            print(f"[whatsapp] Fallo el envio a '{contact}': {error.__class__.__name__}: {error}")
            return False


def close():
    """Cierra el Brave de WhatsApp Web (si se llego a abrir) y limpia el
    estado interno. Se llama al despedirse de Jarvis, para no dejar el
    navegador huerfano corriendo en segundo plano.

    driver.quit() no basta: el driver se conecto a un Brave que nosotros
    mismos lanzamos por fuera (modo 'attach' via debugger_address, ver
    _launch_brave), y en ese modo Selenium no siempre cierra el proceso
    real del navegador, solo la sesion de depuracion. Se mata el proceso
    (y todo su arbol, por los procesos hijos de Chromium) para asegurar
    que de verdad se cierre.

    Lo mismo aplica al proceso de chromedriver.exe (service.stop() puede
    fallar en cerrarlo del todo): un chromedriver huerfano no se ve, pero
    hereda handles del proceso que lo lanzo (stdout/stderr redirigidos,
    p.ej. logs/launcher.log) y los deja bloqueados indefinidamente aunque
    Jarvis ya se haya cerrado, rompiendo el proximo arranque en silencio.
    Se mata por PID igual que Brave, no solo con service.stop()."""
    global _driver, _brave_process, _service
    with _lock:
        if _driver is not None:
            try:
                _driver.quit()
            except Exception:
                pass
            _driver = None

        if _brave_process is not None:
            _kill_process_tree(_brave_process.pid)
            _brave_process = None

        if _service is not None:
            try:
                _service.stop()
            except Exception:
                pass
            service_process = getattr(_service, "process", None)
            if service_process is not None:
                _kill_process_tree(service_process.pid)
            _service = None


def _kill_process_tree(pid: int):
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, 9)
    except Exception:
        pass
