"""
Tool: Consulta de pagos mensuales del inquilino (Google Sheets)

Consulta la planilla de prorrateo de gastos comunes del edificio (Google Sheets)
y devuelve el pago del mes de un inquilino SOLO tras validar su identidad.

Autenticación de TRES factores (información exclusiva de cada inquilino):
    - ID           -> columna "id" (código personal de 4 dígitos)
    - Nombre       -> columna "Responsable de Pago / Propietario"
    - Departamento -> columna "Bloque inmobiliario"

Particularidad de este sheet: la cabecera ocupa DOS filas (fila 1 = categorías,
fila 2 = detalle). Por eso NO se usa get_all_records() (que asume una sola fila
de cabecera y fallaría con celdas vacías/duplicadas): se lee con
get_all_values() y se fusionan ambas filas en una cabecera efectiva
(fila 2, o fila 1 donde la fila 2 esté vacía).

La credencial del service account se reutiliza de la tool de departamentos:
GOOGLE_SHEETS_SERVICE_ACCOUNT_KEY (JSON completo como string, preferido para
despliegue) o, si no está, GOOGLE_SHEETS_CREDENTIALS_FILE (archivo en disco).
El documento a leer es OTRO archivo, identificado por GOOGLE_PAGOS_SPREADSHEET_ID.

Requisito previo: compartir ese Google Sheet (permiso Lector) con el
client_email del service account.

Autor: Ing. Kevin Inofuente Colque - DataPath
"""

import json
import os
import unicodedata

from dotenv import load_dotenv, find_dotenv
from langchain_core.tools import tool

import gspread

load_dotenv(find_dotenv())

# Raíz del proyecto (este archivo vive en tools/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================================
# CONFIGURACIÓN DE GOOGLE SHEETS (pagos)
# ============================================
SPREADSHEET_ID = os.getenv("GOOGLE_PAGOS_SPREADSHEET_ID")
# Se reutiliza la MISMA credencial del service account de la tool de departamentos.
SERVICE_ACCOUNT_KEY = os.getenv("GOOGLE_SHEETS_SERVICE_ACCOUNT_KEY", "")
CREDENTIALS_FILE = os.getenv(
    "GOOGLE_SHEETS_CREDENTIALS_FILE", "credentials/google-service-account.json"
)
WORKSHEET_NAME = os.getenv("GOOGLE_PAGOS_WORKSHEET", "")  # vacío = primera hoja

# Nombres de columna en el sheet (cabecera efectiva tras fusionar fila 1 + fila 2).
# Si en el sheet real cambian, ajusta SOLO estas constantes.
COL_ID = "id"                                         # código personal de 4 dígitos
COL_NOMBRE = "Responsable de Pago / Propietario"
COL_DEPARTAMENTO = "Bloque inmobiliario"
COL_TOTAL = "Total"

# La cabecera ocupa 2 filas; los datos empiezan en la fila 3.
HEADER_ROWS = 2

# Ruta de la clave JSON resuelta contra la raíz del proyecto (portable)
if not os.path.isabs(CREDENTIALS_FILE):
    CREDENTIALS_FILE = os.path.join(BASE_DIR, CREDENTIALS_FILE)

# --- Validación temprana (RNF-03: fallo al importar si falta lo obligatorio) ---
if not SPREADSHEET_ID:
    raise ValueError(
        "❌ Falta GOOGLE_PAGOS_SPREADSHEET_ID en .env\n"
        "Es el ID del Google Sheet de pagos (la parte entre /d/ y /edit de la URL)."
    )

# Credencial del service account: dict parseado desde la env var, si existe.
_SERVICE_ACCOUNT_INFO = None
if SERVICE_ACCOUNT_KEY.strip():
    try:
        _SERVICE_ACCOUNT_INFO = json.loads(SERVICE_ACCOUNT_KEY)
    except json.JSONDecodeError as e:
        raise ValueError(
            "❌ GOOGLE_SHEETS_SERVICE_ACCOUNT_KEY no contiene un JSON válido.\n"
            f"Debe ser el JSON completo del service account. Detalle: {e}"
        )
elif not os.path.exists(CREDENTIALS_FILE):
    raise ValueError(
        "❌ No se encontró la credencial del service account de Google Sheets.\n"
        "Define GOOGLE_SHEETS_SERVICE_ACCOUNT_KEY (JSON completo como string) o\n"
        f"coloca la clave JSON en disco (buscado en: {CREDENTIALS_FILE})."
    )

