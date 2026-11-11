# People 360

People 360 ofrece una vista operativa por Supervisor N1 para revisar cobertura de bitácoras, antigüedad del seguimiento y reuniones. La interfaz y las reglas de negocio consumen un único modelo normalizado y no acceden directamente a SharePoint.

## Fuente y esquema observado

La ubicación y el nombre del fichero se configuran en `config/settings.toml`. El libro inspeccionado contiene:

- `Empleados`: `numeroEmpleado`, `nombre`, `email`, `tecnologia`, `tecnologiaCliente`, `ubicacion`, `jobRole`, `segmento`, fechas de experiencia e incorporación, datos de auditoría, `area`, `cliente`, `servicio`, `equipo`, `manager_oficina`, `supervisor_n1` a `supervisor_n12`, vigencia, `estado` y `usuarioTeams`.
- `Bitacoras`: `numeroEmpleado`, `nombre`, `tipo`, `fecha`, `bitacora_empleado`, `bitacora_supervisor`, `objetivos`, `desempeno`, `satisfaccion` y `compromiso`.
- `Historico reuniones`: `numeroEmpleado`, `nombre`, `fechaSolicitud`, `estado`, `fechaGestion` y `gestionadaPor`.

La referencia funcional sitúa `empleados.xlsx` en `Documentos compartidos/General/PowerBI/Dashboard People 360`. Durante la inspección se encontró el fichero actualizado bajo `General/PowerBI/Evaluaciones` y un dashboard distinto en la carpeta de referencia. Por eso la carpeta y el fichero son parámetros, no constantes de código.

## Cálculos

Una bitácora es válida cuando tiene identificador de empleado, tipo reconocido (`empleado` o `supervisor`), fecha interpretable y contenido en el campo correspondiente. Las coberturas no existen como porcentajes fiables en el libro, por lo que se calculan por equipo:

```text
cobertura = empleados con al menos una bitácora válida del tipo / empleados del Supervisor N1 × 100
```

La cobertura de empleado y la de supervisor se mantienen separadas. Un equipo sin denominador se presenta como `Sin datos`. La prioridad usa exclusivamente la cobertura del supervisor: más de 80 % es `OK`, de 60 % a 80 % incluidos es `Media` y menos de 60 % es `Crítica`.

La alerta de antigüedad compara la última bitácora válida del supervisor con la fecha de consulta. Solo se activa cuando han transcurrido más de 60 días; una persona sin fecha se distingue como `Sin fecha` y no se convierte artificialmente en alerta.

## Arquitectura

- `models.py`: entidades normalizadas, métricas y resultados.
- `repository.py`: puerto `People360Repository`, adaptador XLSX y contrato binario para SharePoint.
- `service.py`: cobertura, prioridades, alertas y agregación.
- `filters.py`: búsqueda y filtros puros.
- `export.py`: informe XLSX de la vista filtrada; es el punto de extensión para Teams.
- `ui.py`: estado de sesión, navegación Supervisor → Empleado y presentación Streamlit.

El adaptador `SharePointExcelRepository` recibe un `SharePointBinaryClient`; no conoce tokens ni autenticación. El proyecto aún no dispone de un cliente Microsoft 365 reutilizable en runtime. Hasta incorporarlo, se puede cargar un XLSX desde la pantalla o configurar una fuente local para desarrollo:

```powershell
$env:PEOPLE360_SOURCE = "local"
$env:PEOPLE360_LOCAL_FILE = "C:\ruta\temporal\empleados.xlsx"
streamlit run app.py
```

La carga local no es la integración definitiva. Los datos permanecen en memoria de sesión, `Actualizar datos` invalida ese estado y no se registra el contenido de celdas.

## Exportación

`Exportar seguimiento` genera el nombre configurado y respeta supervisor, búsqueda, prioridad y filtro de equipo activos. La hoja `Seguimiento` contiene Supervisor N1, empleado, ambas coberturas, prioridad, última bitácora de supervisor, días transcurridos, alerta y reunión pendiente. No se realiza todavía ningún envío a Teams.
