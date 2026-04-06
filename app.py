from flask import Flask, render_template, request, jsonify, g
from datetime import datetime, date
import os, re, socket, sqlite3, subprocess, tempfile, pdfplumber

app = Flask(__name__)
app.config['SECRET_KEY']         = 'cxp-secret-key-2024'
app.config['DATABASE']           = os.path.join(os.path.dirname(__file__), 'cuentas_pagar.db')
app.config['UPLOAD_FOLDER']      = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# ── DB ────────────────────────────────────────────────────────────────────────
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.execute("""
        CREATE TABLE IF NOT EXISTS facturas (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_propia   TEXT,
            rif_propio       TEXT,
            proveedor        TEXT NOT NULL,
            rif_proveedor    TEXT,
            tipo_doc         TEXT DEFAULT 'COMPRA',
            num_documento    TEXT NOT NULL,
            fecha            TEXT,
            fecha_vence      TEXT,
            hora             TEXT,
            subtotal         REAL DEFAULT 0,
            flete            REAL DEFAULT 0,
            descuento        REAL DEFAULT 0,
            monto_iva        REAL DEFAULT 0,
            total            REAL DEFAULT 0,
            tasa_cambio      REAL DEFAULT 1,
            subtotal_usd     REAL DEFAULT 0,
            monto_iva_usd    REAL DEFAULT 0,
            total_usd        REAL DEFAULT 0,
            moneda           TEXT DEFAULT 'VES',
            estado           TEXT DEFAULT 'pendiente',
            notas            TEXT,
            archivo_pdf      TEXT,
            etiqueta_impresa INTEGER DEFAULT 0,
            created_at       TEXT,
            updated_at       TEXT
        )
    """)
    # Migración: agregar columnas nuevas si la tabla ya existe
    for col, tipo in [('tasa_cambio','REAL DEFAULT 1'), ('subtotal_usd','REAL DEFAULT 0'),
                      ('monto_iva_usd','REAL DEFAULT 0'), ('total_usd','REAL DEFAULT 0')]:
        try:
            db.execute(f'ALTER TABLE facturas ADD COLUMN {col} {tipo}')
        except Exception:
            pass
    # Tabla de ítems por factura
    db.execute("""
        CREATE TABLE IF NOT EXISTS factura_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            factura_id    INTEGER NOT NULL,
            codigo        TEXT,
            descripcion   TEXT,
            cantidad      INTEGER DEFAULT 1,
            precio_unit   REAL DEFAULT 0,
            total_item    REAL DEFAULT 0,
            FOREIGN KEY (factura_id) REFERENCES facturas(id) ON DELETE CASCADE
        )
    """)
    # Catálogo de productos (código → descripción completa)
    db.execute("""
        CREATE TABLE IF NOT EXISTS productos_catalogo (
            codigo      TEXT PRIMARY KEY,
            descripcion TEXT NOT NULL
        )
    """)
    # Tabla de proveedores con código de 4 dígitos
    db.execute("""
        CREATE TABLE IF NOT EXISTS proveedores (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo   TEXT NOT NULL UNIQUE,
            nombre   TEXT NOT NULL UNIQUE
        )
    """)
    # Sembrar proveedores del Excel si la tabla está vacía
    if db.execute('SELECT COUNT(*) FROM proveedores').fetchone()[0] == 0:
        proveedores = [
            ('9702','ASIA-AMERICA'),('8379','BRWME/VENPER'),('9851','CASA DEL FIAT'),
            ('8121','CHAVACAL'),('9160','DAC'),('7971','DIAMOND'),('8798','ECH'),
            ('8435','EICA'),('8847','EURO REPUESTO'),('7194','FENIX'),('7160','GAPM'),
            ('8853','GOMAS DUQUE'),('9775','GREM'),('9644','GRUPO 77'),('7112','HM'),
            ('6301','IMDI'),('7769','ITALVEN'),('9991','MA2017'),('7579','OLIMPIC'),
            ('9309','OMCA'),('6794','ORION/BRIHERCA'),('6743','OYOCAR'),('8841','PATRICIA'),
            ('8834','PROPARTS'),('8281','RG'),('9169','RODALVEN'),('6182','SANCHEZ IMPORT'),
            ('7124','SIN ETIQUETA'),('6119','SPT'),('7234','TABRES'),('7007','YUYO707'),
            ('7382','HYBRID'),
        ]
        db.executemany('INSERT OR IGNORE INTO proveedores (codigo, nombre) VALUES (?,?)', proveedores)
    db.commit(); db.close()

