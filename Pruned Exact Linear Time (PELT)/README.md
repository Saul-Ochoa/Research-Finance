# Capítulo 4: Análisis y Discusión de Resultados

## 4.1. Análisis Comparativo de Quiebres Estructurales en la Media vs. Volatilidad

Al aplicar el algoritmo **Pruned Exact Linear Time (PELT)** sobre la serie del **S&P 500** bajo dos horizontes temporales distintos (20 años y 10 años), los resultados revelaron una asimetría fundamental en las propiedades estadísticas del índice.

Mientras que la detección de puntos de cambio orientada a la **media** (retornos logarítmicos continuos con función de costo \(L_{2}\)) arrojó **cero (0) quiebres estructurales** en ambos periodos, la aplicación orientada a la **varianza** (utilizando los retornos al cuadrado como *proxy* de volatilidad latente mediante costo RBF) identificó:

- **23 puntos de cambio** para la serie de **20 años**.
- **16 puntos de cambio** para la serie de **10 años**.

Este hallazgo inicial valida la hipótesis de la **eficiencia débil de los mercados financieros** y la naturaleza **estacionaria en media** de los rendimientos a largo plazo.

Estadísticamente, los choques diarios en el precio se compensan continuamente, manteniendo la media global cercana a cero. Por el contrario, la abundancia de quiebres detectados en la varianza confirma empíricamente el fenómeno de **agrupamiento de volatilidad (*volatility clustering*)**.

El riesgo del S&P 500 no es constante, sino que transiciona de manera abrupta entre regímenes de calma y periodos de alta turbulencia macroeconómica.

---

## 4.2. Validación Histórica y Consistencia Global del Algoritmo PELT

El algoritmo **PELT** demostró una precisión matemática notable al aislar de forma endógena —sin necesidad de introducir fechas previas— los eventos geopolíticos y financieros más disruptivos de las últimas dos décadas.

Como se observa en los resultados cronológicos, PELT identificó con éxito el inicio de la **Crisis Financiera Subprime** el **3 de septiembre de 2008**, apenas días antes del colapso formal de **Lehman Brothers**, marcando un quiebre de régimen persistente hacia la alta volatilidad que no se estabilizó sino hasta mediados de 2009 (**05-06-2009**).

Asimismo, choques sistémicos de la década pasada, como:

- La **crisis de deuda soberana europea** (agosto de 2011).
- El colapso del mercado de derivados de volatilidad de 2018, conocido en la literatura como **Volmageddon**, detectado el **25 y 26 de enero de 2018**.

fueron capturados con exactitud por la regla de poda del algoritmo.

El hito metodológico más robusto se observa en la detección del choque de la **pandemia de COVID-19**. PELT identificó el cambio estructural:

- **20 de febrero de 2020** (serie de 20 años).
- **21 de febrero de 2020** (serie de 10 años).

Históricamente, el **19 de febrero de 2020** el S&P 500 alcanzó su máximo histórico antes de entrar en uno de los **Bear Markets** más rápidos de la historia moderna.

La coincidencia matemática de PELT en ambas ventanas temporales demuestra que los choques sistémicos de gran magnitud poseen una **firma estadística inalterable**, independiente del tamaño de la muestra utilizada.

---

## 4.3. El "Efecto Escala" y la Sensibilidad de la Poda Matemática

Un aporte metodológico clave de esta investigación es el contraste entre el análisis de corto y largo plazo (**10 vs. 20 años**), el cual evidencia el comportamiento del criterio de penalización (\(\beta\)) en la optimización dinámica.

Al expandir la muestra a **20 años**, la inclusión de una crisis de escala macrohistórica como la de **2008** reajusta la varianza global del modelo.

Como consecuencia, eventos menores de inestabilidad de corto plazo observados en la muestra de **10 años**, por ejemplo:

- Las correcciones técnicas de mercado de **julio y septiembre de 2020**.
- El repunte de volatilidad de **febrero de 2026**.

son descartados y **"podados" matemáticamente** en el análisis de 20 años.

Este comportamiento no constituye un error del algoritmo; por el contrario, demuestra la capacidad de PELT para **jerarquizar la relevancia de los quiebres estructurales** en función de la escala temporal del estudio, evitando la sobre-segmentación por ruido cuando se analiza el largo plazo.

---

# Preguntas

## Si preguntan por qué varía un día la fecha del COVID (20 vs. 21 de febrero)

Explica que la diferencia se debe al **efecto de borde (*edge effect*)** y al reajuste de la estimación de la densidad del **kernel RBF** cuando cambia el número total de observaciones en la muestra.

## Si preguntan por las fechas de 2025 y 2026

Justifica que representan la etapa **post-ciclo de alza de tasas de interés de la Reserva Federal (FED)** y la reciente volatilidad asociada al auge de las empresas de **Inteligencia Artificial**, que incrementó la concentración de mercado dentro del S&P 500.