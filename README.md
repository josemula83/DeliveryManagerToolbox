# Delivery Manager Toolbox

Aplicación web local y modular para automatizar procesos habituales de Delivery Management. Facturación es el primer módulo real: ya permite preparar y conservar sus reglas, aunque el algoritmo de reparto todavía no está implementado.

## Módulos previstos

- People 360
- Jira
- Activity Report
- Facturación

## Arquitectura

```text
DeliveryMAnagerToolBox/
├── app.py                         # Punto de entrada de Streamlit
├── config/
│   ├── settings.toml              # Configuración general no sensible
│   └── facturacion.json           # Reglas persistentes de Facturación
├── logs/                          # Logs locales (ignorados por Git)
├── src/delivery_manager_toolbox/
│   ├── application.py             # Composición de la interfaz
│   ├── core/
│   │   ├── config.py              # Carga y validación de configuración
│   │   ├── logging.py             # Configuración centralizada de logs
│   │   ├── models.py              # Contratos compartidos
│   │   └── registry.py            # Descubrimiento automático de módulos
│   └── modules/
│       ├── people_360/
│       ├── jira/
│       ├── activity_report/
│       └── facturacion/
└── tests/                         # Tests automatizados
```

Cada subpaquete de `modules` publica una constante `MODULE` que implementa el contrato `ModuleDefinition`. El registro los descubre automáticamente. Así, cada funcionalidad puede mantener su propia interfaz, servicios, modelos, validaciones y tests sin acoplarse a las demás.

Las responsabilidades se separan en tres zonas:

1. `core`: infraestructura común y contratos estables, sin reglas de negocio.
2. `modules`: funcionalidades aisladas por dominio.
3. `application.py`: composición y navegación; no procesa datos.

Cuando se implemente lógica de negocio, se recomienda que cada módulo use internamente carpetas como `services/`, `domain/`, `repositories/` y `tests/`, e importe de `core` solo contratos compartidos. El acceso a Excel debe quedar detrás de servicios del módulo, usando `pandas` para transformación tabular y `openpyxl` cuando sea necesario conservar formato o manipular libros directamente.

## Requisitos

- Python 3.11 o superior
- PowerShell, Terminal o una consola equivalente

## Instalación y arranque

En PowerShell, desde la raíz del proyecto:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
streamlit run app.py
```

La aplicación abrirá normalmente `http://localhost:8501`. Para detenerla, pulsa `Ctrl+C`.

En macOS o Linux, la activación del entorno es `source .venv/bin/activate`; el resto de comandos es igual.

## Configuración

La configuración por defecto está en `config/settings.toml`. Puede utilizarse otro archivo estableciendo `DMT_CONFIG_PATH` con su ruta. Los archivos de configuración no deben contener secretos; cuando existan integraciones, las credenciales deberán suministrarse mediante variables de entorno o un gestor de secretos.

Los logs se escriben en consola y en `logs/delivery-manager-toolbox.log`. Los ficheros `.log` no se versionan.

## Módulo Facturación

El módulo permite cargar un `.xlsx` con la estructura jerárquica de horas de servicio y elegir el mes mediante su hoja. `WorkbookAnalysis` conserva únicamente metadatos globales y hojas disponibles; cada selección se interpreta como un `SheetAnalysis` independiente. El parser recorre las filas de esa única hoja en orden: una fila con identificador en A y nombre en B inicia una persona; las filas posteriores con A vacía y `EXT-` o `Tarifa` en B se convierten en asignaciones de cuenta. Las filas auxiliares como F, V, A, E o Formación se ignoran.

De cada descripción se conservan el texto original, código EXT, servicio, tarifa y tipo final cuando existe. Las horas diarias se normalizan internamente; los textos auxiliares no se convierten en horas productivas. La previsualización separa personas y asignaciones, y muestra los recuentos de personas, cuentas únicas, asignaciones y tarifas.

La pantalla permite crear, editar y eliminar:

- personas en gestión y su porcentaje objetivo entre 0 y 100 %;
- cuentas sin gestión, excluidas del futuro reparto de horas de gestión;
- cuentas con horas de gestión fija en incrementos de 0,5 h;
- bloques de cuentas con una tarifa base de gestión en €/h;
- excepciones persona-cuenta que conservan las horas originales de una Persona en Gestión para una cuenta concreta.

Las excepciones se identifican lógicamente por `employee_id + account_code`; el nombre se conserva para poder presentarlas y validar su pertenencia a Personas en Gestión. Sus cuentas se obtienen exclusivamente de las asignaciones originales de esa persona en la hoja activa, buscando primero por identificador de empleado y usando el nombre normalizado como respaldo. Al eliminar la persona configurada o cambiar a un Excel donde la combinación no exista en ninguna hoja, la excepción huérfana se elimina con aviso. Cambiar de mes conserva las excepciones válidas en otras hojas y el motor solo aplica las que existen en la hoja calculada.

Las cuentas se almacenan por su código interno aunque la interfaz muestre una descripción legible. Una cuenta sin gestión no puede pertenecer a un bloque de tarifa y una cuenta solo puede estar en un bloque; las cuentas con gestión fija sí pueden formar parte de un bloque. La configuración se valida, se carga automáticamente desde `config/facturacion.json` y puede guardarse, exportarse o importarse como JSON. El esquema actual es la versión 3; las versiones 1 y 2 se migran al cargarlas. El antiguo booleano global para conservar horas de gestores se descarta durante la migración porque no tiene equivalencia segura con las excepciones explícitas. Las comparaciones internas normalizan espacios, mayúsculas y acentos, pero se conserva el texto original para mostrarlo.

