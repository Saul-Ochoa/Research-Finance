"""Descarga y persistencia de barras intradía (1m) de Yahoo Finance.

Este módulo encapsula la lógica reutilizable para:

* Descargar un día de barras de 1 minuto (incluyendo pre y after market)
  con ``yfinance``.
* Normalizar el índice a la zona ``America/New_York``.
* Clasificar cada barra por sesión (Pre-market / Regular / After-hours).
* Guardar el resultado en ``data/raw/yahoo_1m/{TICKER}/YYYY-MM-DD.csv``
  con el esquema exacto:

  ``Datetime, Open, High, Low, Close, Adj Close, Volume, Sesion, Tipo_Sesion``
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytz
import yfinance as yf

__all__ = [
    "PROJECT_ROOT",
    "DATA_RAW",
    "NY_TZ",
    "COLUMNAS_CSV",
    "hoy_nueva_york",
    "clasificar_sesion",
    "descargar_dia",
    "guardar_dia",
    "ruta_dia",
    "cargar_dia",
    "cargar_rango",
]

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Rutas y constantes
# --------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
DATA_RAW: Path = PROJECT_ROOT / "data" / "raw" / "yahoo_1m"

NY_TZ = pytz.timezone("America/New_York")

COLUMNAS_PRECIO: list[str] = ["Open", "High", "Low", "Close", "Adj Close"]
COLUMNAS_CSV: list[str] = [*COLUMNAS_PRECIO, "Volume", "Sesion", "Tipo_Sesion"]
NOMBRE_INDICE = "Datetime"

_PRE_INICIO = time(4, 0)
_REG_INICIO = time(9, 30)
_REG_FIN = time(16, 0)
_AFTER_FIN = time(20, 0)

_TIPO_EXT = "Horas Extendidas (Pre/After)"
_TIPO_REG = "Sesión Regular"
_FUERA = "Fuera de sesión"


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def hoy_nueva_york() -> date:
    """Devuelve la fecha actual en la zona horaria America/New_York.

    Returns:
        date: Fecha de hoy en Nueva York.
    """
    return datetime.now(NY_TZ).date()


def ruta_dia(ticker: str, fecha: date) -> Path:
    """Construye la ruta del CSV de un ticker para una fecha.

    Args:
        ticker: Símbolo (p. ej. ``"NVDA"``).
        fecha: Día de negociación.

    Returns:
        Path: ``DATA_RAW / TICKER / YYYY-MM-DD.csv``.
    """
    return DATA_RAW / ticker.upper() / f"{fecha.isoformat()}.csv"


def clasificar_sesion(ts: pd.Timestamp | datetime) -> str:
    """Clasifica un instante según la sesión de mercado de EE. UU.

    Si ``ts`` trae zona horaria se convierte a America/New_York; si es
    *naive* se asume que ya está en hora de Nueva York.

    Args:
        ts: Marca temporal de la barra.

    Returns:
        str: ``"Pre-market"`` (04:00–09:30), ``"Regular"`` (09:30–16:00),
        ``"After-hours"`` (16:00–20:00) o ``"Fuera de sesión"``.
    """
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(NY_TZ)
    t = ts.time()
    if _PRE_INICIO <= t < _REG_INICIO:
        return "Pre-market"
    if _REG_INICIO <= t < _REG_FIN:
        return "Regular"
    if _REG_FIN <= t < _AFTER_FIN:
        return "After-hours"
    return _FUERA


def _tipo_sesion(sesion: str) -> str:
    """Mapea la etiqueta de sesión a su tipo (regular / extendida / fuera)."""
    if sesion == "Regular":
        return _TIPO_REG
    if sesion in ("Pre-market", "After-hours"):
        return _TIPO_EXT
    return _FUERA


def _a_nueva_york(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Convierte un índice a America/New_York (naive se asume UTC)."""
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert(NY_TZ)


