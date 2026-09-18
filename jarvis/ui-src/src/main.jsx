import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { emit } from "./bridge.js";
import "./index.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// Estas son las funciones que hud.py realmente llama (window.evaluate_js
// con literales como setState("hablando")): tienen que existir como
// globales de window con estos nombres exactos, sin importar que adentro
// el render lo haga React.
window.setState = (state) => emit({ type: "state", value: state });
window.setHeard = (text) => emit({ type: "heard", value: text });
window.setResponse = (text) => emit({ type: "response", value: text });
window.showQr = (dataUri, token) => emit({ type: "showQr", dataUri, token });
window.hideQr = () => emit({ type: "hideQr" });
window.setWeather = (temp, desc) => emit({ type: "weather", temp, desc });
