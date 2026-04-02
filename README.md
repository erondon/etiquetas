# CxP Manager — Cuentas por Pagar con Etiquetas Zebra

Aplicación web Flask para gestionar cuentas por pagar, cargar PDFs de A2 Softway e imprimir etiquetas en impresora Zebra ZD2824 Plus.

---

## Requisitos del sistema

- Python 3.9 o superior
- pip
- Impresora Zebra ZD2824 Plus (conectada por red TCP/IP o USB)

---

## Instalación

### 1. Clonar / copiar la carpeta del proyecto

```bash
cd cxp_app
```

### 2. Crear entorno virtual (recomendado)

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
source venv/bin/activate
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

---

## Ejecutar la aplicación

```bash
python app.py
```

Abre el navegador en: **http://localhost:5000**

---

## Uso

### Cargar factura desde PDF (A2 Softway)

1. Haz clic en **Cargar PDF** (botón amarillo arriba a la derecha)
2. Arrastra o selecciona el PDF generado por A2 Softway
3. La aplicación extrae automáticamente:
   - Proveedor y RIF
   - Número de factura
   - Fechas de emisión y vencimiento
   - Montos (subtotal, IVA, total)
   - Moneda (VES, USD, EUR)
4. Revisa y corrige los datos si es necesario
5. Haz clic en **Guardar Factura**

### Crear factura manualmente

1. Haz clic en **Nueva** (botón secundario)
2. Llena el formulario
3. Guarda

### Gestión de facturas

- **Filtrar** por estado: Todos / Pendiente / Vencido / Pagado
- **Buscar** por proveedor, N° factura o RIF
- **Marcar como Pagado** con un clic
- **Eliminar** factura

### Imprimir etiqueta Zebra

1. Haz clic en el ícono de impresora en la fila de la factura
2. Verifica la vista previa de la etiqueta
3. Selecciona el modo de conexión:
   - **Red (TCP/IP)**: Ingresa la IP de la impresora (ej: `192.168.1.100`) y puerto `9100`
   - **USB/COM**: Ingresa el puerto (ej: `COM3` en Windows, `/dev/usb/lp0` en Linux)
4. Haz clic en **Imprimir**

### Configurar impresora por defecto

Haz clic en **Configurar** en la barra lateral izquierda (junto al nombre de la impresora) para guardar la configuración de conexión.

---

## Configurar la Zebra ZD2824 Plus por red

En la impresora:
1. Imprime la página de configuración (mantén el botón Feed al encender)
2. Anota la IP asignada por DHCP o configura una IP fija
3. En la aplicación, ingresa esa IP con puerto `9100`

Para conexión USB en Windows:
- El puerto suele ser `COM3`, `COM4`, etc.
- Verifica en Administrador de dispositivos → Puertos COM y LPT

---

## Estructura del proyecto

```
cxp_app/
├── app.py              ← Aplicación Flask principal
├── requirements.txt    ← Dependencias Python
├── README.md
├── instance/
│   └── cuentas_pagar.db  ← Base de datos SQLite (auto-generada)
├── uploads/              ← PDFs cargados (auto-generada)
└── templates/
    └── index.html        ← Interfaz web
```

---

## Notas sobre la extracción de PDFs

El parser de PDF está optimizado para el formato estándar de A2 Softway, buscando:
- Patrones de RIF venezolano (J-, V-, E-, G-, C-)
- Formatos de fecha venezolanos (DD/MM/YYYY)
- Montos con separadores de miles punto y decimal coma (1.234.567,89)
- Palabras clave en español: Factura, Proveedor, Subtotal, IVA, Total, Vence, etc.

Si la extracción no es perfecta (p.ej. PDFs escaneados o con diseño inusual), puedes corregir los datos manualmente antes de guardar.

---

## Formato de etiqueta ZPL

La etiqueta generada para la Zebra ZD2824 Plus incluye:
- Nombre del proveedor
- RIF
- Número de factura
- Fechas de emisión y vencimiento
- Total con moneda
- Estado (PENDIENTE / VENCIDO / PAGADO)
- Código de barras Code 128

Tamaño de etiqueta configurado: **2.25" × 1.75"** a 203 DPI.

---

## Licencia

Uso interno empresarial.
