import io
import logging
import subprocess
import time
from pathlib import Path

import requests
import sounddevice as sd
import soundfile as sf

logger = logging.getLogger("jarvis.voice")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OPENVOICE_DIR = PROJECT_ROOT / "openvoice_server"
OPENVOICE_PYTHON = OPENVOICE_DIR / "OpenVoice" / "venv" / "Scripts" / "python.exe"
OPENVOICE_SERVE_SCRIPT = OPENVOICE_DIR / "serve.py"
OPENVOICE_PORT = 8766
OPENVOICE_URL = f"http://127.0.0.1:{OPENVOICE_PORT}"


class OpenVoiceClient:
    """Le habla por HTTP al servidor de OpenVoice V2 (ver
    openvoice_server/serve.py), que corre en su PROPIO venv/proceso
    porque sus dependencias (numpy viejo, transformers viejo, etc) chocan
    con las que ya usa XTTS en este venv principal - no se pueden mezclar
    en un mismo proceso (ver la seccion de OpenVoice en el README).

    A proposito NO arranca el servidor en __init__: el usuario pidio que
    esta voz no quede cargada de entrada (para no gastar VRAM/tiempo si no
    se usa), asi que el arranque real pasa en ensure_ready(), llamado
    recien cuando se elige esta voz por primera vez."""

    def __init__(self):
        self._process = None

    def _start_server(self):
        if self._process is not None and self._process.poll() is None:
            return  # ya esta corriendo, lanzado por esta misma instancia
        try:
            r = requests.get(f"{OPENVOICE_URL}/health", timeout=1)
            if r.ok:
                return  # ya hay un servidor escuchando (de un arranque anterior de Jarvis
                # que no se cerro bien) - lo reusa en vez de lanzar otro duplicado que
                # cargaria el modelo en VRAM de nuevo solo para fallar al bindear el puerto
        except requests.RequestException:
            pass
        logger.info("Arrancando el proceso del servidor OpenVoice...")
        self._process = subprocess.Popen(
            [str(OPENVOICE_PYTHON), str(OPENVOICE_SERVE_SCRIPT)],
            cwd=str(OPENVOICE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def ensure_ready(self, timeout: float = 180.0):
        """Bloquea hasta que el modelo este cargado y listo para sintetizar.
        Arranca el proceso si todavia no esta corriendo (idempotente: si ya
        esta listo, vuelve al toque)."""
        self._start_server()
        deadline = time.time() + timeout
        while time.time() < deadline:
            # self._process es None cuando _start_server() reuso un servidor
            # ya corriendo (lanzado por un arranque anterior de Jarvis) en
            # vez de lanzar uno propio - en ese caso no hay proceso propio
            # que vigilar, solo importa si el servidor (de quien sea)
            # responde ready.
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(
                    f"El proceso de OpenVoice se cerro solo (codigo {self._process.returncode})."
                )
            try:
                r = requests.get(f"{OPENVOICE_URL}/health", timeout=2)
                if r.ok:
                    data = r.json()
                    if data.get("error"):
                        raise RuntimeError(f"OpenVoice fallo al cargar: {data['error']}")
                    if data.get("ready"):
                        return
            except requests.RequestException:
                pass  # el servidor todavia no acepta conexiones, se sigue esperando
            time.sleep(0.5)
        raise RuntimeError("El servidor de OpenVoice no respondio a tiempo.")

    def is_ready(self) -> bool:
        try:
            r = requests.get(f"{OPENVOICE_URL}/health", timeout=1)
            return r.ok and r.json().get("ready", False)
        except requests.RequestException:
            return False

    def synthesize_wav_bytes(self, text: str) -> bytes:
        r = requests.post(f"{OPENVOICE_URL}/synthesize", json={"text": text}, timeout=30)
        r.raise_for_status()
        return r.content

    def speak(self, text: str):
        audio_bytes = self.synthesize_wav_bytes(text)
        data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        sd.play(data, samplerate=sample_rate)
        sd.wait()

    def stop(self):
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
        self._process = None
