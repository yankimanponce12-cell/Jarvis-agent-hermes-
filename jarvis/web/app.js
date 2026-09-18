const TOKEN_KEY = "jarvis_token";
const state = { token: localStorage.getItem(TOKEN_KEY) || "" };

const els = {
  app: document.getElementById("app"),
  tokenGate: document.getElementById("tokenGate"),
  tokenInput: document.getElementById("tokenInput"),
  tokenSave: document.getElementById("tokenSave"),
  tokenError: document.getElementById("tokenError"),
  mainUi: document.getElementById("mainUi"),
  micBtn: document.getElementById("micBtn"),
  micBtnSmall: document.getElementById("micBtnSmall"),
  stateLabel: document.getElementById("stateLabel"),
  heardText: document.getElementById("heardText"),
  responseText: document.getElementById("responseText"),
  textForm: document.getElementById("textForm"),
  textInput: document.getElementById("textInput"),
  lobotomizeBtn: document.getElementById("lobotomizeBtn"),
  voiceSettingsBtn: document.getElementById("voiceSettingsBtn"),
  voicePanel: document.getElementById("voicePanel"),
  voicePanelClose: document.getElementById("voicePanelClose"),
  voiceOptionOpenvoice: document.getElementById("voiceOptionOpenvoice"),
  voiceOptionXtts: document.getElementById("voiceOptionXtts"),
  voiceProgress: document.getElementById("voiceProgress"),
  voiceProgressLabel: document.getElementById("voiceProgressLabel"),
  voiceError: document.getElementById("voiceError"),
};

const STATE_LABELS = {
  inactivo: "EN ESPERA",
  escuchando: "ESCUCHANDO",
  pensando: "PROCESANDO",
  hablando: "RESPONDIENDO",
};

function setState(s) {
  els.app.setAttribute("data-state", s);
  els.stateLabel.textContent = STATE_LABELS[s] || s.toUpperCase();
}

function showCaption(el, text) {
  el.classList.remove("show");
  el.textContent = text || "";
  void el.offsetWidth; // fuerza reflow para re-disparar el fade-in
  if (text) el.classList.add("show");
}

async function checkToken(token) {
  try {
    const res = await fetch("/api/health", { headers: { "X-Jarvis-Token": token } });
    return res.ok;
  } catch (err) {
    return false;
  }
}

async function unlock(token) {
  const ok = await checkToken(token);
  if (!ok) {
    els.tokenError.textContent = "Token incorrecto o no se pudo contactar al servidor.";
    return;
  }
  state.token = token;
  localStorage.setItem(TOKEN_KEY, token);
  els.tokenGate.classList.add("hidden");
  els.mainUi.classList.remove("hidden");
  setState("inactivo");
}

els.tokenSave.addEventListener("click", () => {
  const token = els.tokenInput.value.trim();
  if (token) unlock(token);
});

// referencia al audio de Jarvis actualmente sonando, para poder cortarlo si
// el usuario vuelve a tocar el microfono antes de que termine (si no, se
// solaparia con la respuesta siguiente)
let currentAudio = null;

function stopSpeaking() {
  window.speechSynthesis.cancel();
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
}

// Intenta traer el audio con la voz clonada de Jarvis (XTTS, la misma que
// suena por los parlantes de la PC) y reproducirlo. Devuelve true si sono,
// false si algo fallo (servidor caido, XTTS todavia cargando, etc.) para
// que el que llama pueda usar la voz del navegador como respaldo.
async function speakWithJarvisVoice(text) {
  try {
    const res = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jarvis-Token": state.token },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) return false;

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    return await new Promise((resolve) => {
      const audio = new Audio(url);
      currentAudio = audio;
      const cleanup = (result) => {
        if (currentAudio === audio) currentAudio = null;
        URL.revokeObjectURL(url);
        resolve(result);
      };
      audio.onended = () => cleanup(true);
      audio.onerror = () => cleanup(false);
      audio.play().catch(() => cleanup(false));
    });
  } catch (err) {
    return false;
  }
}

