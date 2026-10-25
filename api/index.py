"""Rutas HTTP del lector de facturas.

Este archivo solo conecta cosas: recibe, delega y responde.
La lógica está en backend/servicio.py.

Flujo:
1. POST /procesar        → comprime, encola y devuelve un id YA. Sin IA.
2. POST /facturas/{id}/procesar → el worker intenta leerla (Flex).
3. GET  /facturas/{id}   → polling del navegador.
4. POST /cron/pendientes → el cron recoge lo que quedó colgado.

Vercel importa este archivo como módulo suelto, así que la raíz del proyecto
tiene que estar en el path para poder importar `backend`.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path
from typing import Annotated

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from fastapi import FastAPI, File, Header, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from starlette.requests import Request  # noqa: E402

from backend.config import Ajustes, ErrorConfiguracion  # noqa: E402
from backend.dependencias import AjustesDep, ClienteDep, RepositorioDep  # noqa: E402
from backend.ia import ErrorGemini  # noqa: E402
from backend.imagen import ImagenInvalida  # noqa: E402
from backend.modelos import (  # noqa: E402
    EstadoFactura,
    FacturaParaGuardar,
    ResultadoEncolado,
)
from backend.repositorio import BaseDeDatosNoConfigurada  # noqa: E402
from backend.servicio import (  # noqa: E402
    encolar_factura,
    guardar_correcciones,
    obtener_estado,
    procesar_una,
)

PAGINA = RAIZ / "public" / "index.html"

app = FastAPI(
    title="Lector de facturas",
    description="Foto de factura colombiana → JSON ordenado, con Gemini.",
    version="0.2.0",
)


# --------------------------------------------------------------------------
# Errores con mensaje en español
# --------------------------------------------------------------------------


@app.exception_handler(ErrorConfiguracion)
def _falta_configuracion(request: Request, error: ErrorConfiguracion) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(error)})


@app.exception_handler(BaseDeDatosNoConfigurada)
def _falta_base_datos(
    request: Request, error: BaseDeDatosNoConfigurada
) -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": str(error)})


# --------------------------------------------------------------------------
# Interfaz
# --------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def portada() -> FileResponse:
    if not PAGINA.is_file():
        raise HTTPException(status_code=500, detail="No encuentro public/index.html")
    return FileResponse(
        PAGINA,
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/salud")
def salud(ajustes: AjustesDep) -> dict:
    """Sirve para saber si el despliegue quedó bien configurado."""
    return {
        "estado": "ok",
        "modelo": ajustes.modelo,
        "servicio_ia": ajustes.servicio_ia,
        "base_datos": "conectada" if ajustes.base_datos_url else "sin configurar",
        "cron": "configurado" if ajustes.cron_secreto else "sin secreto",
    }


# --------------------------------------------------------------------------
# Recepción rápida
# --------------------------------------------------------------------------


@app.post("/procesar", response_model=ResultadoEncolado)
def procesar(
    archivo: Annotated[UploadFile, File(description="Foto de la factura")],
    ajustes: AjustesDep,
    repositorio: RepositorioDep,
) -> ResultadoEncolado:
    """Recibe la foto, la deja en la cola y responde al instante.

    Aquí NO se llama a Gemini. El navegador hace polling mientras el worker
    (o el cron) la procesa.
    """
    if archivo.content_type and not archivo.content_type.startswith("image/"):
        raise HTTPException(
            status_code=415, detail="El archivo tiene que ser una imagen."
        )

    # Leemos un byte de más para detectar si se pasa del límite sin cargar todo.
    contenido = archivo.file.read(ajustes.max_bytes_subida + 1)
    if len(contenido) > ajustes.max_bytes_subida:
        limite_mb = ajustes.max_bytes_subida / 1_000_000
        raise HTTPException(
            status_code=413,
            detail=(
                f"La imagen pesa más de {limite_mb:.0f} MB. "
                "La compresión del navegador debió dejarla más chica; "
                "prueba con otra foto."
            ),
        )

    try:
        return encolar_factura(
            contenido,
            ajustes=ajustes,
            repositorio=repositorio,
        )
    except ImagenInvalida as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


# --------------------------------------------------------------------------
# Worker (lo llama el navegador mientras espera, y el cron)
# --------------------------------------------------------------------------


@app.post("/facturas/{factura_id}/procesar", response_model=EstadoFactura)
def procesar_factura(
    factura_id: int,
    ajustes: AjustesDep,
    cliente: ClienteDep,
    repositorio: RepositorioDep,
) -> EstadoFactura:
    """Intenta procesar ESA factura. Si otro ya la tomó, devuelve su estado."""
    try:
        estado = procesar_una(
            ajustes=ajustes,
            cliente=cliente,
            repositorio=repositorio,
            factura_id=factura_id,
        )
    except ErrorGemini as error:
        # El servicio ya clasifica los errores; esto es una red de seguridad.
        raise HTTPException(status_code=502, detail=str(error)) from error

    if estado is None:
        raise HTTPException(status_code=404, detail="Esa factura no existe.")
    return estado


@app.get("/facturas/{factura_id}", response_model=EstadoFactura)
def consultar_factura(factura_id: int, repositorio: RepositorioDep) -> EstadoFactura:
    """Polling: en qué va la factura."""
    estado = obtener_estado(repositorio, factura_id)
    if estado is None:
        raise HTTPException(status_code=404, detail="Esa factura no existe.")
    return estado


@app.api_route("/cron/pendientes", methods=["GET", "POST"])
def cron_pendientes(
    ajustes: AjustesDep,
    cliente: ClienteDep,
    repositorio: RepositorioDep,
    limite: int = 5,
    authorization: Annotated[str | None, Header()] = None,
    x_cron_secreto: Annotated[str | None, Header()] = None,
) -> dict:
    """Recoge lo que quedó en la cola. Lo llama un cron externo.

    Vercel manda `Authorization: Bearer <CRON_SECRET>` si configuras esa
    variable. También aceptamos `x-cron-secreto` para cualquier otro cron.
    """
    _exigir_secreto(ajustes, authorization, x_cron_secreto)

    hechas: list[EstadoFactura] = []
    vistos: list[int] = []
    for _ in range(max(1, min(limite, 10))):
        estado = procesar_una(
            ajustes=ajustes,
            cliente=cliente,
            repositorio=repositorio,
            excluir=vistos,
        )
        if estado is None:
            break
        # Si falló y volvió a la cola, no la reintentamos en esta misma corrida:
        # se le quemarían todos los intentos de una sola vez.
        vistos.append(estado.id)
        hechas.append(estado)

    return {
        "procesadas": len(hechas),
        "resultados": [estado.model_dump(mode="json") for estado in hechas],
    }


# --------------------------------------------------------------------------
# Correcciones del usuario
# --------------------------------------------------------------------------


@app.post("/guardar", response_model=EstadoFactura)
def guardar(
    factura: FacturaParaGuardar,
    repositorio: RepositorioDep,
) -> EstadoFactura:
    """Guarda lo que el usuario corrigió en la pantalla de revisión."""
    return guardar_correcciones(factura, repositorio=repositorio)


# --------------------------------------------------------------------------
# Ayudas
# --------------------------------------------------------------------------


def _exigir_secreto(
    ajustes: Ajustes,
    authorization: str | None,
    x_cron_secreto: str | None,
) -> None:
    """Sin secreto configurado, el endpoint no existe para nadie."""
    if not ajustes.cron_secreto:
        raise HTTPException(
            status_code=503,
            detail="Falta CRON_SECRET en las variables de entorno.",
        )

    enviado = ""
    if authorization:
        enviado = authorization.removeprefix("Bearer ").strip()
    elif x_cron_secreto:
        enviado = x_cron_secreto.strip()

    if not enviado or not secrets.compare_digest(enviado, ajustes.cron_secreto):
        raise HTTPException(status_code=401, detail="Secreto de cron inválido.")
