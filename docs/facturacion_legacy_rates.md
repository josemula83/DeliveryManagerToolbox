# Equivalencia de tarifas y maximización con Facturación BBVA V1.3

La fuente de verdad es el JavaScript Base64 de `legacy/facturacion_anterior.html`. La implementación se ha portado desde `tariffForAccount()` y los pasos 2, 3 y 4 de `runBusiness()`, incluidas las funciones internas equivalentes a `recalc`, `setMultiplier`, `relativeDev`, `tariffBurden`, `solutionScore` y `better`.

## Algoritmo real

1. `tariffForAccount()` busca la cuenta en los grupos configurados. La cantidad del grupo es una **tarifa base**, no una tarifa final. Si no pertenece a un grupo, usa la primera tarifa original de la cuenta que entra en el modelo de cálculo de la hoja. No selecciona el máximo entre varias tarifas detectadas.
2. Las asignaciones se consolidan por gestor y cuenta antes de optimizar. Un bloque gestor/cuenta conserva todas sus horas reales de gestión y recibe una única tarifa.
3. Cada bloque con tarifa base comienza en x2,5. La tarifa facturable es `base * multiplicador` y las horas facturables son `horas gestión / multiplicador`. Los multiplicadores permitidos son x2,5, x2 y x1.
4. Calcula las horas facturables alcanzables entre el mínimo a x2,5 y el máximo a x1. Reparte ese objetivo entre gestores en proporción a sus objetivos de capacidad ya calculados.
5. La primera pasada prueba bajar bloques completos de x2,5 a x2 o x1, y de x2 a x1. Acepta sólo el movimiento que mejora lexicográficamente: máxima desviación relativa, suma de desviaciones cuadradas, diferencia de carga x1, diferencia de carga x2 y penalización total por bajadas.
6. En empates favorece al gestor con menor proporción x1 y después x2. La segunda pasada puede intercambiar una subida y una bajada entre dos gestores si mantiene casi igual la desviación global y reduce la concentración de x1.
7. La optimización no cambia las horas reales por cuenta, por gestor/cuenta ni el total mensual. Sólo determina multiplicador, horas facturables y tarifa facturable.

## Importe en el HTML

El HTML calcula `amount = billHours * billingTariff`, donde `billHours = managementHours / multiplier` y `billingTariff = baseTariff * multiplier`. Por tanto, el importe también equivale a `managementHours * baseTariff` y no cambia al pasar de x2,5 a x2 o x1. La función optimizada equilibra capacidad facturable; el importe queda maximizado al fijar primero la tarifa base del bloque o de la cuenta. La nueva auditoría conserva ambos tipos de horas para evitar confundir horas reales de gestión con horas facturables.

## Diferencias corregidas

| Comportamiento | Antes | Ahora |
|---|---|---|
| Bloque configurado | Tarifa final directa | Tarifa base con x2,5/x2/x1 |
| Varias tarifas originales | Elegía la máxima | Usa la primera que alimenta el cálculo legacy |
| Optimización | Selección local | Función global exacta de V1.3 |
| Gestor/cuenta repetido | Podía conservar tarifas distintas | Se consolida antes de optimizar |
| Horas facturables | Iguales a horas reales | Horas reales divididas por multiplicador |
| Bajadas de tarifa | Sin reparto legacy | Desempate y segunda pasada de equidad |
| Validación de horas | Cuenta y total | Cuenta, gestor/cuenta, total y escalón de 0,5 h |

## Fixtures portados

- Base 17 €, 5 h reales y objetivo 2 h: x2,5, 2 h facturables, 42,50 €/h e importe 85 €.
- Tarifas originales 17/26 €: el HTML toma 17 €, no el máximo; con el mismo objetivo produce el resultado anterior.
- Bloque base 18 €, 5 h y objetivo 2 h: x2,5, 45 €/h e importe 90 €.
- Gestores con 4 h a base 17 € y 6 h a base 20 €, ambos con objetivo 2 h: x2 para el primero y x2,5 para el segundo; 4,4 h facturables e importe total 188 €.
- Dos gestores con dos bloques de 5 h cada uno y objetivo 7 h: cada gestor recibe un bloque x1 y otro x2,5, distribuyendo la carga baja por igual.
- Dos registros del mismo gestor/cuenta se convierten en uno solo sin alterar sus horas.

La generación del Excel final se documenta por separado en `docs/facturacion_legacy_export.md`.
