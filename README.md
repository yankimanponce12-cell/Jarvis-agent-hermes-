# Jarvis

Asistente de voz en español para Windows, inspirado en el JARVIS de Iron Man.
Escucha una palabra de activación, entiende comandos (por reglas fijas o con un
modelo local con tool-calling) y responde con una voz clonada. Todo el
procesamiento de lenguaje corre en local.

## Qué hace

- Wake word ("Jarvis, ...") o modo push-to-talk (`--push-to-talk`).
- Comandos: WhatsApp Web, YouTube, búsquedas en Google, abrir/cerrar apps,
  volumen, hora/fecha, clima, memoria a largo plazo (nombre, notas).
- Charla libre y acciones ambiguas vía agente local (Ollama).
- HUD flotante (pywebview + React) y panel web para controlarlo desde el
  celular por HTTPS en la misma red.
- Avisos proactivos (aviso de lluvia) y opcionalmente un reloj ESP32-S3 que
  muestra el estado de Jarvis (`jarvis/watch.py`).

## Arquitectura

```
listener.py (Whisper) -> wakeword.py -> commands.py (regex, instantáneo)
                                             |  si no coincide
                                             v
                                        agent.py (Qwen2.5:7b, tool-calling)
brain.py (llama3.2:3b): charla simple y clasificación sí/no
memory.py: memoria a largo plazo (SQLite)
voice_output.py: XTTS v2 u OpenVoice V2 (intercambiables)
server.py: API Flask HTTPS + web móvil (jarvis/web)
hud.py + ui-src/: ventana HUD
```

## Requisitos

- Windows 10/11, Python 3.10+, GPU NVIDIA con CUDA recomendada.
- [Ollama](https://ollama.com) con los modelos `qwen2.5:7b` y `llama3.2:3b`.
- Node.js (solo para compilar el HUD).
- Navegador Brave (para WhatsApp Web vía Selenium).
- Un audio de referencia de **tu propia voz** (o de una voz que tengas derecho
  a usar), de ~10-30 s, en `samples/jarvis_reference.wav`. No se incluye en
  el repo.

## Instalación

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

ollama pull qwen2.5:7b
ollama pull llama3.2:3b

# compilar el HUD (genera jarvis/ui/)
cd jarvis\ui-src
npm install
npm run build
cd ..\..
```

Configuración local opcional (ubicación de respaldo para el clima):

```powershell
copy local_config.example.json local_config.json   # y edita tus valores
```

### Motor de voz OpenVoice (opcional)

Es ~4x más rápido que XTTS pero se parece un poco menos a la voz de referencia.
Corre como proceso aparte (`openvoice_server/serve.py`) con su propio venv,
porque sus dependencias chocan con las de XTTS. XTTS funciona sin esto.

```powershell
cd openvoice_server
git clone https://github.com/myshell-ai/OpenVoice.git
cd OpenVoice

# usa Python 3.10 para este venv (los pines viejos no tienen wheels en versiones nuevas)
C:\ruta\a\Python310\python.exe -m venv venv

# antes de instalar, en setup.py cambia 'faster-whisper==0.9.0' por 'faster-whisper>=1.0'
venv\Scripts\pip install -e .
venv\Scripts\pip install "setuptools<81"          # librosa==0.9.1 necesita pkg_resources

# pip trae torch solo-CPU; fuerza la build con CUDA (ajusta cu130 a tu versión)
venv\Scripts\pip install -U torch torchaudio --index-url https://download.pytorch.org/whl/cu130

venv\Scripts\pip install git+https://github.com/myshell-ai/MeloTTS.git
venv\Scripts\python -m unidic download

# el .zip de la doc oficial da 404: usa el mirror de Hugging Face
venv\Scripts\pip install "huggingface_hub[cli]"
venv\Scripts\hf download myshell-ai/OpenVoiceV2 --local-dir checkpoints_v2

# autoriza UNA vez el VAD de silero (pide confirmación interactiva la primera vez)
venv\Scripts\python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True, onnx=False)"
```

Jarvis arranca `serve.py` solo, bajo demanda, cuando eliges esta voz. Para
probarlo a mano: `openvoice_server\OpenVoice\venv\Scripts\python.exe openvoice_server\serve.py`
(escucha en `127.0.0.1:8766`).

## Uso

```powershell
python -m jarvis.main                  # wake word
python -m jarvis.main --push-to-talk   # Enter para hablar
.\run_debug.ps1                        # con log en logs\run_debug.log
```

Al arrancar, la consola imprime la URL del panel móvil. Dile "Jarvis, mándame
el QR" para escanearla desde el HUD. El servidor genera en el primer arranque
su certificado autofirmado y un PIN de 4 dígitos (`server_cert.pem`,
`server_key.pem`, `server_token.txt`); estos archivos son locales y están en
`.gitignore`.

`launcher/Launcher.cs` es el código de un `Jarvis.exe` que arranca el venv sin
consola; compílalo con `csc` si lo quieres.

## Archivos que NO se suben (ver `.gitignore`)

Credenciales (`secrets/`, `*.pem`, `server_token.txt`), memoria y logs
(`jarvis_memory.db`, `logs/`), perfil de WhatsApp (`whatsapp_profile/`),
audio de referencia (`samples/`), configuración local, binarios y modelos.

## Licencias y avisos

- XTTS v2 se distribuye bajo la licencia CPML de Coqui: **uso no comercial**.
- OpenVoice (MyShell) y sus checkpoints tienen su propia licencia; no se
  incluyen aquí.
- Este proyecto es un experimento personal y no está afiliado a Marvel ni a
  Disney.
- Ten cuidado con el PIN y el servidor: solo debe usarse en redes de
  confianza.