def actualizar_estados(db):
    hoy = date.today().isoformat()
    db.execute("""UPDATE facturas SET estado='vencido'
                  WHERE estado='pendiente' AND fecha_vence IS NOT NULL AND fecha_vence < ?""", (hoy,))
    db.commit()

def row_to_dict(row):
    return dict(row)

# ── PDF Parser calibrado para A2 Softway ─────────────────────────────────────
def parsear_pdf_a2(filepath):
    d = dict(
        empresa_propia='', rif_propio='',
        proveedor='', rif_proveedor='',
        tipo_doc='COMPRA', num_documento='',
        fecha=None, hora='',
        items=[],
        subtotal=0.0, flete=0.0, descuento=0.0,
        monto_iva=0.0, total=0.0,
        moneda='VES', raw_text=''
    )

    # Extraer texto con layout usando pdftotext (mucho mejor que pdfplumber para este formato)
    try:
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as tmp:
            tmpname = tmp.name
        subprocess.run(['pdftotext', '-layout', filepath, tmpname],
                       capture_output=True, check=True)
        with open(tmpname, encoding='utf-8', errors='replace') as f:
            t = f.read()
        os.unlink(tmpname)
    except Exception:
        # Fallback a pdfplumber
        with pdfplumber.open(filepath) as pdf:
            t = '\n'.join((p.extract_text() or '') for p in pdf.pages)

    d['raw_text'] = t

    # ── Empresa propia (primera línea significativa, columna izquierda) ──────
    for line in t.splitlines():
        parte = line[:60].strip()
        if len(parte) > 3 and not re.match(r'^(COMPRA|FACTURA|Fecha|Pag|Hora|Rif|Tel)', parte, re.IGNORECASE):
            d['empresa_propia'] = parte
            break

    # ── Tipo de documento ────────────────────────────────────────────────────
    m = re.search(r'\b(COMPRA|FACTURA|NOTA\s+DE\s+DEBITO|NOTA\s+DE\s+CREDITO|DEVOLUCION)\b',
                  t, re.IGNORECASE)
    if m:
        d['tipo_doc'] = m.group(1).strip().upper()

    # ── Número de documento ──────────────────────────────────────────────────
    # En A2: aparece como número aislado cerca del tipo de doc (lado derecho)
    m = re.search(r'(?:COMPRA|FACTURA)[^\n]*\n\s*(\d{3,})', t, re.IGNORECASE)
    if m:
        d['num_documento'] = m.group(1).strip()
    else:
        m = re.search(r'(?:COMPRA|FACTURA)\s+(\d{3,})', t, re.IGNORECASE)
        if m:
            d['num_documento'] = m.group(1).strip()

    # ── Fecha ────────────────────────────────────────────────────────────────
    m = re.search(r'Fecha[:\s]+(\d{1,2}/\d{1,2}/\d{4})', t, re.IGNORECASE)
    if m:
        try:
            d['fecha'] = datetime.strptime(m.group(1), '%d/%m/%Y').date().isoformat()
        except: pass

    # ── Hora ─────────────────────────────────────────────────────────────────
    m = re.search(r'Hora[:\s]+(\d{1,2}:\d{2}:\d{2})', t, re.IGNORECASE)
    if m:
        d['hora'] = m.group(1)

    # ── RIF de la empresa emisora ────────────────────────────────────────────
    m = re.search(r'Rif[:\s]+([VEJGC]-?\d{5,9}-?\d)', t, re.IGNORECASE)
    if m:
        d['rif_propio'] = m.group(1).strip()

    # ── Proveedor ────────────────────────────────────────────────────────────
    m = re.search(r'Proveedor[:\s]+([^\n\r]{2,60})', t, re.IGNORECASE)
    if m:
        d['proveedor'] = m.group(1).strip()

    # ── Montos ───────────────────────────────────────────────────────────────
    def pmonto(s):
        if not s: return 0.0
        s = s.strip().replace(' ', '')
        # formato venezolano 208.684,00
        if re.match(r'^\d{1,3}(\.\d{3})+,\d{1,2}$', s):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
        try: return float(s)
        except: return 0.0

    m = re.search(r'Total\s+Compra[:\s]+([\d.,]+)', t, re.IGNORECASE)
    if m: d['subtotal'] = pmonto(m.group(1))

    m = re.search(r'Flete[:\s]+([\d.,]+)', t, re.IGNORECASE)
    if m: d['flete'] = pmonto(m.group(1))

    m = re.search(r'Descuento[:\s]+([\d.,]+)', t, re.IGNORECASE)
    if m: d['descuento'] = pmonto(m.group(1))

    m = re.search(r'I\.?V\.?A\.?\s+([\d.,]+)', t, re.IGNORECASE)
    if m: d['monto_iva'] = pmonto(m.group(1))

    m = re.search(r'Total\s+Operacion[:\s]+([\d.,]+)', t, re.IGNORECASE)
    if m:
        d['total'] = pmonto(m.group(1))
    elif d['subtotal']:
        d['total'] = d['subtotal'] + d['monto_iva'] + d['flete']

    # ── Items ────────────────────────────────────────────────────────────────
    # Patrón para texto con layout (pdftotext -layout): columnas separadas por 2+ espacios
    item_pat_layout = re.compile(
        r'^([A-Z][A-Z0-9\-]{2,13})\s{2,}(.+?)\s{2,}(\d{1,4})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s*$',
        re.MULTILINE
    )
    # Patrón alternativo para texto sin layout: campos separados por 1+ espacios
    # Código  Descripción(palabras)  Cantidad  PrecioUnit  Total
    item_pat_plain = re.compile(
        r'^([A-Z][A-Z0-9\-]{2,13})\s+(.+?)\s+(\d{1,4})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s*$',
        re.MULTILINE
    )

    items_found = list(item_pat_layout.finditer(t))
    if not items_found:
        items_found = list(item_pat_plain.finditer(t))

    for m in items_found:
        d['items'].append({
            'codigo':      m.group(1).strip(),
            'descripcion': m.group(2).strip(),
            'cantidad':    int(m.group(3)),
            'precio_unit': pmonto(m.group(4)),
            'total':       pmonto(m.group(5)),
        })

    return d


