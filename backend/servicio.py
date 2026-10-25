"""Orquesta el trabajo: encolar, procesar y leer el estado.

Las rutas de FastAPI quedan delgadas. Toda la lógica vive acá.
"""

from __future__ import annotations

import logging

from google import genai

from .config import Ajustes
from .ia import ErrorDefinitivo, ReintentarLuego, extraer_factura
from .imagen import preparar_imagen
from .modelos import EstadoFactura, FacturaParaGuardar, ResultadoEncolado
from .repositorio import Repositorio
from .validacion import normalizar_datos, validar_datos

logger = logging.getLogger(__name__)


def encolar_factura(
    contenido: bytes,
    *,
    ajustes: Ajustes,
    repositorio: Repositorio,
) -> ResultadoEncolado:
    """Comprime, calcula el hash y deja la factura en la cola. Sin IA."""
    imagen = preparar_imagen(
        contenido,
        lado_maximo=ajustes.lado_maximo_px,
        calidad=ajustes.calidad_jpeg,
    )
    factura_id, estado, ya_existia = repositorio.encolar(
        imagen.hash_imagen, imagen.jpeg
    )
    return ResultadoEncolado(id=factura_id, estado=estado, duplicado=ya_existia)


def procesar_una(
    *,
    ajustes: Ajustes,
    cliente: genai.Client,
    repositorio: Repositorio,
    factura_id: int | None = None,
    excluir: list[int] | None = None,
) -> EstadoFactura | None:
    """Toma una factura de la cola y la intenta. Devuelve su estado final.

    Con `factura_id` procesa esa en particular (lo llama el navegador).
    Sin `factura_id` toma la más vieja (lo llama el cron).
    """
    fila = (
        repositorio.tomar(factura_id, ajustes.segundos_colgada)
        if factura_id is not None
        else repositorio.tomar_siguiente(ajustes.segundos_colgada, excluir)
    )

    if fila is None:
        # Otro worker se la llevó, o ya estaba terminada.
        if factura_id is not None:
            return obtener_estado(repositorio, factura_id)
        return None

    try:
        crudo, tokens = extraer_factura(fila.imagen, ajustes, cliente)
    except ReintentarLuego as error:
        if fila.intentos >= ajustes.max_intentos:
            logger.warning("Factura %s agotó los intentos: %s", fila.id, error)
            repositorio.marcar_error(fila.id, str(error))
        else:
            # Vuelve a la cola con la imagen intacta. La próxima corrida insiste.
            repositorio.devolver_a_cola(fila.id, str(error))
        return obtener_estado(repositorio, fila.id)
    except ErrorDefinitivo as error:
        logger.warning("Factura %s falló sin arreglo: %s", fila.id, error)
        repositorio.marcar_error(fila.id, str(error))
        return obtener_estado(repositorio, fila.id)

    datos = normalizar_datos(crudo)
    problemas = validar_datos(datos)
    estado = "revisar" if problemas else "ok"

    # Aquí se guarda todo y se suelta la imagen, en una sola transacción.
    repositorio.guardar_resultado(fila.id, datos, crudo, tokens, estado)

    return EstadoFactura(
        id=fila.id,
        estado=estado,
        datos=datos,
        problemas=problemas,
        tokens=tokens,
    )


def obtener_estado(repositorio: Repositorio, factura_id: int) -> EstadoFactura | None:
    """Estado actual, para el polling del navegador."""
    fila = repositorio.obtener(factura_id)
    if fila is None:
        return None

    datos = fila["datos"]
    return EstadoFactura(
        id=fila["id"],
        estado=fila["estado"],
        datos=datos,
        # Los problemas no se guardan: se recalculan. Es barato y no se
        # desincroniza cuando el usuario corrige.
        problemas=validar_datos(datos) if datos else [],
        tokens=fila["tokens"],
        error=fila["error"],
    )


def guardar_correcciones(
    factura: FacturaParaGuardar,
    *,
    repositorio: Repositorio,
) -> EstadoFactura:
    """Guarda lo que el usuario corrigió y recalcula el estado."""
    problemas = validar_datos(factura.datos)
    estado = "revisar" if problemas else "ok"
    repositorio.actualizar(factura, estado)
    return EstadoFactura(
        id=factura.id,
        estado=estado,
        datos=factura.datos,
        problemas=problemas,
    )