# Solo lectura: el agente nunca modifica la hoja
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Cliente perezoso: credencial validada al importar, conexión en la 1ª consulta.
_client = None


def _get_worksheet():
    """Devuelve la hoja de trabajo de pagos (autentica en la primera llamada)."""
    global _client
    if _client is None:
        if _SERVICE_ACCOUNT_INFO is not None:
            _client = gspread.service_account_from_dict(
                _SERVICE_ACCOUNT_INFO, scopes=_SCOPES
            )
        else:
            _client = gspread.service_account(filename=CREDENTIALS_FILE, scopes=_SCOPES)
    spreadsheet = _client.open_by_key(SPREADSHEET_ID)
    if WORKSHEET_NAME:
        return spreadsheet.worksheet(WORKSHEET_NAME)
    return spreadsheet.sheet1


# ============================================
# HELPERS
# ============================================
def _norm(texto: str) -> str:
    """Normaliza para comparar: minúsculas, sin tildes y sin espacios sobrantes."""
    texto = str(texto).strip().lower()
    # Quita acentos (á -> a, ç -> c) para tolerar diferencias de escritura
    texto = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return " ".join(texto.split())  # colapsa espacios internos


def _resolver_columna(headers, objetivo):
    """Devuelve el nombre real de la columna que coincide con `objetivo`
    (comparación normalizada: tolera mayúsculas/tildes/espacios). None si no está."""
    objetivo_norm = _norm(objetivo)
    for h in headers:
        if _norm(h) == objetivo_norm:
            return h
    return None


def _construir_registros(worksheet):
    """
    Lee el sheet completo y devuelve (list[dict], headers).

    Fusiona la cabecera de 2 filas en una sola: usa la fila 2 y, donde esté
    vacía, cae a la fila 1. Los datos empiezan tras HEADER_ROWS filas.
    """
    valores = worksheet.get_all_values()
    if len(valores) <= HEADER_ROWS:
        return [], []

    fila_cat = valores[0]      # fila 1: categorías
    fila_det = valores[1]      # fila 2: detalle
    n_cols = max(len(fila_cat), len(fila_det))

    headers = []
    for i in range(n_cols):
        cat = fila_cat[i].strip() if i < len(fila_cat) else ""
        det = fila_det[i].strip() if i < len(fila_det) else ""
        headers.append(det or cat)  # detalle manda; si está vacío, la categoría

    registros = []
    for fila in valores[HEADER_ROWS:]:
        # Ignora filas totalmente vacías (separadores, totales al pie, etc.)
        if not any(str(c).strip() for c in fila):
            continue
        registros.append(dict(zip(headers, fila)))

    return registros, headers


# ============================================
# FUNCIÓN INTERNA DE CONSULTA
# ============================================
def _consultar_pago(
    id_inquilino: str, nombre: str, departamento: str, incluir_detalle: bool = False
) -> str:
    """Valida identidad (id + nombre + bloque) y devuelve el pago del mes o error."""
    try:
        worksheet = _get_worksheet()
        registros, headers = _construir_registros(worksheet)

        if not registros:
            return "No hay pagos registrados en la hoja por el momento."

        # Resuelve los nombres reales de columna (tolerante a mayúsculas/tildes/espacios)
        col_id = _resolver_columna(headers, COL_ID)
        col_nombre = _resolver_columna(headers, COL_NOMBRE)
        col_depto = _resolver_columna(headers, COL_DEPARTAMENTO)
        col_total = _resolver_columna(headers, COL_TOTAL)

        # Verifica que las columnas clave existan (error de configuración, no de usuario)
        faltantes = [
            etiqueta
            for etiqueta, col in (
                (COL_ID, col_id),
                (COL_NOMBRE, col_nombre),
                (COL_DEPARTAMENTO, col_depto),
            )
            if col is None
        ]
        if faltantes:
            return (
                "Error de configuración: no encuentro las columnas "
                f"{faltantes} en el sheet de pagos. Columnas detectadas: {headers}"
            )

        id_norm = _norm(id_inquilino)
        nombre_norm = _norm(nombre)
        depto_norm = _norm(departamento)

        for registro in registros:
            if (
                _norm(registro.get(col_id, "")) == id_norm
                and _norm(registro.get(col_nombre, "")) == nombre_norm
                and _norm(registro.get(col_depto, "")) == depto_norm
            ):
                return _formatear_pago(
                    registro, col_nombre, col_depto, col_total, col_id, incluir_detalle
                )

        # Validación fallida: NUNCA revelar datos de otro inquilino
        return (
            "No fue posible validar tu identidad. Verifica que tu ID (código de 4 "
            "dígitos), el nombre del responsable de pago y el bloque inmobiliario "
            "(departamento) sean exactamente los que figuran en tus datos."
        )

    except gspread.exceptions.WorksheetNotFound:
        return (
            f"No encontré la pestaña '{WORKSHEET_NAME}' en el Google Sheet de pagos. "
            "Revisa GOOGLE_PAGOS_WORKSHEET."
        )
    except Exception as e:
        return f"Error al consultar el Google Sheets de pagos: {str(e)}"


