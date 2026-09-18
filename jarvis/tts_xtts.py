import glob
import io
import logging
import os
import queue
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

# Los pesos de XTTS v2 se distribuyen bajo la licencia CPML de Coqui (uso NO
# comercial; para uso comercial hace falta licencia paga de Coqui). Al
# fijar esta variable se acepta esa licencia de forma no interactiva, en
# vez de que la descarga se trabe esperando un "y/n" por consola. Ver:
# https://coqui.ai/cpml
os.environ.setdefault("COQUI_TOS_AGREED", "1")

logger = logging.getLogger("jarvis.voice")


def _register_ffmpeg_dll_dir():
    """torchcodec (requerido por XTTS/torchaudio) necesita las DLLs de
    FFmpeg para decodificar audio. El PATH del proceso puede no incluirlas
    todavia (p.ej. si FFmpeg se instalo con winget en esta misma sesion, ya
    que los procesos ya abiertos no ven el PATH actualizado hasta reiniciar).
    Como respaldo, se busca el ffmpeg instalado via winget y se registra su
    carpeta con os.add_dll_directory, sin depender del PATH heredado."""
    if os.name != "nt":
        return

    # importante: tiene que ser la variante "shared" (con avutil-*.dll,
    # avcodec-*.dll sueltas); la variante estatica de FFmpeg (solo
    # ffmpeg.exe/ffplay.exe/ffprobe.exe, sin DLLs) no le sirve a torchcodec.
    candidates = glob.glob(
        os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg.Shared_*\ffmpeg-*-shared\bin"
        )
    )
    for bin_dir in candidates:
        if glob.glob(os.path.join(bin_dir, "avutil-*.dll")):
            os.add_dll_directory(bin_dir)
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            logger.info(f"FFmpeg (shared) encontrado y registrado desde {bin_dir}.")
            return
    logger.warning("No se encontro FFmpeg (variante shared); XTTS podria fallar al cargar audio.")


_register_ffmpeg_dll_dir()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REFERENCE_WAV = PROJECT_ROOT / "samples" / "jarvis_reference.wav"
MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"