function speakBrowser(text, onDone) {
  if (!text || !("speechSynthesis" in window)) {
    if (onDone) onDone();
    return;
  }
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = "es-ES";
  utter.onend = () => onDone && onDone();
  utter.onerror = () => onDone && onDone();
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utter);
}

// Voz de Jarvis (XTTS) primero; si no se pudo (PC apagada del lado del
// modelo, error de red, etc.) se cae a la voz generica del navegador para
// que el celular nunca se quede mudo.
async function speak(text, onDone) {
  if (!text) {
    if (onDone) onDone();
    return;
  }
  const played = await speakWithJarvisVoice(text);
  if (played) {
    if (onDone) onDone();
  } else {
    speakBrowser(text, onDone);
  }
}

async function sendCommand(text) {
  stopSpeaking();
  showCaption(els.heardText, text);
  showCaption(els.responseText, "");
  setState("pensando");
  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jarvis-Token": state.token },
      body: JSON.stringify({ text }),
    });

    if (res.status === 401) {
      // el token dejo de ser valido (p.ej. se borro/regenero
      // server_token.txt en la PC): se vuelve a pedir en vez de reintentar
      // en bucle con un token que ya no sirve
      localStorage.removeItem(TOKEN_KEY);
      els.mainUi.classList.add("hidden");
      els.tokenGate.classList.remove("hidden");
      els.tokenError.textContent = "El token ya no es válido, ingresá el nuevo.";
      return;
    }

    const data = await res.json();
    const respuesta = (data.responses || []).join(" ");
    showCaption(els.responseText, respuesta || "(sin respuesta)");
    setState("hablando");
    speak(respuesta, () => setState("inactivo"));
  } catch (err) {
    showCaption(els.responseText, "No se pudo contactar a Jarvis. ¿Está prendida la PC y en la misma red?");
    setState("inactivo");
  }
}

// --- Reconocimiento de voz (microfono del telefono, via el navegador) ---
// Patron "mantener presionado": al bajar el dedo/click arranca a escuchar y
// el texto se va completando en vivo (resultados interinos); al soltar se
// corta la escucha y se manda lo que se llego a transcribir.
const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;

const MIC_ERROR_MESSAGES = {
  "not-allowed": "Permiso de micrófono denegado: revisá los permisos del sitio en el navegador.",
  "service-not-allowed": "El navegador bloqueó el micrófono. ¿Entraste por https:// (no http://)?",
  "audio-capture": "No se encontró un micrófono en este dispositivo.",
  "network": "Fallo de red al reconocer la voz.",
};

const micButtons = [els.micBtn, els.micBtnSmall].filter(Boolean);

