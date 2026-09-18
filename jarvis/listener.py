import glob
import os
import queue
import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
BLOCK_DURATION = 0.5  # segundos por bloque de audio
SILENCE_THRESHOLD = 0.01  # umbral de energia (RMS) para considerar silencio
SILENCE_BLOCKS_TO_STOP = 3  # ~1.5s de silencio para dejar de grabar
MIN_SPEECH_BLOCKS = 2  # evita cortar la grabacion apenas empieza a hablar
MAX_DURATION = 12  # tope duro de grabacion en segundos
PARTIAL_INTERVAL_BLOCKS = 3  # dispara una transcripcion parcial cada ~1.5s (3 bloques)

# Whisper suele transcribir mal la primera palabra de un audio porque no
# tiene contexto previo; darle "Jarvis" como prompt inicial evita que la
# wake word salga irreconocible (probado: sin esto, "Jarvis abre..." se
# transcribia como "Ya arbisable...").
WAKE_WORD_PROMPT = "Jarvis, activa el asistente de voz."


def _register_cuda_dll_dirs():
    """ctranslate2 (motor de faster-whisper) no trae su propio runtime CUDA
    como si hace torch; pide cublas64_12.dll por nombre exacto, que torch no
    trae (torch usa CUDA 13, cublas64_13.dll). Se usa la copia que instala
    el paquete pip nvidia-cublas-cu12, registrando su carpeta explicitamente
    en vez de depender del PATH heredado (mismo motivo que con FFmpeg en
    tts_xtts.py).

    OJO: a proposito NO se registra una carpeta de cudnn aparte. torch ya
    trae su propio cudnn64_9.dll (se registra solo al hacer `import torch`)
    y XTTS lo necesita; si se agrega ademas el nvidia-cudnn-cu12 instalado
    por separado, quedan dos copias de cudnn9 con distinta version visibles
    a la vez y Windows puede mezclar el dispatcher de una con las
    sub-librerias de la otra -> CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH.
    ctranslate2 tambien pide cudnn64_9.dll por nombre, asi que reusa sin
    problema el que ya registro torch."""
    if os.name != "nt":
        return
    import sysconfig

    site_packages = sysconfig.get_paths()["purelib"]
    for bin_dir in glob.glob(os.path.join(site_packages, "nvidia/cublas/bin")):
        os.add_dll_directory(bin_dir)
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


def _pick_device_and_compute_type():
    try:
        import torch

        if torch.cuda.is_available():
            _register_cuda_dll_dirs()
            return "cuda", "float16"
    except ImportError:
        pass
    return "cpu", "int8"


