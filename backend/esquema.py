"""Lo que le pedimos a Gemini: el esquema JSON y el prompt.

El prompt es corto y fijo a propósito: cada token de entrada se paga.
El esquema va aparte del prompt, así el modelo responde con el formato exacto
sin que tengamos que explicárselo con palabras.
"""

from __future__ import annotations

# Esquema que Gemini respeta al responder (salida estructurada).
# Ojo: es un subconjunto de OpenAPI. Nada de $ref ni de modelos de Pydantic.
ESQUEMA_FACTURA: dict = {
    "type": "object",
    "properties": {
        "proveedor": {"type": "string"},
        "nit_rut": {"type": "string"},
        "numero_factura": {"type": "string"},
        "cufe": {"type": "string", "nullable": True},
        "fecha": {"type": "string"},
        "subtotal": {"type": "number"},
        "iva": {"type": "number"},
        "total": {"type": "number"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "descripcion": {"type": "string"},
                    "cantidad": {"type": "number"},
                    "precio_unitario": {"type": "number"},
                    "total_item": {"type": "number"},
                },
                "required": [
                    "descripcion",
                    "cantidad",
                    "precio_unitario",
                    "total_item",
                ],
            },
        },
    },
    "required": [
        "proveedor",
        "nit_rut",
        "numero_factura",
        "fecha",
        "subtotal",
        "iva",
        "total",
        "items",
    ],
}

# Prompt corto y fijo. Sin adornos.
PROMPT = (
    "Factura colombiana. Extrae los datos tal como aparecen.\n"
    "- fecha: formato YYYY-MM-DD. Si no se lee, null.\n"
    "- Montos: solo el número, sin puntos de miles ni símbolos.\n"
    "- nit_rut: el número tal cual, sin puntos de miles.\n"
    "- items: una entrada por línea de la factura. Si no hay detalle, lista vacía.\n"
    "- cufe: solo si está impreso. Si no, null.\n"
    "No inventes datos: si algo no aparece, usa null."
)
