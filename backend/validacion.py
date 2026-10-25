"""Normalizar y validar con código, no con IA.

El modelo escribe cosas como "Octubre 13, 2026" o "900,811,658-0".
Aquí lo dejamos limpio y revisamos que las cuentas cuadren.
Si algo no cuadra, la factura queda en estado "revisar" y NO se vuelve a
llamar a la IA: el usuario corrige a mano.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from .modelos import DatosFactura, ItemFactura

# Un peso de diferencia se acepta por redondeo.
TOLERANCIA = 1.0

_MESES = {
    "enero": 1, "ene": 1,
    "febrero": 2, "feb": 2,
    "marzo": 3, "mar": 3,
    "abril": 4, "abr": 4,
    "mayo": 5, "may": 5,
    "junio": 6, "jun": 6,
    "julio": 7, "jul": 7,
    "agosto": 8, "ago": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "set": 9,
    "octubre": 10, "oct": 10,
    "noviembre": 11, "nov": 11,
    "diciembre": 12, "dic": 12,
}


# --------------------------------------------------------------------------
# Limpieza de valores sueltos
# --------------------------------------------------------------------------


def parsear_monto(valor: object) -> float:
    """Convierte "1.067.500,00" o "$ 1.067.500" en 1067500.0."""
    if isinstance(valor, bool):
        return 0.0
    if isinstance(valor, (int, float)):
        return round(float(valor), 2)

    texto = re.sub(r"[^\d,.\-]", "", str(valor or ""))
    if not texto or texto in {"-", ".", ","}:
        return 0.0

    if "," in texto and "." in texto:
        # Formato colombiano: 1.234.567,89
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        # Formato gringo: 1,234,567.89
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif texto.count(".") > 1:
        # 1.067.500 son miles, no decimales.
        texto = texto.replace(".", "")
    elif texto.count(".") == 1:
        # "1.067" en Colombia es mil sesenta y siete, no 1,067.
        entero, decimales = texto.split(".")
        if len(decimales) == 3 and 1 <= len(entero) <= 3 and entero.isdigit() and entero != "0":
            texto = entero + decimales

    try:
        return round(float(texto), 2)
    except ValueError:
        return 0.0


def parsear_fecha(valor: object) -> date | None:
    """Acepta 2026-10-13, 13/10/2026, "13 de octubre de 2026" y "octubre 13, 2026"."""
    if isinstance(valor, date):
        return valor

    texto = _sin_acentos(str(valor or "")).lower().strip()
    if not texto:
        return None

    # 2026-10-13
    coincidencia = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", texto)
    if coincidencia:
        return _fecha(int(coincidencia[1]), int(coincidencia[2]), int(coincidencia[3]))

    # 13/10/2026 o 13-10-26
    coincidencia = re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", texto)
    if coincidencia:
        dia, mes, anio = (int(g) for g in coincidencia.groups())
        if anio < 100:
            anio += 2000
        # En Colombia va día/mes. Si no cuadra, probamos al revés.
        if mes > 12 and dia <= 12:
            dia, mes = mes, dia
        return _fecha(anio, mes, dia)

    # 13 de octubre de 2026 / 13 octubre 2026
    coincidencia = re.search(
        r"(\d{1,2})\s*(?:de\s+)?([a-z]+)\s*(?:de\s+|del\s+)?(\d{4})", texto
    )
    if coincidencia and coincidencia[2] in _MESES:
        return _fecha(int(coincidencia[3]), _MESES[coincidencia[2]], int(coincidencia[1]))

    # octubre 13, 2026
    coincidencia = re.search(
        r"([a-z]+)\s+(\d{1,2})\s*(?:de\s+|del\s+)?,?\s*(\d{4})", texto
    )
    if coincidencia and coincidencia[1] in _MESES:
        return _fecha(int(coincidencia[3]), _MESES[coincidencia[1]], int(coincidencia[2]))

    return None


def limpiar_nit(valor: object) -> str:
    """Deja solo dígitos y el guion del dígito de verificación."""
    texto = str(valor or "").strip()
    if not texto:
        return ""
    limpio = re.sub(r"[^\d\-]", "", texto)
    return re.sub(r"-{2,}", "-", limpio).strip("-")


def _sin_acentos(texto: str) -> str:
    return (
        unicodedata.normalize("NFKD", texto)
        .encode("ascii", "ignore")
        .decode("ascii")
    )


def _fecha(anio: int, mes: int, dia: int) -> date | None:
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def _texto_o_none(valor: object) -> str | None:
    texto = str(valor or "").strip()
    return texto or None


# --------------------------------------------------------------------------
# Normalizar la respuesta completa de la IA
# --------------------------------------------------------------------------


def normalizar_datos(crudo: dict) -> DatosFactura:
    """Convierte lo que devolvió Gemini en algo limpio y predecible."""
    items: list[ItemFactura] = []
    for fila in crudo.get("items") or []:
        if not isinstance(fila, dict):
            continue

        descripcion = str(fila.get("descripcion") or "").strip()
        cantidad = parsear_monto(fila.get("cantidad"))
        precio = parsear_monto(fila.get("precio_unitario"))
        total = parsear_monto(fila.get("total_item"))

        # Si el modelo no trajo el total de la línea, lo calculamos.
        if total == 0 and cantidad and precio:
            total = round(cantidad * precio, 2)

        if not descripcion and total == 0:
            continue

        items.append(
            ItemFactura(
                descripcion=descripcion,
                cantidad=cantidad,
                precio_unitario=precio,
                total_item=total,
            )
        )

    return DatosFactura(
        proveedor=str(crudo.get("proveedor") or "").strip(),
        nit_rut=limpiar_nit(crudo.get("nit_rut")),
        numero_factura=str(crudo.get("numero_factura") or "").strip(),
        cufe=_texto_o_none(crudo.get("cufe")),
        fecha=parsear_fecha(crudo.get("fecha")),
        subtotal=parsear_monto(crudo.get("subtotal")),
        iva=parsear_monto(crudo.get("iva")),
        total=parsear_monto(crudo.get("total")),
        items=items,
    )


# --------------------------------------------------------------------------
# Reglas de negocio
# --------------------------------------------------------------------------


def validar_datos(datos: DatosFactura) -> list[str]:
    """Devuelve la lista de problemas. Lista vacía = factura confiable."""
    problemas: list[str] = []

    if not datos.proveedor:
        problemas.append("Falta el nombre del proveedor.")
    if not datos.numero_factura:
        problemas.append("Falta el número de factura.")
    if not datos.nit_rut:
        problemas.append("Falta el NIT.")
    if datos.fecha is None:
        problemas.append("La fecha no se pudo leer. Debe ir como AAAA-MM-DD.")
    if datos.total <= 0:
        problemas.append("El total es cero o negativo.")

    suma_total = round(datos.subtotal + datos.iva, 2)
    if abs(suma_total - datos.total) > TOLERANCIA:
        problemas.append(
            f"Subtotal + IVA da {_pesos(suma_total)} y el total dice "
            f"{_pesos(datos.total)}."
        )

    if not datos.items:
        problemas.append("No se detectaron ítems en la factura.")
    else:
        suma_items = round(sum(item.total_item for item in datos.items), 2)
        if abs(suma_items - datos.subtotal) > TOLERANCIA:
            problemas.append(
                f"La suma de los ítems da {_pesos(suma_items)} y el subtotal dice "
                f"{_pesos(datos.subtotal)}."
            )

        for item in datos.items:
            if not item.cantidad or not item.precio_unitario:
                continue
            esperado = round(item.cantidad * item.precio_unitario, 2)
            if abs(esperado - item.total_item) > TOLERANCIA:
                nombre = item.descripcion[:30] or "sin descripción"
                problemas.append(
                    f"Ítem «{nombre}»: {item.cantidad:g} × {_pesos(item.precio_unitario)} "
                    f"no da {_pesos(item.total_item)}."
                )

    return problemas


def _pesos(valor: float) -> str:
    """Formatea 1067500.0 como 1.067.500,00."""
    return f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
