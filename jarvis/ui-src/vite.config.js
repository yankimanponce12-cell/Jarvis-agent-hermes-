import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base:'./' es necesario porque pywebview carga index.html desde disco
// (file://), no desde un servidor con raiz "/": con rutas absolutas los
// assets no cargarian. El build compila directo a jarvis/ui/, la misma
// carpeta que hud.py ya apunta (UI_DIR / "index.html"), asi que no hace
// falta tocar nada de Python.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../ui",
    emptyOutDir: true,
  },
});
