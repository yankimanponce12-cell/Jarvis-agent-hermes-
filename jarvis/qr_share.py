import base64
import io

import qrcode

from jarvis import server as jarvis_server


def mobile_qr_data_uri() -> str:
    """Genera el QR de la pagina movil de Jarvis como data URI base64, listo
    para inyectar en un <img> del HUD (ver Hud.show_qr). El link ya incluye
    el token de acceso, asi que escanearlo alcanza sin copiar nada a mano en
    el telefono."""
    img = qrcode.make(jarvis_server.mobile_url(), box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
