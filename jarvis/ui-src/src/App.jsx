import { useEffect, useState } from "react";
import { listeners } from "./bridge.js";

const STATE_LABELS = {
  cargando: "INICIALIZANDO",
  inactivo: "EN ESPERA",
  escuchando: "ESCUCHANDO",
  pensando: "PROCESANDO",
  hablando: "RESPONDIENDO",
};

const DIAS = ["domingo", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado"];
const MESES = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

const WAVE_BARS = Array.from({ length: 28 }, (_, i) => i);

// Los botones (y el campo de texto) llaman a jarvis.main.Api (ver hud.py);
// pywebview inyecta window.pywebview tras "pywebviewready", asi que se
// chequea que exista por si el usuario alcanza a interactuar antes de que
// la ventana termine de inicializarse.
function callApi(method, ...args) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api[method]) {
    window.pywebview.api[method](...args);
  }
}

// Version que espera el resultado (Promise) en vez de "dispara y olvida":
// la usan get_voice_status/set_voice_engine, que si devuelven algo (ver
// main.py, clase Api).
function callApiAsync(method, ...args) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api[method]) {
    return window.pywebview.api[method](...args);
  }
  return Promise.resolve(null);
}

function formatDate(d) {
  return `${DIAS[d.getDay()]} ${d.getDate()} de ${MESES[d.getMonth()]}`;
}

