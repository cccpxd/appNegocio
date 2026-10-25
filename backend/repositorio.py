"""Acceso a datos.

Dos implementaciones del mismo contrato:

- `RepositorioPostgres`: la de verdad, con la cola y el guardado efímero.
- `RepositorioNoDisponible`: la de cuando no hay DATABASE_URL. La app arranca
  igual y las rutas de datos responden 501 con un mensaje claro.

Todo se crea por petición. Nada de conexiones globales.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .modelos import DatosFactura, FacturaParaGuardar


class BaseDeDatosNoConfigurada(RuntimeError):
    """Falta DATABASE_URL."""


@dataclass(frozen=True)
class FacturaEnCola:
    """Lo mínimo que necesita el worker para trabajar."""

    id: int
    imagen: bytes
    intentos: int


class Repositorio(Protocol):
    """Contrato que usan el servicio y las rutas."""

    disponible: bool

    def encolar(self, hash_imagen: str, jpeg: bytes) -> tuple[int, str, bool]:
        """Guarda la factura pendiente. Devuelve (id, estado, ya_existia)."""
        ...

    def tomar(
        self, factura_id: int, segundos_colgada: int = 180
    ) -> FacturaEnCola | None:
        """Reclama una factura concreta. None si ya la tomó otro o terminó."""
        ...

    def tomar_siguiente(
        self, segundos_colgada: int = 180, excluir: list[int] | None = None
    ) -> FacturaEnCola | None:
        """Reclama la factura pendiente más vieja. None si la cola está vacía.

        `excluir` evita que una misma corrida del cron reintente la misma
        factura varias veces y le queme los intentos de una sola vez.
        """
        ...

    def guardar_resultado(
        self,
        factura_id: int,
        datos: DatosFactura,
        crudo: dict,
        tokens: int | None,
        estado: str,
    ) -> None:
        """Guarda datos + ítems y SUELTA LA IMAGEN. Todo en una transacción."""
        ...

    def devolver_a_cola(self, factura_id: int, motivo: str) -> None:
        """Vuelve a 'pendiente' CONSERVANDO la imagen (Flex dijo 503)."""
        ...

    def marcar_error(self, factura_id: int, motivo: str) -> None:
        """Fallo definitivo: suelta la imagen también."""
        ...

    def obtener(self, factura_id: int) -> dict | None:
        """Estado actual + datos, para que el navegador haga polling."""
        ...

    def actualizar(self, factura: FacturaParaGuardar, estado: str) -> int:
        """Guarda las correcciones del usuario y el estado recalculado."""
        ...


class RepositorioNoDisponible:
    """Sin DATABASE_URL. Todo lo que escribe falla con un mensaje claro."""

    disponible = False

    def encolar(self, hash_imagen: str, jpeg: bytes) -> tuple[int, str, bool]:
        raise BaseDeDatosNoConfigurada(_MENSAJE)

    def tomar(self, factura_id: int, segundos_colgada: int = 180) -> FacturaEnCola | None:
        return None

    def tomar_siguiente(
        self, segundos_colgada: int = 180, excluir: list[int] | None = None
    ) -> FacturaEnCola | None:
        return None

    def guardar_resultado(
        self,
        factura_id: int,
        datos: DatosFactura,
        crudo: dict,
        tokens: int | None,
        estado: str,
    ) -> None:
        raise BaseDeDatosNoConfigurada(_MENSAJE)

    def devolver_a_cola(self, factura_id: int, motivo: str) -> None:
        raise BaseDeDatosNoConfigurada(_MENSAJE)

    def marcar_error(self, factura_id: int, motivo: str) -> None:
        raise BaseDeDatosNoConfigurada(_MENSAJE)

    def obtener(self, factura_id: int) -> dict | None:
        return None

    def actualizar(self, factura: FacturaParaGuardar, estado: str) -> int:
        raise BaseDeDatosNoConfigurada(_MENSAJE)


_MENSAJE = (
    "Todavía no hay base de datos conectada. "
    "Agrega DATABASE_URL (Neon) a las variables de entorno."
)
