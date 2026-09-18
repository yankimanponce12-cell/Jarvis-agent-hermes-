"""Servidor HTTP local para el motor de voz OpenVoice V2, pensado para
correr como proceso APARTE bajo su propio venv
(openvoice_server/OpenVoice/venv), porque sus dependencias (numpy
viejo, transformers viejo, etc, ver la seccion de OpenVoice en el README)
chocan con las que ya usa XTTS en el venv principal del proyecto - no se
pueden mezclar en un mismo proceso.

jarvis/tts_openvoice.py (que corre en el proceso principal de Jarvis, con
el venv de siempre) lo arranca BAJO DEMANDA como subprocess la primera vez
que se elige esta voz (no arranca solo con Jarvis: el usuario pidio que no
quede cargada de entrada, para no gastar VRAM/tiempo si nunca se usa) y le
habla por HTTP en localhost mientras dura la sesion.

Correr a mano (para probar):
    openvoice_server/OpenVoice/venv/Scripts/python.exe serve.py
"""

import logging
import threading
import time
from pathlib import Path

import torch
from flask import Flask, Response, jsonify, request

logging.basicConfig(level=logging.INFO, format="[openvoice-serve] %(message)s")
logger = logging.getLogger("openvoice-serve")

HERE = Path(__file__).resolve().parent
CHECKPOINT_DIR = HERE / "OpenVoice" / "checkpoints_v2"
REFERENCE_WAV = HERE.parent / "samples" / "jarvis_reference.wav"
LANGUAGE = "ES"
TAU = 0.1  # elegido a oido comparando contra jarvis_reference.wav
PORT = 8766

app = Flask(__name__)
_state = {"ready": False, "error": None}
_synth_lock = threading.Lock()  # la GPU sintetiza de a una, no hay que paralelizar


def _load():
    from openvoice import se_extractor
    from openvoice.api import ToneColorConverter
    from melo.api import TTS

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    logger.info(f"Cargando tone color converter (device={device})...")
    converter_dir = CHECKPOINT_DIR / "converter"
    tone_color_converter = ToneColorConverter(str(converter_dir / "config.json"), device=device)
    tone_color_converter.load_ckpt(str(converter_dir / "checkpoint.pth"))

    logger.info(f"Cargando MeloTTS ({LANGUAGE})...")
    tts_model = TTS(language=LANGUAGE, device=device)
    speaker_ids = tts_model.hps.data.spk2id
    speaker_key = next(iter(speaker_ids.keys()))
    speaker_id = speaker_ids[speaker_key]
    source_se = torch.load(
        str(CHECKPOINT_DIR / "base_speakers" / "ses" / f"{speaker_key.lower().replace('_', '-')}.pth"),
        map_location=device,
    )

    logger.info("Extrayendo el embedding de la voz de referencia de Jarvis...")
    target_se, _ = se_extractor.get_se(str(REFERENCE_WAV), tone_color_converter, vad=True)

    _state.update(
        tone_color_converter=tone_color_converter,
        tts_model=tts_model,
        speaker_id=speaker_id,
        source_se=source_se,
        target_se=target_se,
        tmp_path=str(HERE / "_serve_tmp.wav"),
        ready=True,
    )
    logger.info("Listo.")


@app.route("/health")
def health():
    return jsonify({"ready": _state["ready"], "error": _state["error"]})


@app.route("/synthesize", methods=["POST"])
def synthesize():
    if not _state["ready"]:
        return jsonify({"error": "modelo todavia cargando"}), 503

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "falta 'text'"}), 400

    out_path = HERE / "_serve_out.wav"
    with _synth_lock:
        _state["tts_model"].tts_to_file(text, _state["speaker_id"], _state["tmp_path"], speed=1.0)
        _state["tone_color_converter"].convert(
            audio_src_path=_state["tmp_path"],
            src_se=_state["source_se"],
            tgt_se=_state["target_se"],
            output_path=str(out_path),
            tau=TAU,
        )
        audio_bytes = out_path.read_bytes()
    return Response(audio_bytes, mimetype="audio/wav")


if __name__ == "__main__":
    t0 = time.time()
    try:
        _load()
    except Exception as exc:
        logger.error(f"Fallo cargando el modelo: {exc}")
        _state["error"] = str(exc)
    else:
        logger.info(f"Modelo listo en {time.time() - t0:.1f}s. Escuchando en 127.0.0.1:{PORT}")
    # threaded=False a proposito: la GPU sintetiza de a una peticion por vez,
    # no tiene sentido aceptar requests en paralelo (y evita pisarse el
    # archivo temporal compartido).
    app.run(host="127.0.0.1", port=PORT, threaded=False)
