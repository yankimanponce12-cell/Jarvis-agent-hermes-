"""Benchmark de velocidad + escucha para XTTS v2 (el motor que Jarvis usa
HOY en produccion), para comparar en igualdad de condiciones contra las
alternativas de benchmarks/piper y benchmarks/openvoice.

Mide sintesis COMPLETA y bloqueante (synthesize_wav_bytes: genera todo el
audio de la oracion antes de devolver), NO el modo streaming de baja
latencia que ya tiene jarvis/tts_xtts.py para speak() en produccion - eso
es a proposito, porque las otras alternativas tambien se miden con una
sola llamada bloqueante de "generar audio completo de una oracion". Este
numero no es la latencia real que sentis hablando con Jarvis (esa es mas
baja gracias al streaming), es la base de comparacion pareja entre
motores.

Corre con el venv del proyecto principal (el de la carpeta jarvis, un
nivel arriba de benchmarks/), porque XTTS ya esta instalado ahi:
    ..\\..\\venv\\Scripts\\python bench.py
"""

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from jarvis.tts_xtts import XTTSSpeaker  # noqa: E402

OUTPUT_DIR = Path(__file__).parent / "output"
TEST_TEXT = "Hola, señor. Esta es una prueba del motor XTTS."
N_RUNS = 5


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    print("Cargando XTTS v2 (puede tardar)...")
    t0 = time.time()
    speaker = XTTSSpeaker()
    print(f"Modelo listo en {time.time() - t0:.2f}s (device={speaker.device})")

    times = []
    for i in range(N_RUNS):
        t0 = time.time()
        wav_bytes = speaker.synthesize_wav_bytes(TEST_TEXT)
        elapsed = time.time() - t0
        times.append(elapsed)
        out_path = OUTPUT_DIR / f"xtts_run{i}.wav"
        out_path.write_bytes(wav_bytes)
        print(f"  run {i}: {elapsed * 1000:.0f}ms -> {out_path.name}")

    avg_ms = sum(times) / len(times) * 1000
    print(f"\nPromedio ({N_RUNS} corridas, modelo ya cargado): {avg_ms:.0f}ms")
    print(f"Audios guardados en {OUTPUT_DIR} para escuchar.")


if __name__ == "__main__":
    main()
