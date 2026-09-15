# Build & Package Management

Los archivos `package.json` y `package-lock.json` son esenciales para la gestión de dependencias y configuración de proyectos en entornos basados en npm (Node Package Manager). Aunque estos archivos no se encontraron en el sistema, su propósito y relación son clave para el desarrollo.

## package.json
Este archivo define la configuración del proyecto, incluyendo:
- Dependencias instaladas (`dependencies`)
- Herramientas de desarrollo (`devDependencies`)
- Scripts personalizados para tareas como compilación o pruebas
- Información del proyecto (nombre, versión, autor)

**Ejemplo:**
```json
{
  "name": "mi-proyecto",
  "version": "1.0.0",
  "scripts": {
    "build": "webpack --mode production",
    "start": "node index.js"
  },
  "dependencies": {
    "express": "^4.18.2"
  }
}
```

## package-lock.json
Este archivo锁定了 exactas versiones de las dependencias para garantizar consistencia entre entornos. Es generado automáticamente al ejecutar `npm install` y asegura que todas las dependencias se instalen con las mismas versiones especificadas.

**Relación con package.json:**
- `package-lock.json` se basa en la información de `package.json` pero agrega detalles de versiones exactas y árboles de dependencias.

## Uso típico
1. **Instalación de dependencias:**  
   ```bash
   npm install
   ```
   Crea automáticamente `package-lock.json` si no existe.

2. **Ejecución de scripts:**  
   ```bash
   npm run build
   npm start
   ```

Estos archivos son fundamentales para reproducir el entorno de desarrollo y producción de manera consistente. Su ausencia puede indicar un proyecto sin gestión de dependencias formal o un setup personalizado.