class VoiceListener:
    def __init__(self, model_size="small", device=None, compute_type=None):
        # import diferido (no al nivel del modulo): importar faster_whisper
        # tarda ~2.8s por si solo, y como listener.py se importa desde el
        # arranque de main.py, ese costo se pagaba ANTES de que la ventana
        # del HUD llegara a aparecer. Con el import aca adentro, se paga
        # recien cuando VoiceListener() se construye de verdad (que ya
        # pasa en un hilo de fondo, con la ventana ya visible).
        from faster_whisper import WhisperModel

        if device is None or compute_type is None:
            auto_device, auto_compute_type = _pick_device_and_compute_type()
            device = device or auto_device
            compute_type = compute_type or auto_compute_type
        print(f"Cargando modelo Whisper ({model_size}, {device})... la primera vez puede tardar.")
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self._partial_lock = threading.Lock()
        self._latest_partial_seq = 0
        self._partial_busy = False

    def _fire_partial(self, blocks_snapshot: list, language: str, on_partial) -> bool:
        """Dispara una transcripcion parcial del audio acumulado hasta
        ahora, en un hilo aparte para no frenar la captura de audio en
        tiempo real (que tiene que seguir leyendo el microfono cada
        BLOCK_DURATION). Si mientras tanto se dispara una parcial mas
        nueva (o la grabacion ya termino), esta se descarta al terminar en
        vez de pisar un resultado mas fresco con uno mas viejo.

        Si ya hay una parcial corriendo, esta llamada no hace nada (y
        devuelve False): la transcripcion crece con el audio acumulado, asi
        que puede tardar mas que el intervalo entre disparos, y lanzar una
        nueva GPU sin esperar a que termine la anterior las apila todas
        compitiendo por la misma GPU, dejando al HUD (que tambien depende
        de la GPU para sus animaciones) a los tumbos."""
        with self._partial_lock:
            if self._partial_busy:
                return False
            self._partial_busy = True
            self._latest_partial_seq += 1
            seq = self._latest_partial_seq
        audio_so_far = np.concatenate(blocks_snapshot, axis=0).flatten()

        def work():
            try:
                text = self._safe_transcribe(audio_so_far, language)
            finally:
                with self._partial_lock:
                    self._partial_busy = False
            if not text:
                return
            with self._partial_lock:
                if seq != self._latest_partial_seq:
                    return  # quedo vieja: ya hay una parcial mas nueva (o la grabacion termino)
            on_partial(text)

        threading.Thread(target=work, daemon=True).start()
        return True

    def _invalidate_pending_partials(self):
        """Se llama al terminar de grabar una frase: cualquier transcripcion
        parcial que siga en vuelo a partir de aca queda vieja y no debe
        pisar el resultado final (mas preciso) que se muestra despues."""
        with self._partial_lock:
            self._latest_partial_seq += 1

    def _record(self, language="es", on_partial=None):
        block_size = int(SAMPLE_RATE * BLOCK_DURATION)
        q = queue.Queue()

        def callback(indata, frames, time_info, status):
            q.put(indata.copy())

        blocks = []
        silence_run = 0
        speech_blocks = 0
        blocks_since_partial = 0

        print("Escuchando... habla ahora.")
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=block_size,
            callback=callback,
        ):
            while True:
                block = q.get()
                blocks.append(block)
                blocks_since_partial += 1
                rms = float(np.sqrt(np.mean(block**2)))

                if rms > SILENCE_THRESHOLD:
                    silence_run = 0
                    speech_blocks += 1
                else:
                    silence_run += 1

                if (
                    on_partial is not None
                    and speech_blocks >= MIN_SPEECH_BLOCKS
                    and blocks_since_partial >= PARTIAL_INTERVAL_BLOCKS
                ):
                    if self._fire_partial(list(blocks), language, on_partial):
                        blocks_since_partial = 0

                elapsed = len(blocks) * BLOCK_DURATION
                if speech_blocks >= MIN_SPEECH_BLOCKS and silence_run >= SILENCE_BLOCKS_TO_STOP:
                    break
                if elapsed >= MAX_DURATION:
                    break

        self._invalidate_pending_partials()
        return np.concatenate(blocks, axis=0).flatten()

    def _safe_transcribe(self, audio: np.ndarray, language: str) -> str:
        """Corre el modelo de Whisper y devuelve el texto transcrito. Nunca
        lanza excepcion hacia el llamador: una falla transitoria de CUDA (se
        vio pasar en la practica: 'device-side assert triggered') no debe
        tirar abajo todo Jarvis - simplemente se descarta esa frase y se
        sigue escuchando, igual que si se hubiera transcrito vacia."""
        try:
            segments, _ = self.model.transcribe(
                audio,
                language=language,
                initial_prompt=WAKE_WORD_PROMPT,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            print(f"No se pudo transcribir ese audio, se descarta: {exc}")
            return ""

    def listen_and_transcribe(self, language="es", on_partial=None):
        audio = self._record(language, on_partial)
        if audio.size == 0:
            return ""
        return self._safe_transcribe(audio, language)

    def _record_utterance_from_stream(self, q, language="es", on_partial=None):
        """Espera sin limite de tiempo a que se detecte voz, luego graba hasta
        que hay silencio. Si solo fue un ruido breve, se descarta y se vuelve
        a esperar (sin gastar el MAX_DURATION completo en silencio)."""
        while True:
            first_block = q.get()
            rms = float(np.sqrt(np.mean(first_block**2)))
            if rms > SILENCE_THRESHOLD:
                break

        blocks = [first_block]
        silence_run = 0
        speech_blocks = 1
        blocks_since_partial = 0

        while True:
            block = q.get()
            blocks.append(block)
            blocks_since_partial += 1
            rms = float(np.sqrt(np.mean(block**2)))

            if rms > SILENCE_THRESHOLD:
                silence_run = 0
                speech_blocks += 1
            else:
                silence_run += 1

            if (
                on_partial is not None
                and speech_blocks >= MIN_SPEECH_BLOCKS
                and blocks_since_partial >= PARTIAL_INTERVAL_BLOCKS
            ):
                if self._fire_partial(list(blocks), language, on_partial):
                    blocks_since_partial = 0

            elapsed = len(blocks) * BLOCK_DURATION
            if speech_blocks >= MIN_SPEECH_BLOCKS and silence_run >= SILENCE_BLOCKS_TO_STOP:
                self._invalidate_pending_partials()
                return np.concatenate(blocks, axis=0).flatten()
            if speech_blocks < MIN_SPEECH_BLOCKS and silence_run >= SILENCE_BLOCKS_TO_STOP + 2:
                self._invalidate_pending_partials()
                return np.empty(0, dtype="float32")  # fue solo ruido, descartar
            if elapsed >= MAX_DURATION:
                self._invalidate_pending_partials()
                return np.concatenate(blocks, axis=0).flatten()

    def flush_pending_audio(self):
        """Descarta el audio acumulado en el buffer del microfono (por ejemplo
        el que se capto mientras Jarvis estaba hablando por los parlantes),
        para que no se transcriba su propia voz al reanudar la escucha."""
        q = getattr(self, "_current_queue", None)
        if q is None:
            return
        while not q.empty():
            try:
                q.get_nowait()
            except queue.Empty:
                break

    def listen_forever(self, language="es", on_partial=None):
        """Generador: bloquea hasta capturar cada frase dicha frente al
        microfono (sin necesidad de presionar Enter) y va cediendo el texto
        transcrito de cada una, indefinidamente."""
        block_size = int(SAMPLE_RATE * BLOCK_DURATION)
        q = queue.Queue()
        self._current_queue = q

        def callback(indata, frames, time_info, status):
            q.put(indata.copy())

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=block_size,
            callback=callback,
        ):
            while True:
                audio = self._record_utterance_from_stream(q, language, on_partial)
                if audio.size == 0:
                    continue
                text = self._safe_transcribe(audio, language)
                if text:
                    yield text

    def transcribe_array(self, audio: np.ndarray, language="es"):
        return self._safe_transcribe(audio, language)