if (SpeechRecognitionCtor) {
  recognizer = new SpeechRecognitionCtor();
  recognizer.lang = "es-ES";
  // continuous:true es la fuente real del bug de "me escribe 3-4 veces lo
  // mismo": el modo continuo de Chrome/Android es conocido por reenviar
  // resultados finales duplicados/corregidos para la misma frase. En vez de
  // eso, cada sesion interna reconoce UNA sola frase (mucho mas confiable y
  // bien soportado) y onend la reinicia sola mientras se siga con el dedo
  // presionado (ver mas abajo) - el efecto final para el usuario es el
  // mismo (no se corta al pausar), pero sin el duplicado.
  recognizer.continuous = false;
  recognizer.interimResults = true;
  recognizer.maxAlternatives = 1;

  // finalTranscript se reconstruye ENTERO desde event.results en cada
  // evento (no se va concatenando de a poco con event.resultIndex): en
  // Chrome/Android ese indice no siempre avanza como deberia, y acumular de
  // a poco terminaba repitiendo el mismo tramo ya finalizado varias veces.
  // sessionBase guarda lo ya finalizado de una sesion interna anterior,
  // para no perderlo si el navegador corta y se reinicia solo (ver onend)
  // mientras el usuario sigue con el dedo presionado.
  let finalTranscript = "";
  let sessionBase = "";
  let holding = false;
  let released = false;

  recognizer.onstart = () => setState("escuchando");

  recognizer.onresult = (event) => {
    let finalText = "";
    let interim = "";
    for (let i = 0; i < event.results.length; i++) {
      const chunk = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        finalText += (finalText ? " " : "") + chunk.trim();
      } else {
        interim += chunk;
      }
    }
    finalTranscript = (sessionBase + " " + finalText).trim();
    showCaption(els.heardText, (finalTranscript + " " + interim).trim());
  };

  recognizer.onerror = (event) => {
    // "aborted" lo dispara nuestro propio stop() al soltar el boton, y
    // "no-speech" es normal y frecuente ahora que cada frase es su propia
    // sub-sesion (nadie hablo todavia cuando esa sub-sesion hizo timeout):
    // ninguno de los dos es un error real. No se corta "holding" para que
    // onend reinicie la escucha sola si el usuario sigue con el dedo
    // presionado (ver onend mas abajo).
    if (event.error === "aborted" || event.error === "no-speech") return;
    holding = false;
    setState("inactivo");
    showCaption(els.responseText, MIC_ERROR_MESSAGES[event.error] || `No se pudo usar el micrófono (${event.error}).`);
  };

  recognizer.onend = () => {
    // solo se manda el comando cuando el usuario ya solto el boton: si esta
    // sub-sesion termina por su cuenta (fin de frase o silencio) mientras
    // todavia esta presionado, se reinicia para seguir escuchando la
    // siguiente frase. El pequeño delay evita "InvalidStateError" en
    // algunos navegadores que no liberan la sesion anterior al instante.
    if (!released) {
      if (holding) {
        sessionBase = finalTranscript; // no perder lo ya dicho antes del reinicio
        setTimeout(() => {
          if (holding) {
            try { recognizer.start(); } catch (err) { /* ya arrancando */ }
          }
        }, 50);
      }
      return;
    }
    if (els.app.getAttribute("data-state") === "escuchando") setState("inactivo");
    const text = finalTranscript.trim();
    finalTranscript = "";
    sessionBase = "";
    if (text) sendCommand(text);
  };

  const startHold = (event) => {
    event.preventDefault();
    if (holding) return;
    holding = true;
    released = false;
    finalTranscript = "";
    sessionBase = "";
    stopSpeaking();
    showCaption(els.heardText, "");
    showCaption(els.responseText, "");
    try {
      recognizer.start();
    } catch (err) {
      // ya habia una sesion arrancando (doble evento), se ignora
    }
  };

  const endHold = () => {
    if (!holding) return;
    holding = false;
    released = true;
    try {
      recognizer.stop();
    } catch (err) {
      // toque muy breve, la sesion todavia no habia arrancado del todo
    }
  };

  micButtons.forEach((btn) => {
    btn.addEventListener("pointerdown", startHold);
    btn.addEventListener("pointerup", endHold);
    btn.addEventListener("pointercancel", endHold);
    btn.addEventListener("pointerleave", endHold);
  });
} else {
  micButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      showCaption(els.responseText, "Este navegador no soporta reconocimiento de voz, usá el texto de abajo.");
    });
  });
}

els.textForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.textInput.value.trim();
  if (!text) return;
  els.textInput.value = "";
  sendCommand(text);
});

// reinicia la memoria de conversacion con la IA (comparte el mismo Brain
// que la voz/texto de la PC, es un solo proceso: ver /api/reset en
// server.py), para poder cambiar de tema sin que Jarvis arrastre contexto
// de lo que se hablo antes
els.lobotomizeBtn.addEventListener("click", async () => {
  stopSpeaking();
  setState("pensando");
  try {
    const res = await fetch("/api/reset", {
      method: "POST",
      headers: { "X-Jarvis-Token": state.token },
    });

    if (res.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      els.mainUi.classList.add("hidden");
      els.tokenGate.classList.remove("hidden");
      els.tokenError.textContent = "El token ya no es válido, ingresá el nuevo.";
      return;
    }

    const msg = res.ok
      ? "Memoria reiniciada, señor. Empecemos de cero."
      : "No pude reiniciar la memoria.";
    showCaption(els.heardText, "");
    showCaption(els.responseText, msg);
    setState("hablando");
    speak(msg, () => setState("inactivo"));
  } catch (err) {
    showCaption(els.responseText, "No se pudo contactar a Jarvis.");
    setState("inactivo");
  }
});

