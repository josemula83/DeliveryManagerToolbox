# JIRA · Informe de Incurridos BBVA

La herramienta consolida Worklogs de los proyectos configurados en el extractor.
JIRA se abre en una ventana Edge/WebView2 administrada por Delivery Manager
Toolbox. Si la sesión no está autenticada, el usuario inicia sesión directamente
en esa ventana; al llegar al origen autorizado, la aplicación inyecta
automáticamente el formulario de extracción.

## Sesión y arquitectura

El arranque recomendado es `python desktop.py`. El contenedor inicia Streamlit en
un puerto local no fijo y crea dos ventanas pertenecientes al mismo proceso de
escritorio: la Toolbox y JIRA. La selección del módulo JIRA envía una orden por
un canal efímero limitado a `127.0.0.1` para mostrar la segunda ventana.

El perfil de WebView2 se conserva en
`%LOCALAPPDATA%\DeliveryManagerToolbox\WebView2`, fuera del repositorio. WebView2
gestiona directamente la sesión. El código Python no lee, copia ni registra
credenciales, cookies, tokens, URLs de navegación o respuestas del informe.

El controlador clasifica únicamente el estado general de cada navegación:
carga, autenticación externa, login de JIRA o documento del origen autorizado.
Solo en este último caso ejecuta el fuente versionado
`modules/jira/assets/extractor.js`. La protección por identificador de modal
evita inyecciones duplicadas en un mismo documento.

## Uso

1. Ejecuta `python desktop.py` desde el entorno virtual del proyecto.
2. Selecciona **JIRA · Incurridos** en la Toolbox. La ventana integrada aparece
   automáticamente y sin botones hacia navegadores externos.
3. Inicia sesión si JIRA o el proveedor corporativo de identidad lo solicitan.
4. Al volver a JIRA, espera a que aparezca el formulario **Incurridos BBVA**.
5. Elige fechas y proyectos, genera el informe y descarga el CSV.

## Consultas y consolidación

El extractor divide el rango por meses naturales. Las peticiones se realizan
secuencialmente contra el endpoint de ScriptRunner usando la sesión del propio
WebView2. Los fallos parciales se muestran y las demás consultas continúan. No
se registran en disco respuestas ni datos personales.

## CSV

El fichero se denomina `Incurridos_BBVA_FECHA_DESDE_FECHA_HASTA.csv`. Usa UTF-8
con BOM, punto y coma como separador y todos los campos entre comillas. Añade
`PKEY`, `Año`, `Mes` y `Año-Mes` antes de las columnas originales de JIRA.

## Limitaciones

- Se requiere Windows, Microsoft Edge WebView2, acceso a la red corporativa y
  permisos válidos en JIRA.
- La validación automatizada usa URLs y respuestas sintéticas; nunca consulta
  datos reales.
- Si cambia el origen, el contrato del endpoint o la columna de fecha, debe
  publicarse una nueva versión controlada del extractor.
- El arranque directo con `streamlit run app.py` sigue siendo útil para los demás
  módulos, pero no dispone del navegador JIRA integrado.
