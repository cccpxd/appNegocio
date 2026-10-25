"""Modelos de datos de la API (Pydantic).

Ojo: estos modelos NO son el esquema que se le pasa a Gemini.
Ese vive en `esquema.py` y es un diccionario plano.
Aquí solo validamos y damos forma a lo que devolvemos al navegador.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class ItemFactura(BaseModel):
    descripcion: str = ""
    cantidad: float = 0
    precio_unitario: float = 0
    total_item: float = 0


class DatosFactura(BaseModel):
    """Los campos que el usuario puede corregir en pantalla."""

    proveedor: str = ""
    nit_rut: str = ""
    numero_factura: str = ""
    cufe: str | None = None
    fecha: date | None = None
    subtotal: float = 0
    iva: float = 0
    total: float = 0
    items: list[ItemFactura] = Field(default_factory=list)


class ResultadoEncolado(BaseModel):
    """Lo que devuelve POST /procesar. Rápido: no espera a la IA."""

    id: int
    estado: str
    duplicado: bool = False


class EstadoFactura(BaseModel):
    """Lo que devuelve el worker y el polling."""

    id: int
    estado: Literal["pendiente", "procesando", "ok", "revisar", "error"]
    datos: DatosFactura | None = None
    problemas: list[str] = Field(default_factory=list)
    tokens: int | None = None
    error: str | None = None


class FacturaParaGuardar(BaseModel):
    """Lo que manda el navegador a POST /guardar, ya corregido por el usuario."""

    id: int
    datos: DatosFactura
