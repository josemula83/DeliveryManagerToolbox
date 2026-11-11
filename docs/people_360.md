# People 360

People 360 ofrece una vista operativa por Supervisor N1 para revisar cobertura de bitácoras, antigüedad del seguimiento y reuniones. La interfaz y las reglas de negocio consumen un único modelo normalizado y no acceden directamente a SharePoint.

## Fuente y esquema observado

La ubicación y el nombre del fichero se configuran en `config/settings.toml`. La fuente predeterminada es SharePoint y no requiere seleccionar ni descargar manualmente el libro. El libro inspeccionado contiene:

- `Empleados`: `numeroEmpleado`, `nombre`, `email`, `tecnologia`, `tecnologiaCliente`, `ubicacion`, `jobRole`, `segmento`, fechas de experiencia e incorporación, datos de auditoría, `area`, `cliente`, `servicio`, `equipo`, `manager_oficina`, `supervisor_n1` a `supervisor_n12`, vigencia, `estado` y `usuarioTeams`.
- `Bitacoras`: `numeroEmpleado`, `nombre`, `tipo`, `fecha`, `bitacora_empleado`, `bitacora_supervisor`, `objetivos`, `desempeno`, `satisfaccion` y `compromiso`.
- `Historico reuniones`: `numeroEmpleado`, `nombre`, `fechaSolicitud`, `estado`, `fechaGestion` y `gestionadaPor`.

La ruta configurada es `BBVACentros / Documentos compartidos / General / PowerBI / Dashboard People 360 / empleados.xlsx`. Host, site, biblioteca, carpeta y fichero son parámetros independientes, no URLs dispersas en la interfaz.

## Cálculos

Una bitácora es válida cuando tiene identificador de empleado, tipo reconocido (`empleado` o `supervisor`), fecha interpretable y contenido en el campo correspondiente. Las coberturas no existen como porcentajes fiables en el libro, por lo que se calculan por equipo:

```text
cobertura = empleados con al menos una bitácora válida del tipo / empleados del Supervisor N1 × 100
```

La cobertura de empleado y la de supervisor se mantienen separadas. Un equipo sin denominador se presenta como `Sin datos`. La prioridad usa exclusivamente la cobertura del supervisor: más de 80 % es `OK`, de 60 % a 80 % incluidos es `Media` y menos de 60 % es `Crítica`.

La alerta de antigüedad compara la última bitácora válida del supervisor con la fecha de consulta. Solo se activa cuando han transcurrido más de 60 días; una persona sin fecha se distingue como `Sin fecha` y no se convierte artificialmente en alerta.

## Arquitectura

- `models.py`: entidades normalizadas, métricas y resultados.
- `repository.py`: puerto `People360Repository`, normalizador XLSX y fuente local explícita.
- `sharepoint.py`: autenticación Microsoft Entra, cliente Microsoft Graph y `SharePointPeople360Repository`.
- `service.py`: cobertura, prioridades, alertas y agregación.
- `filters.py`: búsqueda y filtros puros.
- `export.py`: informe XLSX de la vista filtrada; es el punto de extensión para Teams.
- `ui.py`: estado de sesión, navegación Supervisor → Empleado y presentación Streamlit.

## Autenticación y SharePoint

`AzureIdentityTokenProvider` utiliza `InteractiveBrowserCredential`, flujo de autorización delegado con PKCE. Antes de consultar SharePoint, la pantalla muestra `Iniciar sesión con Microsoft 365`. Al pulsarlo, se reutiliza una sesión corporativa válida o se abre el navegador para completar el acceso. Solo después se habilita la carga automática del dashboard. El token se conserva en la caché de Azure Identity cifrada por el mecanismo de protección del usuario de Windows; nunca se escribe en configuración, estado de Streamlit, logs o el repositorio.

Es necesario registrar una aplicación pública en Microsoft Entra, habilitar el redirect URI de aplicaciones móviles/escritorio `http://localhost` y conceder el permiso delegado de Microsoft Graph `Sites.Read.All`. Este permiso es el mínimo utilizado por la implementación porque resolver un site por su ruta requiere `Sites.Read.All`. El usuario autenticado también debe tener acceso al site.

El identificador de aplicación no es un secreto y se suministra sin versionarlo:

```powershell
$env:PEOPLE360_GRAPH_CLIENT_ID = "identificador-de-la-aplicacion-registrada"
# Opcional si se desea fijar el tenant en lugar de `organizations`
$env:PEOPLE360_GRAPH_TENANT_ID = "identificador-del-tenant"
python desktop.py
```

El cliente usa Microsoft Graph v1.0 en esta secuencia:

1. `GET /sites/{hostname}:/{site-path}` para localizar el site.
2. `GET /sites/{site-id}/drives` para localizar `Documentos compartidos`.
3. `GET /drives/{drive-id}/root:/{folder}/{file}` para obtener los metadatos.
4. Descarga mediante la URL preautenticada devuelta por Graph, sin reenviar el token a ese host.

El contenido XLSX se procesa en memoria. `Actualizar datos` descarta el dashboard de la sesión, vuelve a Graph y recalcula todas las métricas. La UI diferencia errores de autenticación, permisos, fichero inexistente y fallos temporales.

## Desarrollo local

La fuente local no aparece en la pantalla y solo se habilita explícitamente:

```powershell
$env:PEOPLE360_SOURCE = "local"
$env:PEOPLE360_LOCAL_FILE = "C:\ruta\temporal\empleados.xlsx"
streamlit run app.py
```

No existe uploader en el flujo normal. Los datos permanecen en memoria de sesión y no se registra el contenido de celdas.

## Exportación

`Exportar seguimiento` genera el nombre configurado y respeta supervisor, búsqueda, prioridad y filtro de equipo activos. La hoja `Seguimiento` contiene Supervisor N1, empleado, ambas coberturas, prioridad, última bitácora de supervisor, días transcurridos, alerta y reunión pendiente. No se realiza todavía ningún envío a Teams.
