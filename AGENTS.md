# AGENTS.md

## Propósito

Este repositorio contiene Delivery Manager Toolbox, una aplicación local y modular en Python/Streamlit. Mantén la separación entre infraestructura común y dominios funcionales.

## Reglas de arquitectura

- Coloca cada funcionalidad en su propio paquete bajo `src/delivery_manager_toolbox/modules/`.
- No importes código de un módulo desde otro módulo. Si una capacidad es realmente transversal, define un contrato pequeño en `core`.
- Mantén `application.py` limitado a composición, navegación y presentación global.
- Encapsula el acceso a Excel, APIs o sistemas externos detrás de servicios o repositorios del módulo correspondiente.
- Lee la configuración mediante `core.config`; no codifiques rutas dependientes de una máquina.
- Usa `core.logging.get_logger` en lugar de configurar handlers dentro de cada módulo.
- Un módulo nuevo debe exponer `MODULE: ModuleDefinition` desde su `__init__.py`; el descubrimiento es automático.

## Calidad

- Añade o actualiza tests para cada cambio funcional.
- Ejecuta antes de entregar: `python -m pytest` y `python -m compileall -q app.py src tests`.
- Conserva compatibilidad con Python 3.11 o superior.
- Prefiere funciones pequeñas, type hints y dependencias explícitas.
- Evita lógica de negocio dentro de componentes Streamlit.

## Seguridad

- No añadas credenciales, tokens, datos corporativos, datos personales ni ejemplos reales.
- Usa únicamente datos sintéticos en tests, documentación y fixtures.
- No registres el contenido de hojas de cálculo ni valores sensibles.
- Mantén secretos fuera de TOML y del repositorio; usa variables de entorno o un gestor de secretos cuando se incorporen integraciones.

## Alcance funcional

- Facturación tiene implementadas su arquitectura, configuración persistente validada (esquema v3, compatible con la migración desde v1 y v2), lectura jerárquica y primera fase del cálculo. La configuración, el parser y la lógica de negocio deben permanecer independientes de Streamlit.
- En Facturación, conserva los códigos de cuenta como identificadores persistidos y limita las descripciones legibles a la presentación. Las cuentas sin gestión y los bloques de tarifa de gestión son excluyentes; una cuenta con gestión fija sí puede pertenecer a un bloque de tarifa.
- Las excepciones que conservan horas originales se identifican por `employee_id + account_code`, deben proceder de asignaciones del Excel activo y se eliminan si dejan de corresponder a una Persona en Gestión o quedan huérfanas en todas las hojas del Excel activo. Cambiar de hoja no debe eliminar una excepción válida en otro mes. El antiguo booleano global no debe recuperarse ni convertirse automáticamente.
- El formato principal se interpreta secuencialmente: persona en columnas A/B y cuentas subordinadas en B con `EXT-` o `Tarifa`. El mapeo plano se conserva solo como compatibilidad interna para formatos futuros y no debe aparecer en la vista principal.
- Trata `WorkbookAnalysis` solo como metadatos del fichero. Todo cálculo debe recibir un único `SheetAnalysis` y devolver un `SheetCalculationResult` asociado a su `sheet_name`; nunca agregues horas de hojas distintas antes del cálculo. El modo multihoja debe coordinar llamadas independientes al mismo motor y conservarlas en un `WorkbookCalculationResult` por nombre de hoja.
- La primera fase elimina gestores salvo excepciones, agrega horas por cuenta y aplica la prioridad sin gestión, fija y 5 %. La segunda fase usa el calendario laboral mensual ajustado por ausencias como capacidad. Si un gestor configurado está ausente de una hoja, usa la media única de `monthly_workable_hours` de los gestores configurados presentes con calendario válido, redondeada hacia abajo a 0,5 h; las capacidades sintéticas no alimentan la media y la ausencia de referencias bloquea con `NO_REFERENCE_MANAGER_CAPACITY`. Nunca usa horas imputadas, horas de gestión ni horas facturables. Sobre esos objetivos conserva las cuotas proporcionales de V1.3 por mayores restos en escalones de 0,5 h, los gestores alfabéticos y las cuentas en orden natural. Divide cuentas al alcanzar la cuota y no aplica tolerancia ni rebalanceo. La tercera fase consolida gestor/cuenta, usa la tarifa configurada u original como base y porta la optimización global x2,5/x2/x1 con sus desempates y pasada de equidad. Conserva por separado horas reales y facturables. La exportación replica el informe por cuenta de `buildSheetXml()`; no distribuye gestión por días porque el legacy tampoco lo hacía y valida el fichero después de guardarlo.
- Resuelve las Personas en Gestión de cada hoja con la función centralizada: primero por `employee_id` y después por nombre normalizado exacto, sin fuzzy matching. Conserva la auditoría de identidad y fila. Un gestor ausente usa la media mensual de los gestores configurados presentes con capacidad de calendario válida.
- Usa `docs/facturacion_legacy_equivalence.md`, `docs/facturacion_legacy_rates.md` y `docs/facturacion_legacy_export.md` como referencias del HTML anterior. La corrección de capacidad documentada en la matriz de equivalencia prevalece sobre el `monthBase` legacy: el calendario teórico ajustado por ausencias sí interviene en la base mensual y el reparto.
- JIRA genera un marcador versionado que se ejecuta en la pestaña autenticada del usuario. Mantén el fuente legible en `modules/jira/assets/extractor.js`; no traslades las llamadas al backend, no almacenes cookies y conserva como contrato funcional `docs/jira_incurridos.md`.
- People 360 y Activity Report son marcadores de posición. No asumas reglas de negocio ni formatos de ficheros sin una especificación expresa.