// --- Configuracion de voz: elegir entre XTTS (clasica) y OpenVoice (rapida) ---
// El backend nunca tiene los dos motores cargados a la vez a proposito (ver
// jarvis/voice_output.py): cambiar de voz dispara la carga en un hilo de
// fondo y esta pantalla sondea /api/voice-engine cada segundo para saber
// cuando termino, mostrando una barra de progreso mientras tanto (no hay
// forma de saber el porcentaje real, asi que es indeterminada a proposito).
const voiceOptionButtons = [els.voiceOptionOpenvoice, els.voiceOptionXtts].filter(Boolean);
let voicePollTimer = null;

function renderVoiceStatus(status) {
  const { current, configured, loading, error } = status;
  voiceOptionButtons.forEach((btn) => {
    const engine = btn.dataset.engine;
    btn.classList.toggle("active", !loading && current === engine);
    btn.classList.toggle("loading", Boolean(loading));
  });

  if (loading) {
    els.voiceProgress.classList.remove("hidden");
    const label = loading === "xtts" ? "XTTS" : "OpenVoice";
    els.voiceProgressLabel.textContent = `Cargando ${label}...`;
  } else {
    els.voiceProgress.classList.add("hidden");
  }

  els.voiceError.textContent = !loading && error ? `No se pudo cargar: ${error}` : "";

  if (loading) {
    if (!voicePollTimer) voicePollTimer = setInterval(pollVoiceStatus, 1000);
  } else if (voicePollTimer) {
    clearInterval(voicePollTimer);
    voicePollTimer = null;
  }
}

async function fetchVoiceStatus() {
  const res = await fetch("/api/voice-engine", { headers: { "X-Jarvis-Token": state.token } });
  if (!res.ok) throw new Error("no se pudo consultar el estado de la voz");
  return res.json();
}

async function pollVoiceStatus() {
  try {
    renderVoiceStatus(await fetchVoiceStatus());
  } catch (err) {
    // red caida momentaneamente: se reintenta solo en el proximo tick
  }
}

async function openVoicePanel() {
  els.voicePanel.classList.remove("hidden");
  try {
    renderVoiceStatus(await fetchVoiceStatus());
  } catch (err) {
    els.voiceError.textContent = "No se pudo contactar a Jarvis.";
  }
}

els.voiceSettingsBtn.addEventListener("click", openVoicePanel);
els.voicePanelClose.addEventListener("click", () => els.voicePanel.classList.add("hidden"));

voiceOptionButtons.forEach((btn) => {
  btn.addEventListener("click", async () => {
    if (btn.classList.contains("active") || btn.classList.contains("loading")) return;
    try {
      const res = await fetch("/api/voice-engine", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Jarvis-Token": state.token },
        body: JSON.stringify({ engine: btn.dataset.engine }),
      });
      if (!res.ok) throw new Error("no se pudo cambiar la voz");
      renderVoiceStatus(await fetchVoiceStatus());
    } catch (err) {
      els.voiceError.textContent = "No se pudo cambiar la voz.";
    }
  });
});

// se reconecta solo si ya habia un token guardado de una sesion anterior;
// si no, y la URL trae ?token=... (el link que imprime el servidor al
// arrancar), se usa para no tener que copiarlo a mano la primera vez
if (state.token) {
  unlock(state.token);
} else {
  const urlToken = new URLSearchParams(location.search).get("token");
  if (urlToken) {
    els.tokenInput.value = urlToken;
    unlock(urlToken);
  }
}
