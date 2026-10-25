"""Preparar la foto antes de mandarla a la IA.

Dos trabajos:
1. Dejarla chica (menos píxeles = menos tokens = menos plata).
2. Sacar su huella SHA-256 para no pagar dos veces la misma factura.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError


class ImagenInvalida(ValueError):
    """El archivo no es una imagen que podamos leer."""


@dataclass(frozen=True)
class ImagenPreparada:
    jpeg: bytes
    hash_imagen: str
    ancho: int
    alto: int
    bytes_originales: int

    @property
    def bytes_finales(self) -> int:
        return len(self.jpeg)


def preparar_imagen(
    contenido: bytes,
    *,
    lado_maximo: int,
    calidad: int,
) -> ImagenPreparada:
    """Reescala, pasa a JPEG y calcula el hash de la imagen normalizada."""
    if not contenido:
        raise ImagenInvalida("El archivo llegó vacío.")

    try:
        with Image.open(io.BytesIO(contenido)) as original:
            # Las fotos de celular traen la rotación en el EXIF.
            # Sin esto, la factura llega acostada y la IA lee mal.
            imagen = ImageOps.exif_transpose(original).convert("RGB")
            imagen.thumbnail((lado_maximo, lado_maximo), Image.Resampling.LANCZOS)

            salida = io.BytesIO()
            imagen.save(salida, format="JPEG", quality=calidad, optimize=True)
            jpeg = salida.getvalue()
            ancho, alto = imagen.size
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ImagenInvalida(
            "No pude leer la imagen. Prueba con una foto JPG o PNG."
        ) from error

    return ImagenPreparada(
        jpeg=jpeg,
        # El hash se calcula sobre el JPEG final: es exactamente lo que ve la IA.
        hash_imagen=hashlib.sha256(jpeg).hexdigest(),
        ancho=ancho,
        alto=alto,
        bytes_originales=len(contenido),
    )
