# JIRA · Incurridos y Aplantillado

El módulo ejecuta la extracción dentro de una ventana Edge/WebView2 administrada
por Delivery Manager Toolbox. Tras el login corporativo, el extractor se inyecta
únicamente en el origen JIRA autorizado y permite seleccionar fechas y PKEYS.

## Flujo

```text
JIRA → Extraer → Descargar CSV
JIRA → Extraer → Aplantillado
```

Al finalizar una extracción válida aparecen **Descargar CSV** y **Aplantillado**
en el popup. No es necesario guardar ni volver a cargar el CSV. Una nueva
extracción descarta el resultado anterior antes de comenzar.

## Transferencia y modelo en memoria

`extractor.js` envía a la API expuesta por pywebview un mensaje estructurado con
tipo `jira_extraction_completed`, fechas, PKEYS, cabecera y filas completas. El
host valida y convierte el mensaje en un único `ExtractionResult` con:

- intervalo y PKEYS;
- cabecera y valores originales, necesarios para reproducir el CSV;
- persona, fecha, horas, tarea, comentario y PKEY normalizados para análisis.

La normalización deriva de las columnas reales empleadas por el extractor y el
legacy: `Name`, `Date`, `Hours`, `Sub-Task Summary` o `Summary`, y `Comment`. Los
nombres solo se normalizan mediante trim, espacios y comparación case-insensitive;
no hay fuzzy matching.

El host mantiene ese resultado en un almacén protegido por lock. Streamlit lo
recupera una sola vez por revisión a través del canal local `127.0.0.1`, protegido
con el token efímero del proceso. No se usan Downloads, portapapeles, scraping ni
archivos temporales. El CSV y Aplantillado consumen el mismo objeto.

## Aplantillado

La implementación actual porta la lógica de
`legacy/facturacion_anterior_agrupado_persona.html` sin depender de ese fichero en
runtime. Ofrece:

- selección de persona activa y año disponible;
- vista mensual detallada y año completo;
- agrupación por persona y fecha;
- horas, tareas y comentarios por día;
- métricas de horas totales y días por categoría;
- detalle de fechas de cada categoría;
- lunes-viernes sin registros como faltantes.

La prioridad de clasificación se conserva: Vacation/Vacaciones, Festive/Festivo,
Compensated/Compensado, Staff/Augmentation y Otros. Los fines de semana vacíos no
son faltantes. Esta regla está encapsulada en `is_missing_workday()` para admitir
un calendario laboral futuro.

## Configuración de personas

La primera extracción detecta todos los nombres únicos y los añade activados. En
extracciones posteriores se aplica la unión entre la configuración existente y
los nuevos nombres; nunca se eliminan automáticamente personas ausentes.

La interfaz permite activar, desactivar, añadir, eliminar, restaurar las personas
detectadas, guardar, importar y exportar. El almacenamiento local es
`config/jira_people.json`, ignorado por Git. Su formato es:

```json
{
  "version": 1,
  "people": [
    {"name": "Persona de ejemplo", "enabled": true}
  ]
}
```

Se rechazan JSON inválidos, versiones desconocidas, nombres vacíos, estados no
booleanos y duplicados que solo difieren por espacios o mayúsculas. Una
configuración dañada no bloquea una nueva extracción: se reconstruye a partir de
las personas detectadas.

## Privacidad y persistencia

- WebView2 conserva su perfil en
  `%LOCALAPPDATA%\DeliveryManagerToolbox\WebView2`, fuera del repositorio.
- Python no lee ni registra credenciales, cookies o tokens JIRA.
- Los registros extraídos solo viven en memoria y no generan históricos.
- La única persistencia funcional nueva es la configuración de personas.
- El HTML legacy permanece como referencia histórica y no se carga en producción.

## Limitaciones

- Se requiere Windows, Edge WebView2, red corporativa y permisos JIRA.
- El año completo conserva el comportamiento legacy: cualquier lunes-viernes sin
  registros en ese año se cuenta inicialmente como faltante, incluso fuera del
  intervalo extraído.
- Los tests usan exclusivamente datos sintéticos y no conectan con JIRA.
- `streamlit run app.py` permite administrar personas y ver el estado vacío, pero
  la extracción integrada requiere `python desktop.py`.
