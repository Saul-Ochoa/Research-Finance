# Formato de los datos

## Ubicación y nombre

```
data/raw/yahoo_1m/{TICKER}/YYYY-MM-DD.csv
```

El nombre del archivo contiene solo la fecha (hora de Nueva York), sin el
ticker ni el intervalo. Ejemplo: `data/raw/yahoo_1m/NVDA/2026-11-13.csv`.

## Esquema

```
Datetime,Open,High,Low,Close,Adj Close,Volume,Sesion,Tipo_Sesion
2026-11-13 04:00:00-05:00,166.83,167.31,162.71,165.96,165.96,0,Pre-market,Horas Extendidas (Pre/After)
```

| Columna | Tipo | Notas |
|---|---|---|
| `Datetime` | timestamp ISO 8601 con offset | America/New_York: `-05:00` en invierno (EST), `-04:00` en verano (EDT). Marca el **inicio** del minuto |
| `Open`, `High`, `Low`, `Close` | float | Precios ajustados (`auto_adjust=True`) |
| `Adj Close` | float | Igual a `Close` (ver más abajo) |
| `Volume` | int | Suele ser `0` en pre-market y after-hours |
| `Sesion` | str | Ver la tabla de sesiones |
| `Tipo_Sesion` | str | Ver la tabla de sesiones |

### Sesiones (hora local de Nueva York)

| Intervalo | `Sesion` | `Tipo_Sesion` |
|---|---|---|
| 04:00 ≤ t < 09:30 | `Pre-market` | `Horas Extendidas (Pre/After)` |
| 09:30 ≤ t < 16:00 | `Regular` | `Sesión Regular` |
| 16:00 ≤ t < 20:00 | `After-hours` | `Horas Extendidas (Pre/After)` |
| resto | `Fuera de sesión` | `Fuera de sesión` |

Un día completo tiene como máximo **960 filas**: 330 de pre-market, 390 de
sesión regular y 240 de after-hours. Es normal que tenga menos, porque Yahoo
omite los minutos sin operaciones, sobre todo en horario extendido.

## Lectura recomendada

```python
import pandas as pd

df = pd.read_csv(
    "data/raw/yahoo_1m/NVDA/2026-11-13.csv",
    index_col="Datetime",
)
df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
```

Hay que convertir a UTC y luego a Nueva York porque un conjunto de archivos
que cruza el cambio de horario (DST) mezcla los offsets `-04:00` y `-05:00`.
Sin `utc=True`, pandas no crea un índice con zona horaria.

## Peculiaridades de los datos

- **`Adj Close` == `Close`.** Con `auto_adjust=True`, los precios OHLC ya
  vienen ajustados por splits y dividendos, y yfinance ≥ 1.x no devuelve
  `Adj Close`. La columna se conserva solo para mantener el esquema.
- **Precisión mezclada.** Algunas barras traen valores `float32` expandidos
  (por ejemplo, `217.44000244140625`) y otras vienen redondeadas
  (`219.6406`). Así lo entrega Yahoo y no se modifica.
- **El volumen del horario extendido no es fiable.** Yahoo reporta `0` en la
  mayoría de las barras de pre-market y after-hours. No lo uses para
  ponderar en esas sesiones.
- **Hay archivos antiguos con formato distinto.** Algunos CSV anteriores a
  este pipeline tienen `Volume` como float (`0.0`) o un orden o conjunto de
  columnas diferente. Para normalizarlos, vuelve a descargarlos con
  `--date`, siempre que sigan dentro de la ventana de 30 días.

## Limitaciones de yfinance

| Limitación | Consecuencia |
|---|---|
| Las barras de 1 minuto solo existen para los **últimos ~30 días** | El histórico hay que construirlo descargando cada día; no se puede rellenar después |
| Cada petición de 1 minuto abarca como máximo **7–8 días** | Con `--period`, el máximo útil es `7d` |
| No hay calendario de feriados | El script consulta a Yahoo y detecta el feriado porque no recibe barras |
| Yahoo puede limitar o bloquear peticiones | Aparece como "SIN DATOS"; vuelve a intentarlo más tarde |
| La API cambia con frecuencia | Revisa las notas de versión si la descarga empieza a fallar. Validado con yfinance 1.8.0 y pandas 3.1.1 |
| Los datos se ajustan retroactivamente | Después de un split, los precios históricos descargados de nuevo no coinciden con los guardados antes |
