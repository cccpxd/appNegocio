"""La única llamada a Gemini por factura, en modo Flex.

Un intento por corrida. Si Flex está congestionado, la factura vuelve a la cola
y la siguiente corrida lo reintenta. No dormimos dentro de la función: eso
rompería el límite de tiempo de Vercel.

Todo pasa dentro de funciones. Nada de `client = genai.Client(...)` de módulo.
"""

from __future__ import annotations

import json
import re

from google import genai
from google.genai import types

from .config import Ajustes
from .esquema import ESQUEMA_FACTURA, PROMPT


class ErrorGemini(RuntimeError):
    """La IA falló."""


class ReintentarLuego(ErrorGemini):
    """Flex está sin capacidad o nos pasamos de tiempo. Vuelve a la cola."""


class ErrorDefinitivo(ErrorGemini):
    """No tiene sentido insistir: imagen mala, cuota agotada, error del request."""


def crear_cliente(ajustes: Ajustes) -> genai.Client:
    return genai.Client(
        api_key=ajustes.google_api_key,
        http_options=types.HttpOptions(
            timeout=ajustes.tiempo_maximo_gemini_ms,
            # Un solo intento. El reintento lo hace la cola, no el SDK:
            # si Gemini está saturado, la factura vuelve y se intenta después.
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def extraer_factura(
    jpeg: bytes,
    ajustes: Ajustes,
    cliente: genai.Client,
) -> tuple[dict, int | None]:
    """Manda la imagen y devuelve (json_crudo, tokens). Un solo intento."""
    configuracion = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ESQUEMA_FACTURA,
        # Resolución media: la mitad de tokens y sigue leyendo bien.
        media_resolution="MEDIA_RESOLUTION_MEDIUM",
        # Sin "pensar": no aporta para leer una factura y se paga.
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        temperature=0,
        max_output_tokens=4096,
        # Flex cuesta la mitad. Puede tardar minutos y puede ser cancelada,
        # por eso la factura vive en una cola y no dentro de la petición.
        service_tier=_nivel_de_servicio(ajustes.servicio_ia),
    )

    parte_imagen = types.Part.from_bytes(data=jpeg, mime_type="image/jpeg")

    try:
        respuesta = cliente.models.generate_content(
            model=ajustes.modelo,
            contents=[parte_imagen, PROMPT],
            config=configuracion,
        )
    except Exception as error:  # noqa: BLE001 - el SDK tira varios tipos
        raise clasificar(error) from error

    texto = (respuesta.text or "").strip()
    if not texto:
        raise ReintentarLuego("La IA devolvió una respuesta vacía.")

    return _leer_json(texto), _contar_tokens(respuesta)


def _nivel_de_servicio(nombre: str) -> types.ServiceTier:
    """Flex por defecto. 'standard' es el plan B si Vercel corta a los 60 s."""
    if nombre == "standard":
        return types.ServiceTier.STANDARD
    return types.ServiceTier.FLEX


def clasificar(error: Exception) -> ErrorGemini:
    """Traduce el error del SDK a 'reintentar' o 'no insistir'."""
    codigo = getattr(error, "code", None)
    texto = str(error).lower()
    detalle = f"{type(error).__name__}: {str(error)[:200]}"

    if isinstance(codigo, int) and codigo in {429, 500, 502, 503, 504}:
        return ReintentarLuego(detalle)

    pistas = (
        "unavailable",
        "overloaded",
        "rate limit",
        "resource_exhausted",
        "deadline",
        "timeout",
    )
    if any(pista in texto for pista in pistas):
        return ReintentarLuego(detalle)

    return ErrorDefinitivo(detalle)


def _leer_json(texto: str) -> dict:
    limpio = texto.strip()
    if limpio.startswith("```"):
        # Por si el modelo se escapa con bloques de markdown.
        limpio = re.sub(r"^```[a-zA-Z]*\s*", "", limpio)
        limpio = re.sub(r"\s*```$", "", limpio)

    try:
        datos = json.loads(limpio)
    except json.JSONDecodeError as error:
        # El modelo ya respondió; reintentar puede dar lo mismo, pero una vez
        # más es barato comparado con perder la factura.
        raise ReintentarLuego("La IA no devolvió un JSON válido.") from error

    if not isinstance(datos, dict):
        raise ReintentarLuego("La IA devolvió un JSON con forma inesperada.")
    return datos


def _contar_tokens(respuesta: object) -> int | None:
    uso = getattr(respuesta, "usage_metadata", None)
    if uso is None:
        return None
    entrada = getattr(uso, "prompt_token_count", 0) or 0
    salida = getattr(uso, "candidates_token_count", 0) or 0
    return int(entrada + salida)
