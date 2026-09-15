# Testing & Acceptance

Esta sección describe la estructura y propósito de los archivos relacionados con pruebas y aceptación en el proyecto.

## Archivos Fuente Relevantes

### `acceptance/steps.ts`
Define los pasos (steps) para las pruebas de aceptación. Estos pasos representan acciones específicas que se ejecutan durante las pruebas, como interactuar con elementos de la interfaz o verificar resultados esperados.

### `acceptance/runner-adapter.ts`
Actúa como adaptador para integrar el framework de pruebas con el entorno de ejecución. Permite personalizar cómo se inicializan y ejecutan las pruebas, incluyendo configuraciones de entorno o manejo de excepciones.

### `features/**/*`
Contiene archivos de características (features) que describen escenarios de prueba en un lenguaje natural (como Gherkin). Estos archivos definen los requisitos funcionales del sistema y se vinculan con los pasos definidos en `steps.ts`.

### `vendor/acceptance-pipeline-specification/**/*`
Proporciona especificaciones externas o plantillas para la definición de pipelines de aceptación. Estos archivos pueden incluir reglas, formatos o configuraciones reutilizables para garantizar consistencia en las pruebas.

## Relación entre Archivos
- Los archivos en `features/**/*` se vinculan con `steps.ts` mediante la definición de pasos que implementan las acciones descritas en los escenarios.
- `runner-adapter.ts` coordina la ejecución de las pruebas definidas en `features/**/*`, utilizando el adaptador para integrar con herramientas externas.
- `vendor/acceptance-pipeline-specification/**/*` ofrece plantillas o reglas que guían la estructura de los archivos de características y pasos.

## Ejemplo de Uso
```ts
// acceptance/steps.ts
const { Given, When, Then } = require('@cucumber/cucumber');

Given('el usuario ingresa {string}', (username) => {
  // Lógica para ingresar un usuario
});

When('realiza una acción', () => {
  // Lógica para ejecutar la acción
});

Then('debe ver un mensaje {string}', (message) => {
  // Lógica para verificar el mensaje
});
```

```gherkin
# features/login.feature
Feature: Inicio de sesión
  Scenario: Login exitoso
    Given el usuario ingresa "usuario"
    When realiza una acción
    Then debe ver un mensaje "Bienvenido"
```