def extraer_items_pdfplumber(filepath):
    """Extrae ítems usando pdfplumber con detección de tablas — fallback robusto."""
    items = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                # Intentar extracción de tabla
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if not row or len(row) < 3:
                            continue
                        # Primera celda debe parecer un código de producto
                        cod = (row[0] or '').strip()
                        if not re.match(r'^[A-Z][A-Z0-9\-]{2,13}$', cod):
                            continue
                        try:
                            desc  = (row[1] or '').strip()
                            cant  = int(re.sub(r'[^\d]', '', row[2] or '1') or 1)
                            # Precio unitario y total en las últimas 2 celdas con valor
                            nums  = [c for c in row[3:] if c and re.search(r'\d', c)]
                            pu    = pmonto_simple(nums[-2]) if len(nums) >= 2 else 0.0
                            tot   = pmonto_simple(nums[-1]) if len(nums) >= 1 else 0.0
                            if desc and (pu > 0 or tot > 0):
                                items.append({'codigo': cod, 'descripcion': desc,
                                              'cantidad': cant, 'precio_unit': pu, 'total': tot})
                        except Exception:
                            continue
                # Si no hay tabla, intentar con texto plano línea por línea
                if not items:
                    text = page.extract_text() or ''
                    pat  = re.compile(
                        r'^([A-Z][A-Z0-9\-]{2,13})\s+(.+?)\s+(\d{1,4})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s*$',
                        re.MULTILINE
                    )
                    for m in pat.finditer(text):
                        items.append({
                            'codigo':      m.group(1).strip(),
                            'descripcion': m.group(2).strip(),
                            'cantidad':    int(m.group(3)),
                            'precio_unit': pmonto_simple(m.group(4)),
                            'total':       pmonto_simple(m.group(5)),
                        })
    except Exception as e:
        app.logger.error(f'extraer_items_pdfplumber error: {e}')
    return items


def pmonto_simple(s):
    if not s: return 0.0
    s = s.strip().replace(' ', '')
    if re.match(r'^\d{1,3}(\.\d{3})+,\d{1,2}$', s):
        s = s.replace('.', '').replace(',', '.')
    else:
        s = s.replace(',', '.')
    try: return float(s)
    except: return 0.0

# ── ZPL por ítem para Zebra ZD2824 Plus ──────────────────────────────────────
# Etiqueta: 2.25" x 1.25"  →  457 x 254 dots @ 203 dpi
_MUR_MAP = {'0':'O','1':'M','2':'U','3':'R','4':'C','5':'I','6':'E','7':'L','8':'A','9':'G'}

def a_murcielago(costo_usd):
    import math
    dec    = costo_usd - math.floor(costo_usd)
    entero = math.ceil(costo_usd) if dec > 0.5 else math.floor(costo_usd)
    return ''.join(_MUR_MAP[d] for d in str(max(entero, 0)))

