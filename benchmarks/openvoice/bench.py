"""Benchmark de velocidad + escucha para OpenVoice V2 (MyShell, MIT),
clonando la voz real de Jarvis (samples/jarvis_reference.wav), para
comparar contra benchmarks/xtts (el motor actual) y benchmarks/piper.

Arquitectura: MeloTTS genera el audio base en una voz neutra, y despues un
"tone color converter" liviano lo transforma para que suene con el timbre
del wav de referencia. Reportado: ~2-3GB VRAM, conversion de tono en
~100ms - deberia entrar comodo en los 8GB de la RTX 4060 de esta laptop
(a diferencia de Fish Speech, que se descarto por esto).

SETUP REAL (probado y funcionando en esta laptop, python 3.10 - el 3.14
del sistema es MUY nuevo para las dependencias viejas que pide este repo):
    cd benchmarks/openvoice
    git clone https://github.com/myshell-ai/OpenVoice.git
    cd OpenVoice

    # usar Python 3.10 especificamente para crear el venv, no el default
    # del sistema (numpy==1.22.0 y otros pines viejos no tienen wheel
    # para versiones nuevas de Python):
    C:\\ruta\\a\\Python310\\python.exe -m venv venv

    # editar setup.py ANTES de instalar: cambiar 'faster-whisper==0.9.0'
    # por 'faster-whisper>=1.0' (el pin viejo exige av==10.*, que no
    # tiene wheel precompilado aca y no hay compilador de C instalado
    # para armarlo desde cero; faster-whisper moderno acepta av>=11, que
    # si tiene wheel).
    venv\\Scripts\\pip install -e .

    # pkg_resources fue removido de setuptools nuevo y lo necesita
    # librosa==0.9.1 (pin viejo de este repo):
    venv\\Scripts\\pip install "setuptools<81"

    # pip install -e . trae torch/torchaudio CPU-only por defecto: forzar
    # la build con CUDA (ajustar cu130 a lo que tengas, ver el venv
    # principal del proyecto con: python -c "import torch; print(torch.__version__)"):
    venv\\Scripts\\pip install -U torch torchaudio --index-url https://download.pytorch.org/whl/cu130

    venv\\Scripts\\pip install git+https://github.com/myshell-ai/MeloTTS.git
    venv\\Scripts\\python -m unidic download

    # El link .zip que trae la doc oficial esta roto (404 confirmado, hay
    # un issue abierto en su repo por lo mismo). Usar el mirror en
    # Hugging Face en su lugar (usar "hf download", no "huggingface-cli
    # download": ese esta deprecado y en Windows su warning de
    # deprecacion tira UnicodeEncodeError en la consola y corta todo):
    venv\\Scripts\\pip install "huggingface_hub[cli]"
    venv\\Scripts\\hf download myshell-ai/OpenVoiceV2 --local-dir checkpoints_v2

    # se_extractor usa VAD de silero-vad via torch.hub, que la primera
    # vez pide confirmar interactivamente que confias en el repo - si se
    # corre sin consola interactiva (como bench.py) tira EOFError. Correr
    # esto UNA vez a mano antes para autorizarlo y que quede en cache:
    venv\\Scripts\\python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True, onnx=False)"

CORRER (desde benchmarks/openvoice/OpenVoice, con su venv activado):
    python ..\\bench.py
"""

import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
REPO_DIR = HERE / "OpenVoice"
CHECKPOINT_DIR = REPO_DIR / "checkpoints_v2"
OUTPUT_DIR = HERE / "output"

REFERENCE_WAV = HERE.parent.parent / "samples" / "jarvis_reference.wav"
TEST_TEXT = "Hola, señor. Esta es una prueba del motor OpenVoice."
LANGUAGE = "ES"
N_RUNS = 5

# tau = "fuerza" de la conversion de timbre (default de la libreria: 0.3).
# Mas bajo generalmente se parece mas a la voz de referencia (a cambio de
# sonar un poco menos natural); mas alto es mas natural pero se aleja del
# timbre original. Probado a oido contra jarvis_reference.wav: 0.1 fue el
# elegido (mas parecido que el default 0.3, sin sonar raro todavia).
TAU_VALUES = [0.1, 0.3, 0.2, 0.05]


def main():
    if not CHECKPOINT_DIR.exists():
        raise SystemExit(
            f"No se encontro {CHECKPOINT_DIR}. Segui los pasos de SETUP en "
            f"el docstring de este archivo antes de correr el benchmark."
        )

    import torch
    from openvoice import se_extractor
    from openvoice.api import ToneColorConverter
    from melo.api import TTS

    OUTPUT_DIR.mkdir(exist_ok=True)
    device = "cuda:0" if torch.cuda.is_available() else "cpu"

    print("Cargando tone color converter...")
    t0 = time.time()
    converter_dir = CHECKPOINT_DIR / "converter"
    tone_color_converter = ToneColorConverter(str(converter_dir / "config.json"), device=device)
    tone_color_converter.load_ckpt(str(converter_dir / "checkpoint.pth"))

    print(f"Cargando MeloTTS ({LANGUAGE})...")
    tts_model = TTS(language=LANGUAGE, device=device)
    speaker_ids = tts_model.hps.data.spk2id
    speaker_key = next(iter(speaker_ids.keys()))
    speaker_id = speaker_ids[speaker_key]
    source_se = torch.load(
        str(CHECKPOINT_DIR / "base_speakers" / "ses" / f"{speaker_key.lower().replace('_', '-')}.pth"),
        map_location=device,
    )
    print(f"Modelos listos en {time.time() - t0:.2f}s (device={device})")

    print("Extrayendo el embedding de la voz de referencia de Jarvis...")
    t0 = time.time()
    target_se, _ = se_extractor.get_se(str(REFERENCE_WAV), tone_color_converter, vad=True)
    print(f"Embedding listo en {time.time() - t0:.2f}s (esto se hace UNA sola vez, no por frase)")

    tmp_path = OUTPUT_DIR / "_tmp_base.wav"
    tts_model.tts_to_file(TEST_TEXT, speaker_id, str(tmp_path), speed=1.0)

    print(f"\nGenerando una version por cada tau: {TAU_VALUES}")
    for tau in TAU_VALUES:
        out_path = OUTPUT_DIR / f"openvoice_tau{tau}.wav"
        t0 = time.time()
        tone_color_converter.convert(
            audio_src_path=str(tmp_path),
            src_se=source_se,
            tgt_se=target_se,
            output_path=str(out_path),
            tau=tau,
        )
        elapsed = time.time() - t0
        print(f"  tau={tau}: {elapsed * 1000:.0f}ms -> {out_path.name}")

    print(f"\nAudios guardados en {OUTPUT_DIR} para escuchar y comparar cual tau se parece mas.")

    times = []
    for i in range(N_RUNS):
        t0 = time.time()
        tts_model.tts_to_file(TEST_TEXT, speaker_id, str(tmp_path), speed=1.0)
        out_path = OUTPUT_DIR / f"openvoice_run{i}.wav"
        tone_color_converter.convert(
            audio_src_path=str(tmp_path),
            src_se=source_se,
            tgt_se=target_se,
            output_path=str(out_path),
        )
        elapsed = time.time() - t0
        times.append(elapsed)
        print(f"  run {i}: {elapsed * 1000:.0f}ms -> {out_path.name}")

    avg_ms = sum(times) / len(times) * 1000
    print(f"\nPromedio velocidad ({N_RUNS} corridas, tau default={TAU_VALUES[0]}): {avg_ms:.0f}ms")


if __name__ == "__main__":
    main()
