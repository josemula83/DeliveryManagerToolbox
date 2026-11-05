# Equivalencia funcional de Facturación V1.3

Fuente de verdad: `legacy/facturacion_anterior.html`, marcador **Facturación BBVA - V1.3**. El atributo `HREF` contiene un programa JavaScript codificado en Base64. Las referencias siguientes son los nombres de sus funciones una vez decodificado.

## Flujo real

1. `parseXlsx()` abre el ZIP del XLSX, recorre todas las hojas y detecta personas, servicios, cuentas, tarifas y horas diarias.
2. La configuración selecciona gestores, excepciones gestor-cuenta (`managerKeep`), cuentas sin facturación, gestión fija y grupos con tarifa base.
3. `runBusiness()` procesa cada hoja por separado.
4. Suma todas las horas de servicio por empleado en `personHours` y calcula `reference` como el máximo observado en la hoja.
5. Elimina del modelo de cálculo las horas originales de gestores, salvo las cuentas marcadas en `managerKeep`. Las horas conservadas continúan en el informe, pero no alimentan el 5 %.
6. Excluye de gestión las cuentas de `noBilling`. Agrega las horas restantes por cuenta.
7. Calcula gestión fija o `floorHalf(baseHours * 0.05)`, con mínimo de 0,5 h cuando la cuenta tiene horas. Toda gestión queda en unidades de 0,5 h.
8. Para cada gestor usa sus horas de servicio de la hoja como `monthBase`. Si no tiene horas propias, usa `reference`. El objetivo es `monthBase * pct / 100`.
9. Convierte los objetivos en pesos y distribuye todas las horas de gestión proporcionalmente. Redondea las cuotas a 0,5 h mediante mayores restos, con desempate por el orden alfabético de gestores.
10. Recorre cuentas en orden numérico natural y gestores en orden alfabético. Llena cada cuota secuencialmente y divide una cuenta exactamente en el límite de cuota. No existe tolerancia ±10 %, conservación preferente de cuentas ni segunda pasada de rebalanceo.
11. Calcula una capacidad facturable alcanzable y ejecuta la optimización global de tarifas por bloques gestor-cuenta.
12. `buildSheetXml()` reemplaza el contenido de las hojas con el informe y `exportResultXlsx()` genera el XLSX final.

## Matriz de comparación

| Regla | HTML V1.3 | Implementación actual | Estado tras esta revisión | Riesgo pendiente |
|---|---|---|---|---|
| Persona | ID numérico en A y nombre en B | ID y nombre jerárquicos | Coincide | Bajo |
| Cuenta | Admite `EXT-`, `INT-`, `Tarifa` o separador ` > ` | Parser principal centrado en `EXT-` o `Tarifa` | Parcial | Las cuentas solo `INT-` pueden no reconocerse |
| Horas base | Suma columnas diarias de filas de servicio | Suma `daily_hours` por cuenta | Coincide | Bajo |
| Gestores | Se identifican por ID seleccionado | Resolución por ID y nombre normalizado | Compatible y más defensivo | Bajo |
| Excepciones | `managerKeep` por gestor y cuenta | Excepción `employee_id + account_code` | Coincide | Bajo |
| Sin facturación | No genera gestión | Cuenta sin gestión | Coincide | Bajo |
| Gestión fija | Valor libre, normalizado hacia abajo a 0,5 h; mínimo 0,5 si es positivo | Configuración validada en 0,5 h | Coincide para configuraciones válidas | Un JSON legacy puede contener cuartos de hora |
| Gestión porcentual | 5 %, redondeo inferior a 0,5 h, mínimo 0,5 | Misma regla | Coincide | Bajo |
| Base mensual gestor | Horas de servicio propias; si son cero o no existen, máximo de la hoja | Antes usaba calendario teórico | Corregido a legacy | Bajo |
| Porcentaje | Objetivo usado como peso relativo | Antes se trataba como objetivo absoluto | Corregido a legacy | Bajo |
| Cuota | Mayores restos en unidades de 0,5 h | Antes repartía excedente igualitario | Corregido a legacy | Bajo |
| Orden gestores | Nombre, locale español, sin distinguir acentos | Nombre normalizado | Equivalente para los nombres actuales | Casos raros de colación española |
| Orden cuentas | Secuencias numéricas del código y después nombre | Orden natural del código | Coincide en cuentas EXT actuales | Bajo |
| Fragmentación | Divide al alcanzar cada cuota | Antes intentaba mantener cuentas completas | Corregido a legacy | Bajo |
| Tolerancia | No existe | Antes aplicaba zona preferente ±10 % | Eliminada del reparto | Bajo |
| Segunda pasada | No existe para horas de gestión | Antes rebalanceaba | Convertida en operación nula | Bajo |
| Tarifa de grupo | Base que puede multiplicarse por 1, 2 o 2,5 | Tarifa final aplicada directamente | No coincide | Alto; fuera del alcance actual |
| Tarifa sin grupo | Tarifa original de la cuenta | Máxima tarifa detectada | No coincide cuando hay varias | Alto; fuera del alcance actual |
| Horas facturadas | `managementHours / multiplier` | Iguales a horas de gestión | No coincide | Alto; fuera del alcance actual |
| Optimización | Global, minimiza desviación y reparte penalizaciones x1/x2 | Selección directa y consolidación | No coincide | Alto; pendiente de fase específica |
| Tarifa única | Consolida gestor-cuenta antes de optimizar | Consolida por gestor-cuenta-tarifa | Parcial | Puede conservar múltiples tarifas |
| Exportación | Reescribe las hojas del XLSX y genera `_GESTION.xlsx` | Pendiente | No coincide | Alto; fuera del alcance actual |

## Funciones JavaScript de referencia

- Lectura: `unzip`, `cellValue`, `parseAccount`, `parseXlsx`.
- Configuración: `configData`, `applyImportedConfig`, `managerExistingAccounts`.
- Cálculo y reparto: `floorHalf`, `accountSortKey`, `cmpAccounts`, `runBusiness`.
- Tarifas: `tariffForAccount`, `setMultiplier`, `relativeDev`, `tariffBurden`, `solutionScore`, `better` y los dos bucles de optimización dentro de `runBusiness`.
- Exportación: `makeCell`, `makeRow`, `buildSheetXml`, `zipStore`, `exportResultXlsx`.

## Casos de regresión portados

- La base mensual procede de horas de servicio propias y usa el máximo de la hoja como respaldo.
- Cuatro objetivos 181/90,5/181/181 y 991,5 h producen cuotas 283,5/141,5/283,5/283 h.
- Los mayores restos trabajan en unidades exactas de 0,5 h y desempatan por orden de gestor.
- Una cuenta se divide en el límite de cuota; no se aplica tolerancia para conservarla completa.
- El reparto conserva el cuadre exacto por cuenta y hoja y es determinista.

La optimización de tarifas y la exportación se documentan como diferencias, pero no se modifican en esta fase.