def generar_zpl_item(item, factura, precio_venta, timestamp_unix):
    def trunc(s, n): return (str(s or ''))[:n]

    codigo      = trunc(item.get('codigo',''), 20)
    descripcion = trunc(item.get('descripcion',''), 32)
    cantidad    = int(item.get('cantidad', 1))
    pv_usd      = float(precio_venta or 0)
    pv_usd_str  = f'{pv_usd:,.2f}'
    tasa        = float(factura.get('tasa_cambio') or 1) or 1
    costo_usd   = float(item.get('precio_unit', 0)) / tasa
    murcielago  = a_murcielago(costo_usd)
    ts          = int(timestamp_unix)
    referencia  = trunc(factura.get('referencia',''), 24)  # 8121-42089
    bc          = re.sub(r'[^A-Z0-9]', '', codigo.upper()) or 'PROD'

    zpl = (
        f'^XA\n'
        f'^PW456\n^LL352\n'
        f'^PQ{cantidad}\n'
        # Borde  (57mm × 44mm @ 203dpi = 456 × 352 dots)
        f'^FO4,4^GB448,344,2^FS\n'
        # Código del producto
        f'^CF0,30\n^FO10,18^FD{codigo}^FS\n'
        # Descripción
        f'^CF0,22\n^FO10,56^FD{descripcion}^FS\n'
        # Línea separadora
        f'^FO4,86^GB448,0,1^FS\n'
        # Precio de venta en USD (grande)
        f'^CF0,60\n^FO10,100^FD$ {pv_usd_str}^FS\n'
        # Línea separadora
        f'^FO4,172^GB448,0,1^FS\n'
        # Referencia  |  MURCIELAGO  |  timestamp
        f'^CF0,20\n^FO10,190^FD{referencia}^FS\n'
        f'^CF0,24\n^FO240,186^FD{murcielago}^FS\n'
        f'^CF0,16\n^FO330,194^FD{ts}^FS\n'
        # Línea separadora barcode
        f'^FO4,208^GB448,0,1^FS\n'
        # Código de barras del producto
        f'^BY2,3,80\n'
        f'^FO10,214^BCN,80,N,N^FD{bc}^FS\n'
        f'^XZ'
    )
    return zpl


def generar_zpl_lote(items_config, factura, timestamp_unix):
    """Concatena ZPL de múltiples ítems en un solo job de impresión."""
    return ''.join(
        generar_zpl_item(ic['item'], factura, ic['precio_venta'], timestamp_unix)
        for ic in items_config
    )

def enviar_red(zpl, ip, puerto=9100):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((ip, int(puerto)))
            s.sendall(zpl.encode('utf-8'))
        return True, f'Impreso en {ip}:{puerto}'
    except Exception as e:
        return False, str(e)

def enviar_windows(zpl, printer_name):
    try:
        import win32print
        hprinter = win32print.OpenPrinter(printer_name)
        try:
            win32print.StartDocPrinter(hprinter, 1, ("ZPL Label", None, "RAW"))
            try:
                win32print.StartPagePrinter(hprinter)
                win32print.WritePrinter(hprinter, zpl.encode('utf-8'))
                win32print.EndPagePrinter(hprinter)
            finally:
                win32print.EndDocPrinter(hprinter)
        finally:
            win32print.ClosePrinter(hprinter)
        return True, f'Impreso en {printer_name}'
    except Exception as e:
        return False, str(e)

def enviar_usb(zpl, port='COM1'):
    try:
        import serial
        with serial.Serial(port, 9600, timeout=5) as ser:
            ser.write(zpl.encode('utf-8'))
        return True, 'Impreso exitosamente'
    except ImportError:
        return False, 'Instala pyserial: pip install pyserial'
    except Exception as e:
        return False, str(e)

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    db = get_db()
    actualizar_estados(db)
    facturas   = [row_to_dict(r) for r in db.execute('SELECT * FROM facturas ORDER BY created_at DESC')]
    pendientes = db.execute("SELECT COUNT(*) FROM facturas WHERE estado='pendiente'").fetchone()[0]
    vencidas   = db.execute("SELECT COUNT(*) FROM facturas WHERE estado='vencido'").fetchone()[0]
    pagadas    = db.execute("SELECT COUNT(*) FROM facturas WHERE estado='pagado'").fetchone()[0]
    total_pend = db.execute("SELECT COALESCE(SUM(total),0) FROM facturas WHERE estado IN ('pendiente','vencido')").fetchone()[0]
    hoy        = date.today().isoformat()
    return render_template('index.html', facturas=facturas, pendientes=pendientes,
                           vencidas=vencidas, pagadas=pagadas,
                           total_pendiente=total_pend, hoy=hoy)

