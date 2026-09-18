"""Benchmark de velocidad + escucha para Piper TTS.

Piper NO clona voces (usa modelos ya entrenados con voces fijas): esto
mide que tan rapido es el motor EN SI con una voz de stock en espanol,
como piso minimo de latencia, antes de invertir tiempo en entrenar una
voz custom con la referencia de Jarvis (posible con Piper, pero es un
paso aparte con su propio dataset y entrenamiento).

Corre 100% en CPU (no necesita GPU), modelos ONNX de ~100MB.

SETUP (una sola vez):
    cd benchmarks/piper
    python -m venv venv
    venv\\Scripts\\pip install -r requirements.txt
    venv\\Scripts\\python -m piper.download_voices
    (el comando de arriba, SIN argumentos, imprime el catalogo completo de
    voces - elegi una es_MX o es_ES y pegala abajo en MODEL_NAME)

CORRER:
    venv\\Scripts\\python bench.py
"""

import subprocess
import sys
import time
import wave
from pathlib import Path

HERE = Path(__file__).parent
MODELS_DIR = HERE / "models"
OUTPUT_DIR = HERE / "output"

# Cambiar por una voz real de la lista que imprime
# "python -m piper.download_voices" (ver docstring de arriba).
MODEL_NAME = "es_MX-claude-high"

TEST_TEXT = "Hola, señor. Esta es una prueba del motor Piper."
N_RUNS = 5


def ensure_model() -> Path:
    onnx_path = MODELS_DIR / f"{MODEL_NAME}.onnx"
    if not onnx_path.exists():
        MODELS_DIR.mkdir(exist_ok=True)
        print(f"Descargando voz {MODEL_NAME}...")
        subprocess.run(
            [sys.executable, "-m", "piper.download_voices", MODEL_NAME],
            cwd=MODELS_DIR,
            check=True,
        )
    if not onnx_path.exists():
        raise FileNotFoundError(
            f"No se encontro {onnx_path} despues de la descarga. Revisa el "
            f"nombre exacto del modelo con: python -m piper.download_voices"
        )
    return onnx_path


def main():
    from piper import PiperVoice

    OUTPUT_DIR.mkdir(exist_ok=True)
    onnx_path = ensure_model()

    print("Cargando el modelo...")
    t0 = time.time()
    voice = PiperVoice.load(str(onnx_path))
    print(f"Modelo listo en {time.time() - t0:.2f}s (CPU)")

    times = []
    for i in range(N_RUNS):
        out_path = OUTPUT_DIR / f"piper_run{i}.wav"
        t0 = time.time()
        with wave.open(str(out_path), "wb") as wav_file:
            voice.synthesize_wav(TEST_TEXT, wav_file)
        elapsed = time.time() - t0
        times.append(elapsed)
        print(f"  run {i}: {elapsed * 1000:.0f}ms -> {out_path.name}")

    avg_ms = sum(times) / len(times) * 1000
    print(f"\nPromedio ({N_RUNS} corridas, modelo ya cargado): {avg_ms:.0f}ms")
    print(f"Audios guardados en {OUTPUT_DIR} para escuchar.")


if __name__ == "__main__":
    main()
