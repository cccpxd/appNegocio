-- ============================================================================
-- Cola de facturas con guardado efímero de la imagen
-- ============================================================================
-- La imagen vive en `facturas.imagen` SOLO mientras la factura hace fila.
-- Cuando el worker termina (bien o mal) la pone en NULL.
--
-- Ojo con la expectativa: NULL no encoge el archivo en disco. Deja tuplas
-- muertas que autovacuum reutiliza. El archivo solo encoge si vacuum puede
-- cortar el final (cola vacía) o con VACUUM FULL, que bloquea la tabla.
-- Lo que sí logramos: la tabla se queda del tamaño de la COLA, no del
-- historial. Probado: 15 facturas procesadas, 344 KB estables.
-- ============================================================================

create table if not exists facturas (
    id              bigserial primary key,

    -- Huella de la imagen. Sobrevive al borrado del bytea, así el dedupe
    -- sigue funcionando para siempre.
    hash_imagen     text        not null unique,

    estado          text        not null default 'pendiente',
    intentos        integer     not null default 0,
    tomado_en       timestamptz,

    -- El archivo pesado. Efímero: se pone en NULL al terminar.
    imagen          bytea,
    imagen_mime     text        not null default 'image/jpeg',

    -- Datos extraídos por la IA.
    proveedor       text,
    nit_rut         text,
    numero_factura  text,
    cufe            text,
    fecha           date,
    subtotal        numeric(14, 2),
    iva             numeric(14, 2),
    total           numeric(14, 2),
    json_crudo      jsonb,
    tokens          integer,
    error           text,

    creado_en       timestamptz not null default now(),
    actualizado_en  timestamptz not null default now(),

    constraint facturas_estado_valido check (
        estado in ('pendiente', 'procesando', 'ok', 'revisar', 'error')
    ),

    -- El candado: si la factura salió de la cola, la imagen NO puede seguir ahí.
    -- Así la limpieza no depende de que el código se acuerde.
    constraint facturas_sin_imagen_al_terminar check (
        estado in ('pendiente', 'procesando') or imagen is null
    )
);

-- Cola: solo mira las filas que hacen fila. Índice pequeño y barato.
create index if not exists facturas_cola_idx
    on facturas (creado_en)
    where estado in ('pendiente', 'procesando');

create index if not exists facturas_estado_idx
    on facturas (estado, creado_en desc);

-- Autovacuum más agresivo que el de fábrica (20%). Con blobs en juego,
-- queremos que el espacio muerto se recicle rápido.
alter table facturas set (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_threshold = 50,
    toast.autovacuum_vacuum_scale_factor = 0.02,
    toast.autovacuum_vacuum_threshold = 50
);

-- ============================================================================
-- Ítems de la factura
-- ============================================================================

create table if not exists items (
    id              bigserial primary key,
    factura_id      bigint not null references facturas (id) on delete cascade,
    orden           smallint not null default 0,
    descripcion     text not null default '',
    cantidad        numeric(14, 3) not null default 0,
    precio_unitario numeric(14, 2) not null default 0,
    total_item      numeric(14, 2) not null default 0
);

create index if not exists items_factura_idx on items (factura_id);
