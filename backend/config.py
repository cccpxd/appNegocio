"""Configuración de la aplicación.

Regla de la casa: nada de variables globales con secretos.
Los ajustes se leen del entorno cada vez que hacen falta y se pasan
como dependencia (`Depends`) a las rutas.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Raíz del proyecto. Sirve para encontrar el .env y la carpeta public/.
RAIZ_PROYECTO = Path(__file__).resolve().parents[1]

# Único modelo permitido. No se cambia por variable de entorno a propósito.
MODELO_GEMINI = "gemini-3.8-flash"

# ~4.5 MB es el límite del cuerpo en Vercel. Dejamos margen.
MAX_BYTES_SUBIDA = 4_000_000

# La foto se reescala en el backend si el navegador no la dejó suficientemente chica.
LADO_MAXIMO_PX = 1600
CALIDAD_JPEG = 75

# El worker hace UN intento por corrida. Si la IA falla, la factura vuelve a la
# cola y la siguiente corrida lo reintenta. No dormimos dentro de la función.
# 'standard' responde en 5-15 s; el corte es holgado por si se congestiona.
TIEMPOS_IA_MS = {"standard": 60_000, "flex": 120_000}

# 'standard': rápido y predecible. Es el modo de esta app.
# 'flex': cuesta la mitad pero puede tardar 1-2 minutos.
SERVICIO_IA_POR_DEFECTO = "standard"

# Después de N fallos, la factura se marca 'error' y se suelta la imagen.
MAX_INTENTOS = 5

# Una factura en 'procesando' por más de esto se considera colgada
# (la función de Vercel murió a mitad) y se puede volver a tomar.
SEGUNDOS_COLGADA = 180


class ErrorConfiguracion(RuntimeError):
    """Falta una variable de entorno obligatoria."""


@dataclass(frozen=True)
class Ajustes:
    """Todo lo que la app necesita saber del entorno."""

    google_api_key: str
    modelo: str
    base_datos_url: str | None
    max_bytes_subida: int
    lado_maximo_px: int
    calidad_jpeg: int
    tiempo_maximo_gemini_ms: int
    servicio_ia: str
    max_intentos: int
    segundos_colgada: int
    cron_secreto: str | None


def cargar_ajustes() -> Ajustes:
    """Lee el entorno y devuelve los ajustes. Falla rápido si falta la API key."""
    _cargar_env_local()

    api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise ErrorConfiguracion(
            "Falta GOOGLE_API_KEY. Ponla en el archivo .env (local) "
            "o en las variables de entorno del proyecto en Vercel."
        )

    base_datos = (os.getenv("DATABASE_URL") or "").strip() or None

    # Vercel manda el secreto del cron en la cabecera Authorization si la
    # variable se llama CRON_SECRET. Aceptamos también CRON_SECRETO.
    cron_secreto = (
        (os.getenv("CRON_SECRET") or "").strip()
        or (os.getenv("CRON_SECRETO") or "").strip()
        or None
    )

    servicio = (os.getenv("SERVICIO_IA") or SERVICIO_IA_POR_DEFECTO).strip().lower()
    if servicio not in {"flex", "standard"}:
        servicio = SERVICIO_IA_POR_DEFECTO

    tiempo_ia = _entero_del_entorno("TIEMPO_IA_MS", TIEMPOS_IA_MS[servicio])

    return Ajustes(
        google_api_key=api_key,
        modelo=MODELO_GEMINI,
        base_datos_url=base_datos,
        max_bytes_subida=MAX_BYTES_SUBIDA,
        lado_maximo_px=LADO_MAXIMO_PX,
        calidad_jpeg=CALIDAD_JPEG,
        tiempo_maximo_gemini_ms=tiempo_ia,
        servicio_ia=servicio,
        max_intentos=MAX_INTENTOS,
        segundos_colgada=SEGUNDOS_COLGADA,
        cron_secreto=cron_secreto,
    )


def _entero_del_entorno(nombre: str, por_defecto: int) -> int:
    crudo = (os.getenv(nombre) or "").strip()
    if not crudo:
        return por_defecto
    try:
        return int(crudo)
    except ValueError:
        return por_defecto


def _cargar_env_local() -> None:
    """Carga el .env solo si existe. En Vercel no existe y no pasa nada."""
    archivo = RAIZ_PROYECTO / ".env"
    if archivo.is_file():
        load_dotenv(archivo, override=False)