La primera fase del motor recibe obligatoriamente un `SheetAnalysis` y crea un modelo nuevo sin modificar el análisis original. Elimina las asignaciones de Personas en Gestión salvo las excepciones persona-cuenta, registra la auditoría, agrega las horas por `account_code` sin considerar tarifas y calcula las horas de gestión. El resultado `SheetCalculationResult` conserva el nombre de la hoja. Nunca se agregan asignaciones entre meses antes del cálculo. La prioridad es: cuenta sin gestión, gestión fija y, finalmente, 5 % con mínimo y redondeo inferior en escalones de 0,5 h.

La interfaz permite calcular solo la hoja seleccionada o todas las hojas compatibles. El modo multihoja crea un `WorkbookCalculationResult` y llama al mismo motor una vez por cada `SheetAnalysis`; conserva los resultados y auditorías separados por nombre de hoja. Las hojas sin personas o asignaciones reconocibles se omiten con un aviso y no impiden calcular los demás meses.

El reparto conserva las cuotas proporcionales de Facturación BBVA V1.3 documentadas en `docs/facturacion_legacy_equivalence.md`, con una corrección expresa de capacidad. La base mensual procede exclusivamente del calendario laboral teórico, de lunes a jueves 9 h y viernes 7 h, descontando vacaciones, compensados, enfermedad, formación, festivos y demás ausencias no trabajables. El porcentaje configura un peso relativo: las horas de gestión se convierten en cuotas mediante mayores restos en escalones de 0,5 h. Después se recorren gestores alfabéticamente y cuentas en orden natural, dividiendo una cuenta cuando alcanza el límite exacto de una cuota. No se aplica tolerancia ±10 % ni un rebalanceo posterior.

Cada Persona en Gestión se asocia primero por identificador de empleado y después por nombre normalizado exacto, tolerando mayúsculas, espacios, acentos y Unicode sin usar coincidencias difusas. La auditoría muestra el gestor configurado, si está presente, la persona y fila encontradas, el método de asociación, las horas teóricas, las ausencias descontadas, las horas laborables del mes y su origen. Si un gestor configurado no aparece, recibe la media de `monthly_workable_hours` de los gestores configurados presentes cuya capacidad procede del calendario, redondeada hacia abajo a 0,5 h; después se aplica su porcentaje. Las capacidades estimadas no alimentan esa media. Si no existe ninguna referencia válida se bloquea con `NO_REFERENCE_MANAGER_CAPACITY`; un gestor presente sin calendario resoluble conserva `CAPACITY_DATA_MISSING`. Nunca se usan horas imputadas en cuentas.

La fase de tarifas se ejecuta explícitamente después del reparto y toma esas asignaciones sin cambiar sus horas. Replica Facturación BBVA V1.3: el bloque configurado aporta la tarifa base y, en su ausencia, se toma la primera tarifa original de la cuenta en la hoja. Cada gestor/cuenta se consolida antes de elegir un único multiplicador x2,5, x2 o x1. La optimización minimiza la desviación de capacidad facturable y distribuye las bajadas entre gestores. Se auditan por separado horas reales de gestión, horas facturables, tarifa base, multiplicador, tarifa final e importe. Las cuentas sin tarifa producen incidencias explícitas y los datos nunca se comparten entre meses.

La exportación final replica `buildSheetXml()` de V1.3. Cada pestaña calculada se convierte en un informe por cuenta con personas originales, excepciones, asignaciones de gestión, resumen de gestores y detalle final de cuatro columnas. El legacy no repartía la gestión entre días: exportaba directamente las horas facturables y las horas reales ya validadas. El libro se genera en memoria con el sufijo `_FACTURACION.xlsx`, conserva las hojas no calculadas y se vuelve a abrir para validar horas, tarifas, importes, duplicados y nombres de pestañas antes de habilitar la descarga.

Responsabilidades internas:

- `view.py`: interfaz y estado de sesión;
- `excel_reader.py`: parser jerárquico, parsing de descripciones y normalización de horas;
- `models.py`: contratos de datos;
- `configuration.py`: modelo de configuración, operaciones y persistencia JSON;
- `validation.py`: reglas de validación y normalización;
- `business.py`: limpieza auditable, agregación de horas base y cálculo de gestión por cuenta;
- `allocation.py`: objetivos, reparto, rebalanceo y cuadre de horas entre gestores;
- `rate_optimization.py`: selección, consolidación y resumen de tarifas e importes;
- `excel_export.py`: generación y validación posterior del XLSX final;
- `exporter.py`: punto de extensión para el futuro Excel de salida.

La generación del Excel final mediante `exporter.py` sigue deliberadamente pendiente. La interfaz consume contratos estables para que esa fase futura pueda añadirse sin rediseñarla.

## Comprobaciones

```powershell
python -m pytest
python -m compileall -q app.py src tests
```

## Añadir un módulo

1. Crea un paquete dentro de `src/delivery_manager_toolbox/modules/`.
2. Define una función de renderizado sin parámetros.
3. Expón en su `__init__.py` una constante `MODULE` de tipo `ModuleDefinition` con un `key` único.
4. Añade tests del módulo.

No es necesario modificar el registro ni la navegación. Consulta los módulos existentes como plantilla.

## Seguridad y datos

Este repositorio no debe contener credenciales, tokens, datos personales, ficheros de clientes ni información corporativa. Usa datos sintéticos en tests y ejemplos. Revisa siempre los ficheros antes de versionarlos.

