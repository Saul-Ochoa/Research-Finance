"""Descarga barras de 1 minuto (con pre/after market) y las guarda por día.

Uso (desde scripts/ o desde la raíz del proyecto):

    python fetch_today.py
    python fetch_today.py --tickers NVDA SPY --date 2026-11-13
    python scripts/fetch_today.py --tickers NVDA --interval 1m --period 1d
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Hace importable src/ sin depender del directorio de trabajo ni de PYTHONPATH.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data_loader import (  # noqa: E402
    descargar_dia,
    guardar_dia,
    hoy_nueva_york,
)

logger = logging.getLogger("fetch_today")


@dataclass
class Resultado:
    """Resumen del procesamiento de un ticker."""

    ticker: str
    filas: int = 0
    inicio: str = "-"
    fin: str = "-"
    ruta: Path | None = None


def _parse_fecha(valor: str) -> date:
    """Convierte ``YYYY-MM-DD`` en ``date`` (para argparse)."""
    try:
        return date.fromisoformat(valor)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Fecha inválida '{valor}', usa YYYY-MM-DD") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Define y parsea los argumentos de línea de comandos.

    Args:
        argv: Lista de argumentos (por defecto ``sys.argv[1:]``).

    Returns:
        argparse.Namespace: Argumentos parseados.
    """
    p = argparse.ArgumentParser(description="Descarga barras intradía de Yahoo Finance.")
    p.add_argument("--tickers", nargs="+", default=["NVDA", "SPY"],
                   help="Tickers a descargar (por defecto: NVDA SPY).")
    p.add_argument("--date", type=_parse_fecha, default=None,
                   help="Fecha YYYY-MM-DD (por defecto: hoy en America/New_York).")
    p.add_argument("--period", default="1d",
                   help="Ventana de descarga de yfinance (por defecto: 1d).")
    p.add_argument("--interval", default="1m",
                   help="Intervalo de las barras (por defecto: 1m).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del script.

    Args:
        argv: Argumentos de línea de comandos.

    Returns:
        int: 0 si al menos un ticker se guardó, 1 en caso contrario.
    """
    # Evita caracteres '�' en consolas de Windows (cp1252) con tildes/eñes.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args(argv)
    fecha: date = args.date or hoy_nueva_york()
    logger.info("Fecha objetivo (America/New_York): %s", fecha)

    resultados: list[Resultado] = []
    for ticker in (t.upper() for t in args.tickers):
        res = Resultado(ticker)
        df = descargar_dia(ticker, fecha, period=args.period, interval=args.interval)
        if df is None:
            logger.warning("%s: sin datos para %s; no se crea archivo.", ticker, fecha)
        else:
            res.filas = len(df)
            res.inicio = str(df.index.min())
            res.fin = str(df.index.max())
            res.ruta = guardar_dia(df, ticker, fecha)
        resultados.append(res)

    print("\n" + "=" * 78)
    print(f"RESUMEN  fecha={fecha}  interval={args.interval}  period={args.period}")
    print("=" * 78)
    for r in resultados:
        estado = str(r.ruta) if r.ruta else "SIN DATOS (no se creó archivo)"
        print(f"{r.ticker:<6} filas={r.filas:<5} rango={r.inicio} -> {r.fin}")
        print(f"       archivo: {estado}")
    print("=" * 78)

    return 0 if any(r.ruta for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
