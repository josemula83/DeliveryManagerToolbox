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
- La primera fase elimina gestores salvo excepciones, agrega horas por cuenta y aplica la prioridad sin gestión, fija y 5 %. La segunda fase reparte las horas en escalones de 0,5 h entre las Personas en Gestión según su capacidad relativa, con tolerancia objetivo de ±10 %, orden determinista, cuadre exacto y auditoría independiente por hoja. La tercera fase aplica primero la tarifa de bloque configurada y, en su ausencia, la tarifa máxima detectada para la cuenta en esa misma hoja; consolida gestor-cuenta, calcula importes y conserva las horas del reparto. La exportación final sigue pendiente. No implementes ni asumas esa regla sin una especificación expresa.
- Los demás módulos actuales son marcadores de posición. No asumas reglas de negocio ni formatos de ficheros sin una especificación expresa.

