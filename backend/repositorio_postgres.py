"""Implementación de la cola sobre Postgres (Neon).

Todas las consultas están parametrizadas. La conexión se abre y se cierra por
petición: en serverless no sirve un pool global.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .modelos import DatosFactura, FacturaParaGuardar
from .repositorio import FacturaEnCola

# Reclama la factura pendiente más vieja, o una colgada en 'procesando'.
# `skip locked` es lo que evita que dos workers se peleen por la misma fila.
_TOMAR_SIGUIENTE = """
with siguiente as (
    select id
      from facturas
     where imagen is not null
       and id <> all(%s::bigint[])
       and (
             (estado = 'pendiente'
              and (tomado_en is null
                   or tomado_en < now() - interval '30 seconds'))
          or (estado = 'procesando'
              and tomado_en < now() - make_interval(secs => %s))
           )
     order by creado_en
     for update skip locked
     limit 1
)
update facturas f
   set estado = 'procesando',
       tomado_en = now(),
       intentos = intentos + 1,
       actualizado_en = now()
  from siguiente
 where f.id = siguiente.id
returning f.id, f.imagen, f.intentos
"""

_TOMAR_UNA = """
update facturas
   set estado = 'procesando',
       tomado_en = now(),
       intentos = intentos + 1,
       actualizado_en = now()
 where id = %s
   and imagen is not null
   and (
         (estado = 'pendiente'
          and (tomado_en is null
               or tomado_en < now() - interval '30 seconds'))
      or (estado = 'procesando'
          and tomado_en < now() - make_interval(secs => %s))
       )
returning id, imagen, intentos
"""

# El paso crucial: datos + estado final + imagen = NULL, en un solo UPDATE.
_GUARDAR_RESULTADO = """
update facturas
   set estado = %s,
       proveedor = %s,
       nit_rut = %s,
       numero_factura = %s,
       cufe = %s,
       fecha = %s,
       subtotal = %s,
       iva = %s,
       total = %s,
       json_crudo = %s,
       tokens = %s,
       error = null,
       imagen = null,
       actualizado_en = now()
 where id = %s