function formatTime(d) {
  return d.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function App() {
  const [state, setState] = useState("cargando");
  const [heard, setHeard] = useState("");
  const [heardAt, setHeardAt] = useState(null);
  const [response, setResponse] = useState("");
  const [responseAt, setResponseAt] = useState(null);
  const [weather, setWeather] = useState(null); // {temp, desc} | null hasta que llegue el primer dato real
  const [qr, setQr] = useState({ visible: false, dataUri: "", token: "" });
  const [maximized, setMaximized] = useState(false);
  const [command, setCommand] = useState("");
  const [now, setNow] = useState(() => new Date());
  const [voicePanelOpen, setVoicePanelOpen] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState({ current: null, loading: null, error: null });

  useEffect(() => {
    const handler = (event) => {
      switch (event.type) {
        case "state":
          setState(event.value);
          break;
        case "heard":
          setHeard(event.value);
          setHeardAt(new Date());
          break;
        case "response":
          setResponse(event.value);
          setResponseAt(new Date());
          break;
        case "weather":
          setWeather({ temp: event.temp, desc: event.desc });
          break;
        case "showQr":
          setQr({ visible: true, dataUri: event.dataUri, token: event.token || "" });
          break;
        case "hideQr":
          setQr((q) => ({ ...q, visible: false }));
          break;
        default:
          break;
      }
    };
    listeners.add(handler);
    return () => listeners.delete(handler);
  }, []);

  // reloj real de la PC, tick cada segundo (no depende de nada de Python)
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  function submitCommand(e) {
    e.preventDefault();
    const text = command.trim();
    if (!text) return;
    setCommand("");
    callApi("send_text", text);
  }

  function toggleMaximize() {
    callApi("toggle_maximize");
    setMaximized((m) => !m);
  }

  function askForQr() {
    callApi("send_text", "mandame el qr");
  }

  function askForDemo() {
    callApi("send_text", "hazme una demo");
  }

  // El backend nunca tiene los dos motores de voz cargados a la vez a
  // proposito (ver jarvis/voice_output.py): cambiar de voz dispara la
  // carga en un hilo de fondo y este panel sondea get_voice_status() cada
  // segundo mientras esta abierto, para saber cuando termino y mostrar una
  // barra de progreso mientras tanto (no hay forma de saber el porcentaje
  // real, asi que es indeterminada a proposito).
  async function refreshVoiceStatus() {
    const status = await callApiAsync("get_voice_status");
    if (status) setVoiceStatus(status);
  }

  useEffect(() => {
    if (!voicePanelOpen) return;
    refreshVoiceStatus();
    const id = setInterval(refreshVoiceStatus, 1000);
    return () => clearInterval(id);
  }, [voicePanelOpen]);

  function selectVoiceEngine(engine) {
    if (voiceStatus.loading || voiceStatus.current === engine) return;
    callApi("set_voice_engine", engine);
    setVoiceStatus((s) => ({ ...s, loading: engine }));
  }

  const stateLabel = STATE_LABELS[state] || state.toUpperCase();

  return (
    <div id="hud" data-state={state}>
      <div className="bg-grid" />
      <div className="scan-overlay" />

      {/* ---------- barra superior ---------- */}
      <div className="topbar">
        <div className="brand-block">
          <span className="brand-mark" />
          <div className="brand-text">
            <span className="brand-title">JARVIS</span>
            <span className="brand-subtitle">Asistente local</span>
          </div>
        </div>

        <div className="status-badge">
          <span className="status-dot" />
          {stateLabel}
        </div>

        <div className="clock-block">
          <span className="clock-date">{formatDate(now)}</span>
          <span className="clock-time">{formatTime(now)}</span>
        </div>

        <div className="win-controls">
          <button
            className="win-btn win-btn-lobotomize"
            title="Lobotomizar (reiniciar memoria)"
            aria-label="Lobotomizar"
            onClick={() => callApi("lobotomizar")}
          >
            &#9711;
          </button>
          <button
            className="win-btn win-btn-voice"
            title="Configurar voz"
            aria-label="Configurar voz"
            onClick={() => setVoicePanelOpen(true)}
          >
            &#9881;
          </button>
          <button className="win-btn" title="Minimizar" aria-label="Minimizar" onClick={() => callApi("minimize")}>
            &#9472;
          </button>
          <button className="win-btn" title="Maximizar" aria-label="Maximizar" onClick={toggleMaximize}>
            {maximized ? "❑" : "□"}
          </button>
          <button className="win-btn win-btn-close" title="Cerrar" aria-label="Cerrar" onClick={() => callApi("close")}>
            &#10005;
          </button>
        </div>
      </div>

      {/* ---------- cuerpo: panel de voz | nucleo central | actividad ---------- */}
      <div className="dashboard">
        <div className="panel panel-voice">
          <div className="panel-title">ESTADO DE VOZ</div>
          <div className="waveform">
            {WAVE_BARS.map((i) => (
              <span key={i} className="wave-bar" style={{ "--i": i }} />
            ))}
          </div>
          <div className="voice-state-label">{stateLabel}</div>
        </div>

        <div className="core-stage">
          <div className="core-sphere-wrap">
            <div className="sphere-glow" />
            <div className="core-sphere">
              <div className="sphere-ring sphere-ring-1" />
              <div className="sphere-ring sphere-ring-2" />
              <div className="sphere-ring sphere-ring-3" />
              <div className="sphere-ring sphere-ring-4" />
            </div>
            <div className="core-wordmark">JARVIS</div>
            <div className="core-sub">NUCLEO IA</div>
          </div>
        </div>

        <div className="panel panel-feed">
          <div className="panel-title">ACTIVIDAD</div>
          <div className="feed-item feed-heard">
            <span className="feed-tag">USTED DIJO</span>
            <span className="feed-text">{heard ? `“${heard}”` : "—"}</span>
            {heardAt && <span className="feed-time">{formatTime(heardAt)}</span>}
          </div>
          <div className="feed-item feed-response">
            <span className="feed-tag">JARVIS RESPONDIO</span>
            <span className="feed-text">{response || "—"}</span>
            {responseAt && <span className="feed-time">{formatTime(responseAt)}</span>}
          </div>
        </div>
      </div>

      {/* ---------- barra inferior ---------- */}
      <div className="bottombar">
        <div className="info-chips">
          {weather ? (
            <span className="info-chip">CLIMA · {weather.temp}&deg;C · {weather.desc}</span>
          ) : (
            <span className="info-chip info-chip-dim">CLIMA · --</span>
          )}
        </div>

        <form className="console-form" onSubmit={submitCommand}>
          <span className="prompt">&gt;</span>
          <input
            type="text"
            placeholder="Ingresar comando..."
            autoComplete="off"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
          />
        </form>

        <div className="quick-actions">
          <button type="button" className="quick-btn" onClick={askForDemo}>
            DEMO
          </button>
          <button type="button" className="quick-btn" onClick={askForQr}>
            QR
          </button>
          <button type="button" className="quick-btn" onClick={() => callApi("lobotomizar")}>
            RESET
          </button>
        </div>
      </div>

      {voicePanelOpen && (
        <div className="voice-overlay" onClick={() => setVoicePanelOpen(false)}>
          <div className="voice-panel" onClick={(e) => e.stopPropagation()}>
            <div className="voice-panel-title">MOTOR DE VOZ</div>
            {["openvoice", "xtts"].map((engine) => (
              <button
                key={engine}
                type="button"
                className={
                  "voice-option" +
                  (!voiceStatus.loading && voiceStatus.current === engine ? " active" : "") +
                  (voiceStatus.loading ? " loading" : "")
                }
                onClick={() => selectVoiceEngine(engine)}
              >
                <span>{engine === "openvoice" ? "OpenVoice" : "XTTS"}</span>
                <span className="voice-option-tag">{engine === "openvoice" ? "rápida" : "clásica"}</span>
              </button>
            ))}
            {voiceStatus.loading && (
              <div className="voice-progress">
                <div className="voice-progress-track">
                  <div className="voice-progress-bar" />
                </div>
                <span>Cargando {voiceStatus.loading === "xtts" ? "XTTS" : "OpenVoice"}...</span>
              </div>
            )}
            {!voiceStatus.loading && voiceStatus.error && (
              <div className="voice-error">No se pudo cargar: {voiceStatus.error}</div>
            )}
            <button type="button" className="voice-panel-close" onClick={() => setVoicePanelOpen(false)}>
              Cerrar
            </button>
          </div>
        </div>
      )}

      {qr.visible && (
        <div className="qr-overlay" onClick={() => setQr((q) => ({ ...q, visible: false }))}>
          <div className="qr-panel">
            <img id="qrImage" src={qr.dataUri} alt="Codigo QR" />
            <div className="qr-token">{qr.token}</div>
            <div className="qr-caption">
              Escaneá con la cámara o tipeá el PIN
              <br />
              (misma red wifi)
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
