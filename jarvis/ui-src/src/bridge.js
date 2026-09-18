// Puente entre hud.py (que le habla a la pagina via window.evaluate_js,
// llamando funciones globales como setState("...")) y el estado de React.
// hud.py no sabe que esto es React por dentro: solo necesita que existan
// window.setState/setHeard/setResponse/showQr/hideQr como funciones
// globales (ver main.jsx), asi que ese contrato no cambia aunque adentro
// ahora dispare un evento en vez de tocar el DOM directamente.
export const listeners = new Set();

export function emit(event) {
  listeners.forEach((fn) => fn(event));
}