@app.route('/cargar_pdf', methods=['POST'])
def cargar_pdf():
    if 'pdf' not in request.files:
        return jsonify({'error': 'No se recibió archivo'}), 400
    archivo = request.files['pdf']
    if not archivo.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Solo se aceptan archivos PDF'}), 400
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    nombre = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{archivo.filename}"
    ruta   = os.path.join(app.config['UPLOAD_FOLDER'], nombre)
    archivo.save(ruta)
    datos = parsear_pdf_a2(ruta)
    datos['archivo_pdf'] = nombre
    return jsonify({'ok': True, 'datos': datos})

@app.route('/guardar_factura', methods=['POST'])
def guardar_factura():
    d    = request.json or {}
    now  = datetime.utcnow().isoformat()
    db   = get_db()

    tasa       = float(d.get('tasa_cambio') or 1) or 1
    subtotal   = float(d.get('subtotal')   or 0)
    flete      = float(d.get('flete')      or 0)
    descuento  = float(d.get('descuento')  or 0)
    monto_iva  = float(d.get('monto_iva')  or 0)
    total      = float(d.get('total')      or 0)

    subtotal_usd  = round(subtotal  / tasa, 4)
    monto_iva_usd = round(monto_iva / tasa, 4)
    total_usd     = round(total     / tasa, 4)

    cur = db.execute("""
        INSERT INTO facturas
            (empresa_propia, rif_propio, proveedor, rif_proveedor,
             tipo_doc, num_documento, fecha, fecha_vence, hora,
             subtotal, flete, descuento, monto_iva, total,
             tasa_cambio, subtotal_usd, monto_iva_usd, total_usd,
             moneda, estado, notas, archivo_pdf, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        d.get('empresa_propia',''), d.get('rif_propio',''),
        d.get('proveedor',''),      d.get('rif_proveedor',''),
        d.get('tipo_doc','COMPRA'), d.get('num_documento',''),
        d.get('fecha'),             d.get('fecha_vence'),
        d.get('hora',''),
        subtotal, flete, descuento, monto_iva, total,
        tasa, subtotal_usd, monto_iva_usd, total_usd,
        d.get('moneda','VES'), d.get('estado','pendiente'),
        d.get('notas',''),    d.get('archivo_pdf',''),
        now, now
    ))
    fid = cur.lastrowid

    # Guardar ítems si vienen en el payload
    items = d.get('items', [])
    for it in items:
        db.execute("""
            INSERT INTO factura_items (factura_id, codigo, descripcion, cantidad, precio_unit, total_item)
            VALUES (?,?,?,?,?,?)
        """, (fid, it.get('codigo',''), it.get('descripcion',''),
              int(it.get('cantidad',1)), float(it.get('precio_unit',0)),
              float(it.get('total',0))))
    db.commit()
    return jsonify({'ok': True, 'id': fid, 'mensaje': 'Documento guardado'})

@app.route('/api/factura/<int:fid>/items')
def get_items(fid):
    db   = get_db()
    fac  = db.execute('SELECT * FROM facturas WHERE id=?', (fid,)).fetchone()
    if not fac: return jsonify({'error': 'No encontrado'}), 404
    fac_dict = row_to_dict(fac)

    # Buscar código del proveedor por nombre (coincidencia parcial)
    nombre_prov = (fac_dict.get('proveedor') or '').strip().upper()
    cod_prov = ''
    if nombre_prov:
        # Buscar coincidencia exacta primero, luego parcial
        row = db.execute(
            "SELECT codigo FROM proveedores WHERE UPPER(nombre)=? OR UPPER(?) LIKE '%'||UPPER(nombre)||'%' LIMIT 1",
            (nombre_prov, nombre_prov)
        ).fetchone()
        if row:
            cod_prov = row[0]

    fac_dict['cod_proveedor'] = cod_prov
    fac_dict['referencia']    = f"{cod_prov}-{fac_dict.get('num_documento','')}" if cod_prov else fac_dict.get('num_documento','')

    items = [row_to_dict(r) for r in
             db.execute('SELECT * FROM factura_items WHERE factura_id=? ORDER BY id', (fid,))]
    # Enriquecer descripciones desde catálogo
    codigos = [it['codigo'] for it in items if it.get('codigo')]
    if codigos:
        ph  = ','.join('?' * len(codigos))
        cat = {r[0]: r[1] for r in db.execute(
            f'SELECT codigo, descripcion FROM productos_catalogo WHERE codigo IN ({ph})',
            codigos
        ).fetchall()}
        for it in items:
            if it.get('codigo') in cat:
                it['descripcion'] = cat[it['codigo']]
    return jsonify({'factura': fac_dict, 'items': items})

@app.route('/api/upload_y_reimportar', methods=['POST'])
def upload_y_reimportar():
    """Sube un PDF, lo asocia a la factura y extrae sus ítems."""
    fid = request.form.get('factura_id')
    if not fid:
        app.logger.error('upload_y_reimportar: falta factura_id')
        return jsonify({'error': 'factura_id requerido'}), 400
    fid = int(fid)

    if 'pdf' not in request.files:
        app.logger.error('upload_y_reimportar: no se recibió archivo')
        return jsonify({'error': 'No se recibió archivo'}), 400

    archivo = request.files['pdf']
    app.logger.info(f'upload_y_reimportar: archivo={archivo.filename}, fid={fid}')

    if not archivo.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Solo se aceptan archivos PDF'}), 400

    db  = get_db()
    fac = db.execute('SELECT * FROM facturas WHERE id=?', (fid,)).fetchone()
    if not fac:
        return jsonify({'error': 'Factura no encontrada'}), 404

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    # Nombre limpio sin caracteres problemáticos
    nombre_limpio = re.sub(r'[^\w\-.]', '_', archivo.filename)
    nombre = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{nombre_limpio}"
    ruta   = os.path.join(app.config['UPLOAD_FOLDER'], nombre)
    archivo.save(ruta)
    app.logger.info(f'upload_y_reimportar: guardado en {ruta}, existe={os.path.exists(ruta)}, size={os.path.getsize(ruta)}')

    datos = parsear_pdf_a2(ruta)
    items = datos.get('items', [])
    app.logger.info(f'upload_y_reimportar: items encontrados={len(items)}, error={datos.get("error")}')

    if not items:
        # Intentar con pdfplumber directo como último recurso
        items = extraer_items_pdfplumber(ruta)
        app.logger.info(f'upload_y_reimportar: items con fallback pdfplumber={len(items)}')

    if not items:
        return jsonify({
            'error': f'No se encontraron productos en el PDF. '
                     f'Texto extraído: {datos.get("raw_text","")[:200]}'
        }), 400

    db.execute('UPDATE facturas SET archivo_pdf=?, updated_at=? WHERE id=?',
               (nombre, datetime.utcnow().isoformat(), fid))
    db.execute('DELETE FROM factura_items WHERE factura_id=?', (fid,))
    for it in items:
        db.execute("""
            INSERT INTO factura_items (factura_id, codigo, descripcion, cantidad, precio_unit, total_item)
            VALUES (?,?,?,?,?,?)
        """, (fid, it.get('codigo',''), it.get('descripcion',''),
              int(it.get('cantidad',1)), float(it.get('precio_unit',0)),
              float(it.get('total',0))))
    db.commit()
    return jsonify({'ok': True, 'items': len(items),
                    'mensaje': f'{len(items)} productos importados del PDF'})

@app.route('/api/factura/<int:fid>/reimportar_items', methods=['POST'])
def reimportar_items(fid):
    """Re-parsea el PDF guardado y salva los ítems en factura_items."""
    db  = get_db()
    fac = db.execute('SELECT * FROM facturas WHERE id=?', (fid,)).fetchone()
    if not fac:
        return jsonify({'error': 'Factura no encontrada'}), 404

    archivo_pdf = fac['archivo_pdf']
    if not archivo_pdf:
        return jsonify({'error': 'Esta factura no tiene PDF asociado'}), 400

    ruta = os.path.join(app.config['UPLOAD_FOLDER'], archivo_pdf)
    if not os.path.exists(ruta):
        return jsonify({'error': f'Archivo PDF no encontrado: {archivo_pdf}'}), 404

    datos = parsear_pdf_a2(ruta)
    items = datos.get('items', [])
    if not items:
        return jsonify({'error': 'No se encontraron ítems en el PDF'}), 400

    # Borrar ítems anteriores e insertar los nuevos
    db.execute('DELETE FROM factura_items WHERE factura_id=?', (fid,))
    for it in items:
        db.execute("""
            INSERT INTO factura_items (factura_id, codigo, descripcion, cantidad, precio_unit, total_item)
            VALUES (?,?,?,?,?,?)
        """, (fid, it.get('codigo',''), it.get('descripcion',''),
              int(it.get('cantidad',1)), float(it.get('precio_unit',0)),
              float(it.get('total',0))))
    db.commit()
    return jsonify({'ok': True, 'items': len(items), 'mensaje': f'{len(items)} ítems importados del PDF'})

@app.route('/api/factura/<int:fid>/guardar_items', methods=['POST'])
def guardar_items(fid):
    """Guarda ítems ingresados manualmente para una factura existente."""
    db    = get_db()
    fac   = db.execute('SELECT * FROM facturas WHERE id=?', (fid,)).fetchone()
    if not fac: return jsonify({'error': 'No encontrado'}), 404
    items = request.json.get('items', [])
    db.execute('DELETE FROM factura_items WHERE factura_id=?', (fid,))
    for it in items:
        db.execute("""
            INSERT INTO factura_items (factura_id, codigo, descripcion, cantidad, precio_unit, total_item)
            VALUES (?,?,?,?,?,?)
        """, (fid, it.get('codigo',''), it.get('descripcion',''),
              int(it.get('cantidad',1)), float(it.get('precio_unit',0)),
              float(it.get('total',0))))
    db.commit()
    return jsonify({'ok': True, 'items': len(items)})

@app.route('/api/preview_items_zpl', methods=['POST'])
def preview_items_zpl():
    """Devuelve ZPL para previsualización de ítems seleccionados con precios de venta."""
    d          = request.json or {}
    factura    = d.get('factura', {})
    items_cfg  = d.get('items', [])   # [{item:{...}, precio_venta: X}, ...]
    ts         = int(datetime.utcnow().timestamp())
    zpls = []
    for ic in items_cfg:
        zpls.append({
            'codigo':      ic['item'].get('codigo',''),
            'descripcion': ic['item'].get('descripcion',''),
            'zpl':         generar_zpl_item(ic['item'], factura, ic.get('precio_venta', 0), ts),
            'timestamp':   ts,
        })
    return jsonify({'ok': True, 'zpls': zpls, 'timestamp': ts})

@app.route('/imprimir_items', methods=['POST'])
def imprimir_items():
    """Imprime etiquetas por ítem con precios de venta."""
    d         = request.json or {}
    factura   = d.get('factura', {})
    items_cfg = d.get('items', [])
    cfg       = d.get('printer', {})
    modo      = cfg.get('modo', 'red')
    ts        = int(datetime.utcnow().timestamp())

    zpl_total = generar_zpl_lote(items_cfg, factura, ts)

    if modo == 'red':
        ip = cfg.get('ip', '').strip()
        if not ip: return jsonify({'error': 'Debe indicar la IP de la impresora'}), 400
        ok, msg = enviar_red(zpl_total, ip, cfg.get('puerto', 9100))
    elif modo == 'windows':
        printer_name = cfg.get('printer_name', '').strip()
        if not printer_name: return jsonify({'error': 'Debe indicar el nombre de la impresora'}), 400
        ok, msg = enviar_windows(zpl_total, printer_name)
    else:
        ok, msg = enviar_usb(zpl_total, cfg.get('port', 'COM1'))

    if ok and factura.get('id'):
        db = get_db()
        db.execute('UPDATE facturas SET etiqueta_impresa=1 WHERE id=?', (factura['id'],))
        db.commit()

    return jsonify({'ok': ok, 'mensaje': msg, 'timestamp': ts, 'total_etiquetas': len(items_cfg)})

@app.route('/actualizar_estado/<int:fid>', methods=['POST'])
def actualizar_estado(fid):
    nuevo = request.json.get('estado')
    if nuevo not in ('pendiente', 'pagado', 'vencido'):
        return jsonify({'error': 'Estado inválido'}), 400
    db = get_db()
    db.execute("UPDATE facturas SET estado=?, updated_at=? WHERE id=?",
               (nuevo, datetime.utcnow().isoformat(), fid))
    db.commit()
    return jsonify({'ok': True})

@app.route('/eliminar_factura/<int:fid>', methods=['DELETE'])
def eliminar_factura(fid):
    db = get_db()
    db.execute('DELETE FROM factura_items WHERE factura_id=?', (fid,))
    db.execute('DELETE FROM facturas WHERE id=?', (fid,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/proveedores')
def api_proveedores():
    db = get_db()
    rows = db.execute('SELECT * FROM proveedores ORDER BY nombre').fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.route('/api/proveedores', methods=['POST'])
def crear_proveedor():
    d      = request.json or {}
    nombre = d.get('nombre','').strip().upper()
    if not nombre:
        return jsonify({'error': 'El nombre es obligatorio'}), 400
    db = get_db()
    # Generar código de 4 dígitos aleatorio que no esté en uso
    import random
    usados = {r[0] for r in db.execute('SELECT codigo FROM proveedores').fetchall()}
    disponibles = [str(n).zfill(4) for n in range(1000, 10000) if str(n).zfill(4) not in usados]
    if not disponibles:
        return jsonify({'error': 'No hay códigos disponibles'}), 500
    codigo = random.choice(disponibles)
    try:
        db.execute('INSERT INTO proveedores (codigo, nombre) VALUES (?,?)', (codigo, nombre))
        db.commit()
        return jsonify({'ok': True, 'codigo': codigo})
    except Exception:
        return jsonify({'error': 'El nombre ya existe'}), 400

@app.route('/api/proveedores/<int:pid>', methods=['PUT'])
def editar_proveedor(pid):
    d      = request.json or {}
    codigo = d.get('codigo','').strip()
    nombre = d.get('nombre','').strip().upper()
    if not re.match(r'^\d{4}$', codigo):
        return jsonify({'error': 'El código debe ser exactamente 4 dígitos'}), 400
    db = get_db()
    try:
        db.execute('UPDATE proveedores SET codigo=?, nombre=? WHERE id=?', (codigo, nombre, pid))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': 'Código o nombre ya existe'}), 400

@app.route('/api/proveedores/<int:pid>', methods=['DELETE'])
def eliminar_proveedor(pid):
    db = get_db()
    db.execute('DELETE FROM proveedores WHERE id=?', (pid,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/dashboard')
def api_dashboard():
    db = get_db()
    actualizar_estados(db)
    return jsonify({
        'pendientes':       db.execute("SELECT COUNT(*) FROM facturas WHERE estado='pendiente'").fetchone()[0],
        'vencidas':         db.execute("SELECT COUNT(*) FROM facturas WHERE estado='vencido'").fetchone()[0],
        'pagadas':          db.execute("SELECT COUNT(*) FROM facturas WHERE estado='pagado'").fetchone()[0],
        'total_pendiente':  db.execute("SELECT COALESCE(SUM(total),0)     FROM facturas WHERE estado IN ('pendiente','vencido')").fetchone()[0],
        'total_pend_usd':   db.execute("SELECT COALESCE(SUM(total_usd),0) FROM facturas WHERE estado IN ('pendiente','vencido')").fetchone()[0],
    })

@app.route('/api/windows_printers')
def windows_printers():
    try:
        import win32print
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printers = [p[2] for p in win32print.EnumPrinters(flags, None, 1)]
        return jsonify({'printers': printers})
    except Exception as e:
        return jsonify({'printers': [], 'error': str(e)})

@app.route('/api/catalogo/stats')
def catalogo_stats():
    db = get_db()
    count = db.execute('SELECT COUNT(*) FROM productos_catalogo').fetchone()[0]
    return jsonify({'count': count})

@app.route('/api/catalogo/upload', methods=['POST'])
def catalogo_upload():
    if 'excel' not in request.files:
        return jsonify({'error': 'No se recibió archivo'}), 400
    archivo = request.files['excel']
    fname = archivo.filename.lower()
    if not fname.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'Solo se aceptan archivos Excel (.xlsx, .xls)'}), 400
    try:
        if fname.endswith('.xlsx'):
            import openpyxl
            wb   = openpyxl.load_workbook(archivo, read_only=True, data_only=True)
            ws   = wb.active
            rows = list(ws.iter_rows(values_only=True))
            wb.close()
        else:
            import xlrd
            data = archivo.read()
            wb   = xlrd.open_workbook(file_contents=data)
            ws   = wb.sheet_by_index(0)
            rows = [tuple(ws.row_values(r)) for r in range(ws.nrows)]
    except Exception as e:
        return jsonify({'error': f'Error al leer el Excel: {e}'}), 400
    if not rows:
        return jsonify({'error': 'El archivo está vacío'}), 400
    # Columna A (índice 0) = código, Columna C (índice 2) = descripción
    # Cabecera en fila 12 (índice 11), datos desde fila 13 (índice 12)
    data_rows = rows[12:]
    db    = get_db()
    count = 0
    for row in data_rows:
        if len(row) < 3:
            continue
        codigo = str(row[0] or '').strip().upper()
        desc   = str(row[2] or '').strip()
        if codigo and desc:
            db.execute('INSERT OR REPLACE INTO productos_catalogo (codigo, descripcion) VALUES (?,?)',
                       (codigo, desc))
            count += 1
    db.commit()
    return jsonify({'ok': True, 'cargados': count,
                    'mensaje': f'{count} productos cargados al catálogo'})

@app.route('/api/tasa', methods=['GET', 'POST'])
def api_tasa():
    db = get_db()
    db.execute("CREATE TABLE IF NOT EXISTS configuracion (clave TEXT PRIMARY KEY, valor TEXT)")
    if request.method == 'POST':
        tasa = request.json.get('tasa', 1)
        db.execute("INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ('tasa_cambio', ?)", (str(tasa),))
        db.commit()
        return jsonify({'ok': True, 'tasa': tasa})
    row = db.execute("SELECT valor FROM configuracion WHERE clave='tasa_cambio'").fetchone()
    return jsonify({'tasa': float(row[0]) if row else 1.0})

if __name__ == '__main__':
    init_db()
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.run(debug=True, host='0.0.0.0', port=5000)
