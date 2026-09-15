# User Interface (TUI)

La carpeta `tui` contiene la implementación de la interfaz de usuario basada en texto (TUI). A continuación se describe su estructura y función:

## Estructura de archivos
- **`main.ts`**: Punto de entrada principal de la aplicación. Inicializa la interfaz y maneja la lógica principal del sistema.
- **`src/**/*`**: Contiene los componentes, utilidades y módulos que conforman la interfaz. Incluye clases para manejar el renderizado de pantallas, la entrada de usuario y la comunicación con otros módulos.
- **`test/**/*`**: Almacena pruebas unitarias para validar el funcionamiento de los componentes de la interfaz.

## Relación entre archivos
- `main.ts` importa y coordina los módulos definidos en `src/**/*`.
- Los archivos en `src/**/*` se organizan en carpetas temáticas (ej.: `screens/`, `utils/`) y se importan/exportan según su funcionalidad.
- `test/**/*` contiene casos de prueba para garantizar que los componentes de la interfaz funcionen correctamente.

## Ejemplo de uso
```typescript
// Ejemplo de un componente en src/screens/main-screen.ts
export class MainScreen {
  render(): string {
    return `Menú principal:\n1. Opción 1\n2. Opción 2`;
  }
}
```

```typescript
// main.ts usa el componente
import { MainScreen } from './src/screens/main-screen';

const mainScreen = new MainScreen();
console.log(mainScreen.render());
```

> Nota: Los archivos fuente no están disponibles para revisión, por lo que la descripción se basa en una estructura típica de proyecto.