def _formatear_pago(
    registro: dict, col_nombre, col_depto, col_total, col_id, incluir_detalle: bool = False
) -> str:
    """
    Arma la respuesta según el nivel pedido:
    - Por defecto (incluir_detalle=False): SOLO el total a pagar del mes.
    - Con incluir_detalle=True: el total + la lista detallada (todas las
      columnas con valor: consumos, servicios, mantenimientos, subtotales, etc.).
    """
    respuesta = (
        "✅ Identidad validada correctamente.\n\n"
        f"Departamento (bloque): {registro.get(col_depto, '')}\n"
        f"Responsable de pago: {registro.get(col_nombre, '')}\n\n"
    )

    total = str(registro.get(col_total, "")).strip() if col_total else ""
    respuesta += f"💰 TOTAL A PAGAR ESTE MES: {total or 'no disponible'}\n"

    if incluir_detalle:
        # Lista detallada: todas las columnas con valor, salvo las ya mostradas
        # arriba (nombre, bloque, total) y el ID (dato secreto, no se re-expone).
        omitir = {col_nombre, col_depto, col_total, col_id}
        detalle = [
            f"- {col}: {val}"
            for col, val in registro.items()
            if col not in omitir and str(val).strip()
        ]
        if detalle:
            respuesta += "\nDetalle completo del mes:\n" + "\n".join(detalle) + "\n"

    return respuesta


# ============================================
# TOOL EXPORTABLE
# ============================================
@tool
def consultar_pago_inquilino(
    id_inquilino: str, nombre: str, departamento: str, incluir_detalle: bool = False
) -> str:
    """
    Consulta el pago mensual (gastos comunes) de un inquilino en el Google Sheet.

    Usa esta herramienta cuando el usuario pregunte por SU pago, por ejemplo:
    - "¿Cuánto debo pagar este mes?"
    - "¿Cuál es mi alquiler / boleto / cuota de mantenimiento?"
    - "¿Cuánto me toca pagar de gastos comunes?"

    NIVEL DE DETALLE:
    - Por defecto devuelve SOLO el total a pagar del mes.
    - Si el usuario pide ver el desglose o el detalle (p. ej. "dame el detalle",
      "muéstrame el desglose", "¿en qué se compone?", "detállame los gastos"),
      invócala con incluir_detalle=True para incluir la lista completa de rubros.

    IMPORTANTE — autenticación obligatoria de TRES factores:
    Antes de invocar esta herramienta, el usuario DEBE proporcionar los tres datos:
    - id_inquilino: su ID / código personal de 4 dígitos.
    - nombre: nombre del responsable de pago / propietario, tal como figura en sus datos.
    - departamento: el bloque inmobiliario (unidad) del inquilino.
    Si el usuario no dio los tres, PÍDESELOS; no los inventes ni adivines.

    Nunca reveles el monto si la validación falla: la información de pago es
    exclusiva de cada inquilino y no puede mostrarse a terceros.

    Args:
        id_inquilino: ID / código personal de 4 dígitos del inquilino.
        nombre: Nombre del responsable de pago / propietario.
        departamento: Bloque inmobiliario (unidad) del inquilino.
        incluir_detalle: True SOLO si el usuario pide el desglose/detalle completo;
            False (por defecto) devuelve únicamente el total a pagar.
    """
    # No se loguea el ID en claro (dato secreto): solo se enmascara su longitud.
    print(
        f"   💰 Consultando pago del inquilino "
        f"(bloque: '{departamento}', nombre: '{nombre}', "
        f"id: {'*' * len(str(id_inquilino).strip())}, detalle: {incluir_detalle})"
    )
    return _consultar_pago(id_inquilino, nombre, departamento, incluir_detalle)
