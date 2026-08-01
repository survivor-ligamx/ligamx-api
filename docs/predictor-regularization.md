# Regularización de `/predict`

El predictor usa exclusivamente partidos terminados de la temporada solicitada. Esto evita mezclar torneos y que una predicción histórica incorpore resultados de temporadas posteriores.

Cuando la muestra local o visitante es pequeña, las tasas observadas se regularizan hacia el promedio de liga correspondiente a la sede. Por ello la ventaja local no desaparece durante las primeras jornadas.

La corrección Dixon–Coles ajusta marcadores bajos y la matriz se normaliza después. El límite defensivo de probabilidades extremas se reporta como *tempering* heurístico; no se presenta como calibración empírica. La respuesta indica `empirically_calibrated: false` hasta que exista validación fuera de muestra.