def _normalizar(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Aplana columnas, ajusta zona horaria, tipos y añade la sesión."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.loc[:, ~df.columns.duplicated()].copy()

    df.index = _a_nueva_york(pd.DatetimeIndex(df.index))
    df.index.name = NOMBRE_INDICE
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    if "Adj Close" not in df.columns:
        logger.warning(
            "%s: yfinance no devolvió 'Adj Close' (auto_adjust=True); "
            "se usa 'Close' como 'Adj Close'.",
            ticker,
        )
        df["Adj Close"] = df["Close"]

    faltantes = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c not in df.columns]
    if faltantes:
        raise ValueError(f"{ticker}: faltan columnas en la respuesta de yfinance: {faltantes}")

    df[COLUMNAS_PRECIO] = df[COLUMNAS_PRECIO].astype("float64")
    df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0).astype("int64")

    df["Sesion"] = [clasificar_sesion(ts) for ts in df.index]
    df["Tipo_Sesion"] = df["Sesion"].map(_tipo_sesion)

    return df[COLUMNAS_CSV]


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------
def descargar_dia(
    ticker: str,
    fecha: date | None = None,
    period: str = "1d",
    interval: str = "1m",
) -> pd.DataFrame | None:
    """Descarga las barras de un día (con pre y after market) de Yahoo.

    Si ``fecha`` es hoy (en Nueva York) se usa ``period``. Si ``fecha`` es un
    día pasado, ``period="1d"`` devolvería la sesión más reciente y no la
    pedida, así que se descarga con ``start``/``end`` acotados a ese día.
    En ambos casos el resultado se filtra al día calendario ``fecha``.

    Args:
        ticker: Símbolo (p. ej. ``"NVDA"``).
        fecha: Día a descargar. Por defecto, hoy en America/New_York.
        period: Ventana de descarga para yfinance (por defecto ``"1d"``).
        interval: Intervalo de las barras (por defecto ``"1m"``).

    Returns:
        pd.DataFrame | None: DataFrame indexado por ``Datetime`` (tz
        America/New_York) con las columnas de ``COLUMNAS_CSV``, o ``None``
        si no hay datos (fin de semana, feriado, fecha fuera de la ventana
        de Yahoo o error de red).
    """
    ticker = ticker.upper()
    hoy = hoy_nueva_york()
    fecha = fecha or hoy

    if fecha > hoy:
        logger.warning("%s: la fecha %s está en el futuro; no hay datos.", ticker, fecha)
        return None
    if fecha.weekday() >= 5:
        logger.warning(
            "%s: %s es fin de semana (%s); el mercado está cerrado.",
            ticker, fecha, fecha.strftime("%A"),
        )
        return None

    kwargs: dict = dict(
        interval=interval,
        prepost=True,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if fecha == hoy:
        kwargs["period"] = period
        logger.info("%s: descargando period=%s interval=%s (prepost)", ticker, period, interval)
    else:
        kwargs["start"] = fecha.isoformat()
        kwargs["end"] = (fecha + timedelta(days=1)).isoformat()
        logger.info(
            "%s: descargando %s (start/end) interval=%s (prepost)", ticker, fecha, interval
        )

    try:
        raw = yf.download(ticker, **kwargs)
    except Exception as exc:  # yfinance puede lanzar errores de red/JSON variados
        logger.warning("%s: error al descargar de Yahoo: %s", ticker, exc)
        return None

    if raw is None or raw.empty:
        logger.warning(
            "%s: yfinance devolvió un DataFrame vacío para %s "
            "(¿feriado, mercado cerrado o fuera de la ventana de %s?).",
            ticker, fecha, interval,
        )
        return None

    try:
        df = _normalizar(raw, ticker)
    except ValueError as exc:
        logger.warning("%s", exc)
        return None

    df = df[df.index.date == fecha]
    if df.empty:
        ultimo = _a_nueva_york(pd.DatetimeIndex(raw.index)).max().date()
        logger.warning(
            "%s: no hay barras para %s (lo más reciente devuelto es %s). "
            "Probablemente sea feriado o la sesión aún no ha comenzado.",
            ticker, fecha, ultimo,
        )
        return None

    return df


def guardar_dia(df: pd.DataFrame, ticker: str, fecha: date) -> Path:
    """Guarda un DataFrame diario en ``data/raw/yahoo_1m/{TICKER}/YYYY-MM-DD.csv``.

    Crea la carpeta si no existe y sobrescribe el archivo si ya existe
    (avisando por consola).

    Args:
        df: DataFrame devuelto por :func:`descargar_dia`.
        ticker: Símbolo del activo.
        fecha: Día al que corresponden los datos (nombre del archivo).

    Returns:
        Path: Ruta absoluta del archivo escrito.
    """
    ruta = ruta_dia(ticker, fecha)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.exists():
        logger.warning("%s: sobrescribiendo archivo existente %s", ticker.upper(), ruta)
    df[COLUMNAS_CSV].to_csv(ruta, index=True, index_label=NOMBRE_INDICE)
    logger.info("%s: %d filas guardadas en %s", ticker.upper(), len(df), ruta)
    return ruta


# --------------------------------------------------------------------------
# Lectura de CSVs guardados
# --------------------------------------------------------------------------
def cargar_dia(ticker: str, fecha: date) -> pd.DataFrame | None:
    """Lee el CSV de un día y lo normaliza al esquema estándar.

    Tolera los CSV antiguos (sin ``Adj Close`` o con otro orden de columnas)
    y recalcula ``Sesion``/``Tipo_Sesion`` si faltan.

    Args:
        ticker: Símbolo del activo.
        fecha: Día a leer.

    Returns:
        pd.DataFrame | None: DataFrame indexado por ``Datetime``
        (tz America/New_York) con las columnas de ``COLUMNAS_CSV``, o
        ``None`` (con un warning) si el archivo falta, está vacío o es
        ilegible.
    """
    ticker = ticker.upper()
    ruta = ruta_dia(ticker, fecha)
    if not ruta.exists():
        logger.warning("%s: no existe %s", ticker, ruta)
        return None
    try:
        df = pd.read_csv(ruta, index_col=0)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, OSError) as exc:
        logger.warning("%s: no se pudo leer %s (%s)", ticker, ruta, exc)
        return None
    if df.empty:
        logger.warning("%s: archivo vacío %s", ticker, ruta)
        return None

    df.index = pd.to_datetime(df.index, utc=True).tz_convert(NY_TZ)
    df.index.name = NOMBRE_INDICE
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    if "Adj Close" not in df.columns:
        df["Adj Close"] = df["Close"]
    df[COLUMNAS_PRECIO] = df[COLUMNAS_PRECIO].astype("float64")
    df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0).astype("int64")
    if "Sesion" not in df.columns:
        df["Sesion"] = [clasificar_sesion(ts) for ts in df.index]
    if "Tipo_Sesion" not in df.columns:
        df["Tipo_Sesion"] = df["Sesion"].map(_tipo_sesion)
    return df[COLUMNAS_CSV]


def cargar_rango(ticker: str, desde: date, hasta: date) -> pd.DataFrame:
    """Concatena los CSV diarios de ``desde`` a ``hasta`` (ambos incluidos).

    Los fines de semana se omiten sin aviso; cualquier otro día sin archivo
    (feriado o descarga faltante) se reporta con un warning y se continúa.

    Args:
        ticker: Símbolo del activo.
        desde: Primer día (incluido).
        hasta: Último día (incluido).

    Returns:
        pd.DataFrame: Barras de todos los días disponibles con columna
        adicional ``Fecha`` (date). Vacío si no hay ningún día.
    """
    partes: list[pd.DataFrame] = []
    dia = desde
    while dia <= hasta:
        if dia.weekday() < 5:
            df = cargar_dia(ticker, dia)
            if df is not None:
                partes.append(df.assign(Fecha=dia))
        dia += timedelta(days=1)
    if not partes:
        logger.warning("%s: no hay datos entre %s y %s", ticker.upper(), desde, hasta)
        return pd.DataFrame(columns=[*COLUMNAS_CSV, "Fecha"])
    return pd.concat(partes).sort_index()
