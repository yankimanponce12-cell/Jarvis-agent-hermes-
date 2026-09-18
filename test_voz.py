"""Prueba manual del sistema de voz de Jarvis (XTTS v2 local).

Corre: python test_voz.py

Revisa logs/jarvis.log despues de correr esto para ver si algo fallo (los
errores del motor de voz se registran ahi, no se imprimen en consola).
"""

from jarvis.tts_xtts import XTTSSpeaker


def test_xtts():
    print("=== XTTS v2 local ===")
    print("Cargando el modelo, puede tardar la primera vez...")
    try:
        speaker = XTTSSpeaker()
        speaker.speak("Hola, señor. Esta es una prueba del motor local XTTS.")
        print(f"OK: XTTS genero y reprodujo el audio (device={speaker.device}).")
    except Exception as exc:
        print(f"XTTS fallo: {exc}")


if __name__ == "__main__":
    test_xtts()
