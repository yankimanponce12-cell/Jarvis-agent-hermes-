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

Motor de voz OpenVoice (opcional, más rápido que XTTS): sigue el setup descrito
en `benchmarks/openvoice/bench.py` (clona OpenVoice en
`benchmarks/openvoice/OpenVoice` con su propio venv). XTTS funciona sin eso.

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