"""

_INSERTAR_ITEM = """
insert into items (factura_id, orden, descripcion, cantidad, precio_unitario, total_item)
values (%s, %s, %s, %s, %s, %s)
"""


class RepositorioPostgres:
    disponible = True

    def __init__(self, url: str) -> None:
        self._url = url

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------

    def _conectar(self) -> psycopg.Connection:
        # prepare_threshold=None es obligatorio con el pooler de Neon.
        return psycopg.connect(
            self._url,
            row_factory=dict_row,
            prepare_threshold=None,
            connect_timeout=10,
        )

    # ------------------------------------------------------------------
    # Cola
    # ------------------------------------------------------------------

    def encolar(self, hash_imagen: str, jpeg: bytes) -> tuple[int, str, bool]:
        with self._conectar() as con, con.cursor() as cur:
            cur.execute(
                """insert into facturas (hash_imagen, imagen)
                   values (%s, %s)
                   on conflict (hash_imagen) do nothing
                   returning id, estado""",
                (hash_imagen, jpeg),
            )
            fila = cur.fetchone()
            if fila:
                return fila["id"], fila["estado"], False

            # Ya existe. Si había fallado para siempre, la revivimos con la
            # imagen nueva: es un reintento del usuario, no un duplicado.
            cur.execute(
                """update facturas
                      set estado = 'pendiente', intentos = 0, error = null,
                          imagen = %s, tomado_en = null, actualizado_en = now()
                    where hash_imagen = %s and estado = 'error'
                returning id, estado""",
                (jpeg, hash_imagen),
            )
            fila = cur.fetchone()
            if fila:
                return fila["id"], fila["estado"], False

            cur.execute(
                "select id, estado from facturas where hash_imagen = %s",
                (hash_imagen,),
            )
            fila = cur.fetchone()
            return fila["id"], fila["estado"], True

    def tomar(self, factura_id: int, segundos_colgada: int = 180) -> FacturaEnCola | None:
        with self._conectar() as con, con.cursor() as cur:
            cur.execute(_TOMAR_UNA, (factura_id, segundos_colgada))
            return _fila_a_cola(cur.fetchone())

    def tomar_siguiente(
        self, segundos_colgada: int = 180, excluir: list[int] | None = None
    ) -> FacturaEnCola | None:
        with self._conectar() as con, con.cursor() as cur:
            cur.execute(_TOMAR_SIGUIENTE, (excluir or [], segundos_colgada))
            return _fila_a_cola(cur.fetchone())

    # ------------------------------------------------------------------
    # Resultado
    # ------------------------------------------------------------------

    def guardar_resultado(
        self,
        factura_id: int,
        datos: DatosFactura,
        crudo: dict,
        tokens: int | None,
        estado: str,
    ) -> None:
        with self._conectar() as con:
            with con.transaction(), con.cursor() as cur:
                cur.execute(
                    _GUARDAR_RESULTADO,
                    (
                        estado,
                        datos.proveedor,
                        datos.nit_rut,
                        datos.numero_factura,
                        datos.cufe,
                        datos.fecha,
                        datos.subtotal,
                        datos.iva,
                        datos.total,
                        Jsonb(crudo),
                        tokens,
                        factura_id,
                    ),
                )
                _reemplazar_items(cur, factura_id, datos)

    def devolver_a_cola(self, factura_id: int, motivo: str) -> None:
        # Conserva la imagen: la IA dijo "vuelve después". `tomado_en` queda en
        # ahora para que el enfriamiento de 30 s del reclamo haga efecto: así el
        # navegador no quema los 5 intentos en 20 segundos de congestión.
        with self._conectar() as con, con.cursor() as cur:
            cur.execute(
                """update facturas
                      set estado = 'pendiente', tomado_en = now(),
                          error = %s, actualizado_en = now()
                    where id = %s""",
                (motivo[:500], factura_id),
            )

    def marcar_error(self, factura_id: int, motivo: str) -> None:
        # Aquí sí se suelta la imagen: no se va a reintentar sola.
        with self._conectar() as con, con.cursor() as cur:
            cur.execute(
                """update facturas
                      set estado = 'error', error = %s,
                          imagen = null, actualizado_en = now()
                    where id = %s""",
                (motivo[:500], factura_id),
            )

    def actualizar(self, factura: FacturaParaGuardar, estado: str) -> int:
        """Correcciones del usuario. No toca la imagen (ya está en NULL)."""
        datos = factura.datos
        with self._conectar() as con:
            with con.transaction(), con.cursor() as cur:
                cur.execute(
                    """update facturas
                          set estado = %s,
                              proveedor = %s, nit_rut = %s, numero_factura = %s,
                              cufe = %s, fecha = %s, subtotal = %s, iva = %s,
                              total = %s, actualizado_en = now()
                        where id = %s""",
                    (
                        estado,
                        datos.proveedor,
                        datos.nit_rut,
                        datos.numero_factura,
                        datos.cufe,
                        datos.fecha,
                        datos.subtotal,
                        datos.iva,
                        datos.total,
                        factura.id,
                    ),
                )
                _reemplazar_items(cur, factura.id, datos)
            return factura.id

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------

    def obtener(self, factura_id: int) -> dict | None:
        with self._conectar() as con, con.cursor() as cur:
            cur.execute(
                """select id, estado, proveedor, nit_rut, numero_factura, cufe,
                          fecha, subtotal, iva, total, tokens, error
                     from facturas where id = %s""",
                (factura_id,),
            )
            fila = cur.fetchone()
            if fila is None:
                return None

            cur.execute(
                """select descripcion, cantidad, precio_unitario, total_item
                     from items where factura_id = %s order by orden, id""",
                (factura_id,),
            )
            fila["items"] = cur.fetchall()

        fila["datos"] = _datos_desde_fila(fila)
        return fila


# ----------------------------------------------------------------------
# Ayudas
# ----------------------------------------------------------------------


def _fila_a_cola(fila: dict | None) -> FacturaEnCola | None:
    if fila is None:
        return None
    return FacturaEnCola(
        id=fila["id"], imagen=bytes(fila["imagen"]), intentos=fila["intentos"]
    )


def _reemplazar_items(cur: psycopg.Cursor, factura_id: int, datos: DatosFactura) -> None:
    cur.execute("delete from items where factura_id = %s", (factura_id,))
    if not datos.items:
        return
    cur.executemany(
        _INSERTAR_ITEM,
        [
            (
                factura_id,
                orden,
                item.descripcion,
                item.cantidad,
                item.precio_unitario,
                item.total_item,
            )
            for orden, item in enumerate(datos.items)
        ],
    )


def _datos_desde_fila(fila: dict) -> DatosFactura | None:
    """Solo hay datos cuando la IA ya respondió."""
    if fila["estado"] not in ("ok", "revisar"):
        return None
    return DatosFactura(
        proveedor=fila["proveedor"] or "",
        nit_rut=fila["nit_rut"] or "",
        numero_factura=fila["numero_factura"] or "",
        cufe=fila["cufe"],
        fecha=fila["fecha"],
        subtotal=float(fila["subtotal"] or 0),
        iva=float(fila["iva"] or 0),
        total=float(fila["total"] or 0),
        items=fila["items"],
    )
