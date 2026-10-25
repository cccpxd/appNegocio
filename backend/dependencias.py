"""Dependencias de FastAPI.

Aquí se arman las cosas que necesita cada petición: ajustes, cliente de Gemini
y repositorio. Se crean por petición, no al importar el módulo.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from google import genai

from .config import Ajustes, cargar_ajustes
from .ia import crear_cliente
from .repositorio import Repositorio, RepositorioNoDisponible
from .repositorio_postgres import RepositorioPostgres


def obtener_ajustes() -> Ajustes:
    return cargar_ajustes()


def obtener_cliente(
    ajustes: Annotated[Ajustes, Depends(obtener_ajustes)],
) -> genai.Client:
    return crear_cliente(ajustes)


def obtener_repositorio(
    ajustes: Annotated[Ajustes, Depends(obtener_ajustes)],
) -> Repositorio:
    # Sin DATABASE_URL la app arranca igual: las rutas de datos responden 501.
    if not ajustes.base_datos_url:
        return RepositorioNoDisponible()
    return RepositorioPostgres(ajustes.base_datos_url)


AjustesDep = Annotated[Ajustes, Depends(obtener_ajustes)]
ClienteDep = Annotated[genai.Client, Depends(obtener_cliente)]
RepositorioDep = Annotated[Repositorio, Depends(obtener_repositorio)]
