# Pipeline de descarga de datos

Cómo se obtienen las barras intradía de Yahoo Finance y se guardan en
`data/raw/yahoo_1m/`.

## Flujo

```
fetch_today.py  ──►  descargar_dia()  ──►  yfinance.download(prepost=True)
   (CLI)                  │
                          ├─ aplana las columnas MultiIndex
                          ├─ índice → America/New_York (si viene naive, se asume UTC)
                          ├─ agrega "Adj Close" si falta (copia de Close)
                          ├─ Volume → int64 (los NaN pasan a 0)
                          ├─ clasifica la sesión de cada minuto
                          └─ filtra al día calendario pedido
                  ──►  guardar_dia()  ──►  data/raw/yahoo_1m/{TICKER}/YYYY-MM-DD.csv
```

## Script `scripts/fetch_today.py`

| Argumento | Por defecto | Descripción |
|---|---|---|
| `--tickers` | `NVDA SPY` | Uno o varios símbolos; se convierten a mayúsculas |
| `--date` | hoy en America/New_York | Fecha `YYYY-MM-DD` |
| `--period` | `1d` | Ventana de yfinance; solo se usa cuando la fecha es hoy |
| `--interval` | `1m` | Intervalo de las barras |

- Se puede ejecutar desde `scripts/` o desde la raíz del proyecto. El script
  agrega la raíz a `sys.path` usando `Path(__file__).resolve()`.
- Al terminar imprime un resumen con los tickers, el número de filas, el rango
  temporal y la ruta de cada archivo.
- **Código de salida:** `0` si se guardó al menos un ticker y `1` si no se
  guardó ninguno. Esto sirve para programar el script en el Programador de
  tareas de Windows o en cron.

### Fecha de hoy y fechas pasadas

- **Hoy:** se descarga con `period` (por defecto `1d`).
- **Fecha pasada:** se descarga con `start=fecha` y `end=fecha+1`. Con
  `period="1d"`, yfinance devuelve siempre la sesión **más reciente**, no la
  que se pidió.

En los dos casos el resultado se filtra al día calendario pedido.

## API de `src/data_loader.py`

### Constantes

| Nombre | Valor |
|---|---|
| `PROJECT_ROOT` | Raíz del proyecto (`Path(__file__).resolve().parents[1]`) |
| `DATA_RAW` | `PROJECT_ROOT / "data" / "raw" / "yahoo_1m"` |
| `NY_TZ` | `pytz.timezone("America/New_York")` |
| `COLUMNAS_CSV` | Orden de las columnas de salida (sin contar el índice) |

### Funciones

**`descargar_dia(ticker, fecha=None, period="1d", interval="1m") -> pd.DataFrame | None`**
Descarga y normaliza un día. Devuelve `None`, en lugar de lanzar una
excepción, cuando no hay datos: fin de semana, feriado, fecha futura, fecha
fuera de la ventana de Yahoo o error de red.

**`guardar_dia(df, ticker, fecha) -> Path`**
Escribe el CSV y crea la carpeta si no existe. Si el archivo ya existe, lo
sobrescribe y lo avisa con un `WARNING`.

**`clasificar_sesion(ts) -> str`**
Devuelve `"Pre-market"`, `"Regular"`, `"After-hours"` o `"Fuera de sesión"`.
Si el timestamp no tiene zona horaria, se asume que ya está en hora de Nueva
York.

**`hoy_nueva_york() -> date`**
Fecha actual en America/New_York. Úsala en lugar de `date.today()`, que
depende de la zona horaria de la máquina.

**`ruta_dia(ticker, fecha) -> Path`**
Ruta del CSV correspondiente.

## Casos borde

| Situación | Comportamiento |
|---|---|
| Sábado o domingo | Avisa sin consultar a Yahoo y no crea archivo |
| Feriado | Yahoo no devuelve barras de ese día: avisa ("lo más reciente devuelto es X") y no crea archivo |
| Fecha futura | Avisa y no crea archivo |
| Fecha con más de ~30 días de antigüedad | Yahoo devuelve vacío: avisa y no crea archivo |
| El archivo ya existe | Lo sobrescribe con un aviso |
| Falta `Adj Close` | Usa `Close` y lo avisa. Con `auto_adjust=True` esto pasa **siempre** |
| Error de red o de Yahoo | `WARNING` legible y el ticker queda como "SIN DATOS" |
| Se ejecuta durante la sesión | El archivo llega solo hasta el minuto actual. Vuelve a ejecutarlo después de las 20:00 ET |

## Logging

Usa la librería estándar `logging`, nivel `INFO`, con el formato
`%(asctime)s [%(levelname)s] %(message)s`. Si importas el módulo desde un
notebook, configura el logging tú mismo:

```python
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
```
