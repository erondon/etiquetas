# Zebra Label Manager

Aplicación web para generar e imprimir etiquetas Zebra ZPL con catálogo de productos, codificación MURCIELAGO y gestión de proveedores.

## Requisitos

- Python 3.8+
- Impresora Zebra (LP 2824 Plus o compatible)

## Instalación

```bash
pip install -r requirements.txt
python app.py
```

Abre el navegador en `http://localhost:5000`

## Funciones

### Nueva Etiqueta
Crea una etiqueta individual. Al ingresar el código, se autocompletan descripción, precio y costo desde la base de datos. La referencia se construye como `CODIGO_PROVEEDOR-FACTURA`.

### Imprimir Excel
Carga un Excel para imprimir etiquetas en lote. Las cantidades son editables por fila antes de imprimir.

**Columnas esperadas:**
| Columna | Dato |
|---------|------|
| A | Código |
| B | Descripción |
| E | Cantidad |
| G | Costo (USD) |
| K | Precio de venta (USD) |

> Datos desde la fila 2 (fila 1 = cabecera)

### Importar Productos
Carga masiva de productos desde un Excel con solo dos columnas: **A = Código**, **B = Descripción**. Agrega productos nuevos y actualiza los existentes sin borrar nada.

### Proveedores
Gestión de proveedores con código único de 4 dígitos generado automáticamente. El código se usa como prefijo en la referencia de la etiqueta (`CODIGO-FACTURA`).

## Formato de etiqueta

- **Tamaño:** 57 mm × 44 mm (456 × 352 dots @ 203 DPI)
- **ZPL:** generado en servidor y cliente (preview SVG en tiempo real)
- **Codificación MURCIELAGO:** el costo en USD se codifica en la etiqueta

```
M=1  U=2  R=3  C=4  I=5
E=6  L=7  A=8  G=9  O=0
```

Ejemplo: USD 25 → `UE`

## Modos de impresión

| Modo | Descripción |
|------|-------------|
| Windows | Imprime directo al spooler de Windows por nombre de impresora |
| Red TCP/IP | Envía ZPL por socket a IP:puerto (default 9100) |
| USB/COM | Envía ZPL por puerto serial (COM1, COM3, etc.) |

## Base de datos (SQLite)

| Tabla | Descripción |
|-------|-------------|
| `lote_productos` | Catálogo de productos con precios |
| `proveedores` | Proveedores y sus códigos |
| `configuracion` | Tasa de cambio y otros ajustes |

## Estructura

```
files/
├── app.py              # Backend Flask
├── etiquetas.db        # Base de datos SQLite (auto-generada)
├── requirements.txt
└── templates/
    └── index.html      # UI completa (single-page)
```