class XTTSSpeaker:
    """Motor de voz local de respaldo: Coqui XTTS v2. Clona la voz de
    referencia en samples/jarvis_reference.wav. El modelo se carga una sola
    vez en __init__ (es lento: unos segundos a minutos segun CPU/GPU)."""

    def __init__(self, reference_wav: str | Path = DEFAULT_REFERENCE_WAV, language: str = "es"):
        self.reference_wav = str(reference_wav)
        self.language = language
        self._load_model()
        self._load_voice_conditioning()

    def _load_model(self):
        import torch
        from TTS.api import TTS

        if torch.cuda.is_available():
            self.device = "cuda"
        else:
            self.device = "cpu"
            logger.warning("CUDA no disponible: XTTS v2 correra en CPU (sera mas lento).")

        t0 = time.time()
        self.model = TTS(MODEL_NAME).to(self.device)
        logger.info(f"XTTS v2 cargado en {self.device} en {time.time() - t0:.1f}s.")

    def _load_voice_conditioning(self):
        """Calcula una sola vez los 'latentes' de clonado de voz a partir de
        la referencia. Usar el metodo de alto nivel model.tts(speaker_wav=...)
        los recalculaba desde cero en CADA frase que Jarvis decia, y ese
        recalculo resultaba ser el verdadero cuello de botella de la
        sintesis (una respuesta de ~8s de audio tardaba ~29s en generarse,
        vs ~7.5s reutilizando los latentes ya calculados aqui)."""
        t0 = time.time()
        self._gpt_cond_latent, self._speaker_embedding = self.model.synthesizer.tts_model.get_conditioning_latents(
            audio_path=self.reference_wav
        )
        logger.info(f"Latentes de voz calculados en {time.time() - t0:.1f}s.")

    def _synthesize_sentence(self, sentence: str, language: str | None = None) -> np.ndarray:
        """Sintetiza UNA sola oracion ya recortada (no vacia), de una sola
        vez (bloqueante). La usa _synthesize() (junta todo antes de
        devolver, para mandar un WAV completo al telefono). speak() ya NO
        la usa: usa _stream_sentence(), que devuelve el audio en pedazos en
        vez de esperar la oracion entera (ver comentario ahi)."""
        tts_model = self.model.synthesizer.tts_model
        out = tts_model.inference(
            sentence,
            language or self.language,
            self._gpt_cond_latent,
            self._speaker_embedding,
        )
        return np.asarray(out["wav"], dtype="float32")

    def _stream_sentence(self, sentence: str, language: str | None = None):
        """Generador de a pedazos (chunks numpy float32) del audio de UNA
        oracion, usando inference_stream() en vez de inference(): el
        primer pedazo sale en ~200-400ms (GPU) apenas arranca a generar,
        en vez de esperar a que toda la oracion este lista (~2.5-3s), que
        era el delay real que se sentia en speak()."""
        tts_model = self.model.synthesizer.tts_model
        for wav_chunk in tts_model.inference_stream(
            sentence,
            language or self.language,
            self._gpt_cond_latent,
            self._speaker_embedding,
        ):
            yield wav_chunk.squeeze().cpu().numpy().astype("float32")

    def _split_sentences(self, text: str) -> list[str]:
        sentences = self.model.synthesizer.split_into_sentences(text) or [text]
        return [s for s in sentences if s.strip()]

    def _synthesize(self, text: str, language: str | None = None) -> np.ndarray | None:
        """Genera el audio (float32, sample rate = output_sample_rate) para
        `text` con la voz clonada, sin reproducirlo. Devuelve None si no
        quedo nada que sintetizar (texto vacio/solo espacios). La usa
        synthesize_wav_bytes() (le tiene que mandar el WAV completo al
        telefono de una), no speak() (ver mas abajo)."""
        sentences = self._split_sentences(text)
        if not sentences:
            return None
        chunks = [self._synthesize_sentence(s, language) for s in sentences]
        return np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

    def speak(self, text: str, language: str | None = None):
        """Sintetiza y reproduce por los parlantes de la PC en streaming,
        pedazo por pedazo (ver _stream_sentence), en vez de esperar cada
        oracion completa como antes.

        La sintesis corre en un HILO DE FONDO separado (productor) que va
        metiendo chunks en una cola en orden (uno por oracion, en orden,
        porque la GPU sintetiza de a una igual), mientras este metodo
        (consumidor) los escribe a un unico sd.OutputStream ya abierto a
        medida que llegan. Con hilos separados, la reproduccion nunca
        espera mas que lo que el productor tarde en tener listo el
        siguiente chunk (tipicamente ~200-400ms en GPU, contra los
        ~2.5-3s por oracion completa de antes), y al ser un solo stream
        continuo (no un play()/wait() por oracion) tampoco hay hueco de
        silencio entre oraciones.

        Antes de arrancar a reproducir se junta un pequeño colchon de
        chunks ya generados (no solo el primero): la GPU no siempre tarda
        lo mismo en generar cada chunk, y si el consumidor le pisa los
        talones al productor, un chunk que se demora un toque mas deja al
        dispositivo de audio sin datos para reproducir - eso se escucha
        como un corte/click. Con colchon, el productor normalmente ya le
        saca ventaja al consumidor antes de que se note."""
        sentences = self._split_sentences(text)
        if not sentences:
            return

        sample_rate = self.model.synthesizer.output_sample_rate
        chunk_queue = queue.Queue()
        END = object()

        def produce():
            for sentence in sentences:
                for chunk in self._stream_sentence(sentence, language):
                    chunk_queue.put(chunk)
            chunk_queue.put(END)

        producer = threading.Thread(target=produce, daemon=True)
        producer.start()

        PREBUFFER_CHUNKS = 3
        buffer = []
        while len(buffer) < PREBUFFER_CHUNKS:
            item = chunk_queue.get()
            buffer.append(item)
            if item is END:
                break

        # latency="high" le da a PortAudio mas margen interno de buffer,
        # lo que tambien ayuda a absorber esas variaciones de timing.
        with sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32", latency="high") as stream:
            while True:
                chunk = buffer.pop(0) if buffer else chunk_queue.get()
                if chunk is END:
                    break
                stream.write(chunk)

        producer.join()

    def synthesize_wav_bytes(self, text: str, language: str | None = None) -> bytes:
        """Igual que speak(), pero devuelve los bytes de un WAV en vez de
        reproducirlo por los parlantes de la PC: la usa el servidor (ver
        server.py) para mandarle al telefono la respuesta con la voz real
        de Jarvis en vez de la voz generica del navegador."""
        if not text:
            return b""
        data = self._synthesize(text, language)
        if data is None:
            return b""
        buf = io.BytesIO()
        sf.write(buf, data, self.model.synthesizer.output_sample_rate, format="WAV")
        return buf.getvalue()
