# People 360

People 360 ofrece una vista operativa por Supervisor N1 para revisar cobertura de bitácoras, antigüedad del seguimiento y reuniones. La interfaz y las reglas de negocio consumen un único modelo normalizado y no acceden directamente a SharePoint.

## Carga y esquema observado

La pantalla permite seleccionar o arrastrar el Excel corporativo. El contenido se procesa en memoria y se descarta al retirar el fichero o cerrar la sesión. No se requiere autenticación Microsoft 365 ni se mantiene una copia local. El libro esperado contiene:

- `Empleados`: `numeroEmpleado`, `nombre`, `email`, `tecnologia`, `tecnologiaCliente`, `ubicacion`, `jobRole`, `segmento`, fechas de experiencia e incorporación, datos de auditoría, `area`, `cliente`, `servicio`, `equipo`, `manager_oficina`, `supervisor_n1` a `supervisor_n12`, vigencia, `estado` y `usuarioTeams`.
- `Bitacoras`: `numeroEmpleado`, `nombre`, `tipo`, `fecha`, `bitacora_empleado`, `bitacora_supervisor`, `objetivos`, `desempeno`, `satisfaccion` y `compromiso`.
- `Historico reuniones`: `numeroEmpleado`, `nombre`, `fechaSolicitud`, `estado`, `fechaGestion` y `gestionadaPor`.

El nombre del fichero no está hardcodeado, pero debe ser `.xlsx` y contener las tres hojas requeridas. Si el formato es incorrecto, la interfaz muestra un error comprensible sin traza técnica.

## Cálculos

Una bitácora es válida cuando tiene identificador de empleado, tipo reconocido (`empleado` o `supervisor`), fecha interpretable y contenido en el campo correspondiente. Las coberturas no existen como porcentajes fiables en el libro, por lo que se calculan por equipo:

```text
cobertura = empleados con al menos una bitácora válida del tipo / empleados del Supervisor N1 × 100
```

La cobertura de empleado y la de supervisor se mantienen separadas. Un equipo sin denominador se presenta como `Sin datos`. La prioridad usa exclusivamente la cobertura del supervisor: más de 80 % es `OK`, de 60 % a 80 % incluidos es `Media` y menos de 60 % es `Crítica`.

La alerta de antigüedad compara la última bitácora válida del supervisor con la fecha de consulta. Solo se activa cuando han transcurrido más de 60 días; una persona sin fecha se distingue como `Sin fecha` y no se convierte artificialmente en alerta.

## Arquitectura

- `models.py`: entidades normalizadas, métricas y resultados.
- `repository.py`: puerto `People360Repository`, lectura y normalización del XLSX cargado.
- `service.py`: cobertura, prioridades, alertas y agregación.
- `filters.py`: búsqueda y filtros puros.
- `export.py`: informe XLSX de la vista filtrada; es el punto de extensión para Teams.
- `ui.py`: estado de sesión, navegación Supervisor → Empleado y presentación Streamlit.

El dashboard se recalcula al cargar un fichero diferente o pulsar `Volver a procesar Excel`. La sesión conserva únicamente el modelo calculado y una huella SHA-256 para detectar cambios; no duplica ni escribe el XLSX en disco.

## Exportación

`Exportar seguimiento` genera el nombre configurado y respeta supervisor, búsqueda, prioridad y filtro de equipo activos. La hoja `Seguimiento` contiene Supervisor N1, empleado, ambas coberturas, prioridad, última bitácora de supervisor, días transcurridos, alerta y reunión pendiente. No se realiza todavía ningún envío a Teams.
