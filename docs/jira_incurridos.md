# JIRA · Informe de Incurridos BBVA

La herramienta consolida Worklogs de los proyectos `BBVACS`, `BBVAALPHA`,
`BBVAVALORE`, `BBVAGDV`, `BBVACIB` y `BBVACIBGM`. La página de la Toolbox abre
`https://umane.emeal.nttdata.com/jiraito/secure/Dashboard.jspa` y proporciona el
marcador **Incurridos BBVA**.

## Sesión y arquitectura

El endpoint de ScriptRunner usa la sesión autenticada de JIRA. Una página de
Streamlit no puede leer las cookies ni ejecutar código en otra pestaña por la
política de mismo origen del navegador. Por ello, el marcador se ejecuta dentro
de la pestaña de JIRA. La Toolbox no recibe ni almacena credenciales, cookies o
respuestas del informe.

El código mantenible está en `modules/jira/assets/extractor.js`. El generador
inyecta únicamente configuración pública y codifica ese fuente como una URL
`javascript:` instalable. También genera un HTML local desde el que se puede
arrastrar el marcador a la barra de favoritos.

## Uso

1. En la Toolbox, abre **JIRA · Incurridos**.
2. Descarga y abre el instalador HTML; después arrastra **Incurridos BBVA** a la
   barra de favoritos. Como alternativa, copia manualmente la dirección mostrada.
3. Pulsa **Abrir JIRA** e inicia sesión.
4. Desde la pestaña de JIRA, pulsa el favorito instalado.
5. Elige las fechas y proyectos, genera el informe y descarga el CSV.

La acción manual de pulsar el favorito es necesaria porque el navegador impide
que Streamlit inyecte código automáticamente en JIRA.

## Consultas y consolidación

El extractor divide el rango por meses naturales. Por ejemplo, del 15 de enero
al 10 de marzo consulta `15/01–31/01`, `01/02–28/02` y `01/03–10/03` por cada
proyecto seleccionado. Las peticiones son secuenciales, con una pausa de 150 ms,
contra:

`/jiraito/rest/scriptrunner/latest/custom/getReportTimesheet`

Los fallos parciales se registran y las demás consultas continúan. No se registran
en disco respuestas ni datos personales.

## CSV

El fichero se denomina `Incurridos_BBVA_FECHA_DESDE_FECHA_HASTA.csv`. Usa UTF-8
con BOM, punto y coma como separador y todos los campos entre comillas. Añade
`PKEY`, `Año`, `Mes` y `Año-Mes` antes de las columnas originales de JIRA.

## Limitaciones

- Es necesaria una sesión JIRA activa y acceso a la red corporativa.
- La barra de favoritos debe permitir marcadores `javascript:`.
- La validación automatizada usa respuestas sintéticas; no consulta datos reales.
- Si cambia el contrato del endpoint o la posición de su columna de fecha, deberá
  publicarse una nueva versión del extractor.
