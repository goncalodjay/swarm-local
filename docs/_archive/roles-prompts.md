# Roles & Prompts

Los archivos en las carpetas `swarmforge/roles` y `template/swarmforge/roles` definen los **roles** y **prompts** utilizados por el sistema para gestionar interacciones y comportamientos específicos. Estos archivos suelen contener configuraciones que determinan cómo se ejecutan los agentes o procesos dentro del entorno.

### Propósito
- **Roles**: Describen la función o responsabilidad de un agente dentro del sistema (ej.: "Analista de datos", "Asistente de soporte").
- **Prompts**: Son instrucciones o plantillas que guían la generación de respuestas por parte de modelos de lenguaje o agentes.

### Estructura típica
Un archivo de rol podría verse así:
```yaml
role:
  name: "Analista de datos"
  description: "Procesa y analiza datos para generar insights."
  instructions:
    - "Revisa los datos ingresados."
    - "Identifica patrones o anomalías."
    - "Presenta resultados en un formato claro."
```

### Relación entre archivos
- Los roles se vinculan a prompts específicos para definir cómo debe actuar un agente en escenarios concretos.
- Los prompts suelen ser reutilizados por múltiples roles, manteniendo la coherencia en la interacción con usuarios o sistemas.

### Ejemplo de uso
Si un usuario solicita un análisis de datos, el sistema selecciona el rol "Analista de datos" y ejecuta el prompt asociado para generar la respuesta adecuada.

Nota: Los archivos mencionados no fueron encontrados en los directorios especificados, lo que indica que su ubicación o existencia podría variar según la implementación actual.