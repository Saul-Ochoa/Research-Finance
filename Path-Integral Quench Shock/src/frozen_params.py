"""Lectura y verificación de los parámetros congelados (``config/params_calibrated.yaml``).

Regla dura del shadow: **los parámetros nunca se re-calibran en producción**.
El YAML lleva una huella SHA-256 (``integrity.sha256``) calculada sobre
``version``, ``calibration_window`` y ``tickers``. Si alguien edita un
parámetro a mano sin pasar por ``scripts/calibrate_params.py`` (que escribe una
versión nueva y su huella), la carga falla con ``PARAMS_VERSION_MISMATCH``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

__all__ = ["ParamsVersionMismatch", "fingerprint", "load_frozen_params", "PARAMS_PATH"]

PARAMS_PATH: Path = Path(__file__).resolve().parents[1] / "config" / "params_calibrated.yaml"
_CAMPOS_FIRMADOS = ("version", "calibration_window", "tickers")


class ParamsVersionMismatch(RuntimeError):
    """Los parámetros congelados fueron modificados fuera del flujo de calibración."""


def fingerprint(params: dict[str, Any]) -> str:
    """Huella SHA-256 canónica de los campos firmados del YAML.

    Args:
        params: Contenido del YAML (dict).

    Returns:
        str: Hex digest.
    """
    firmado = {k: params.get(k) for k in _CAMPOS_FIRMADOS}
    canon = json.dumps(firmado, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def load_frozen_params(path: Path | str = PARAMS_PATH, expected_version: str | None = None) -> dict[str, Any]:
    """Carga el YAML de parámetros y verifica que no fue alterado.

    Args:
        path: Ruta del YAML.
        expected_version: Si se indica (p. ej. la versión registrada en los
            logs del shadow), la versión del YAML debe coincidir.

    Returns:
        dict: Parámetros (incluye ``integrity.sha256``).

    Raises:
        FileNotFoundError: Si el YAML no existe.
        ParamsVersionMismatch: Si la huella no coincide o la versión difiere
            de ``expected_version``.
    """
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        params = yaml.safe_load(fh)
    guardada = (params.get("integrity") or {}).get("sha256")
    actual = fingerprint(params)
    if guardada != actual:
        raise ParamsVersionMismatch(
            f"PARAMS_VERSION_MISMATCH: re-calibration detected — {path.name} fue modificado "
            f"(sha256 guardado={str(guardada)[:12]}…, actual={actual[:12]}…). "
            "Para cambiar parámetros usa scripts/calibrate_params.py con una versión nueva.")
    if expected_version is not None and str(params.get("version")) != str(expected_version):
        raise ParamsVersionMismatch(
            f"PARAMS_VERSION_MISMATCH: re-calibration detected — versión del YAML "
            f"{params.get('version')} ≠ versión en producción {expected_version}.")
    return params
