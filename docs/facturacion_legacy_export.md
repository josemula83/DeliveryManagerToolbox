# Equivalencia de exportación con Facturación BBVA V1.3

La fuente de verdad es `legacy/facturacion_anterior.html`. La implementación sigue `makeCell()`, `makeRow()`, `resultForSheet()`, `buildSheetXml()`, `zipStore()` y `exportResultXlsx()`.

## Estructura real encontrada

El exportador antiguo no distribuye las nuevas horas de gestión entre días. `buildSheetXml()` elimina todas las filas de cada hoja calculada y crea un informe nuevo titulado **FACTURACIÓN POR CUENTA**. También elimina combinaciones, formatos condicionales, validaciones y autofiltros de la hoja procesada. Conserva el nombre de la pestaña y el resto del contenedor XLSX.

Por cada cuenta genera:

1. Cabecera con código, servicio y total de horas facturables.
2. Tabla con número de empleado, persona, tipo, horas facturables, horas reales de gestión, tarifa e importe.
3. Filas originales del modelo limpio con tipo `Original`.
4. Excepciones persona/cuenta con tipo `Original gestor` y sus horas intactas.
5. Asignaciones nuevas con tipo `Gestión`, visualmente destacadas.
6. Total de horas e importe de la cuenta.

Después añade el resumen de capacidad de gestores y la tabla legacy de cuatro columnas:

```text
Gestor | Cuenta | Horas facturables | Tarifa
```

No se añade importe a esa tabla porque el HTML antiguo no lo hacía. El importe permanece disponible en la tabla principal y en la interfaz.

## Horas y tarifas

- `Horas facturables` procede directamente de `billable_hours` del optimizador.
- `Horas gestión reales` procede de `management_hours` y nunca se recalcula al exportar.
- `Tarifa` procede de `management_rate`.
- `Importe` procede de `amount` y se valida de nuevo contra horas facturables por tarifa.
- No existe distribución diaria ni una regla legacy para fines de semana, vacaciones o ausencias durante la exportación. Añadirla cambiaría el resultado de V1.3.

## Un mes y varios meses

El libro se carga en memoria y nunca se sobrescribe. En cálculo individual sólo se reescribe la pestaña seleccionada. En cálculo de todos los meses se reescribe cada pestaña con resultado independiente. Las hojas no calculadas se conservan. Los nombres y el orden de todas las pestañas se verifican después de guardar.

## Validaciones

Antes de guardar se bloquea la exportación cuando falta reparto, optimización, tarifa o importe, cuando existe una incidencia grave o cuando las horas por cuenta o gestor/cuenta no cuadran.

Después de guardar se abre de nuevo el XLSX y se comprueba:

- cabecera y tabla resumen de cada mes;
- presencia exacta de cada gestor/cuenta;
- ausencia de duplicados gestor/cuenta;
- horas reales por mes, gestor y cuenta;
- horas facturables de la tabla principal y el resumen;
- tarifas e importes finales;
- conservación de personas normales y excepciones;
- estilo diferenciado de filas de gestión;
- nombres y orden de pestañas.

El nombre de salida usa el sufijo `_FACTURACION.xlsx` y el contenido original se mantiene inmutable.
