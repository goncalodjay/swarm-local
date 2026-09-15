# Logging & Monitoring

Los archivos de registro en el directorio `tui/.swarmforge/logs/` son utilizados para almacenar información de diagnóstico y eventos generados por la aplicación TUI (Text User Interface). El archivo `tui.log` específico se encuentra en esta ubicación y contiene registros relacionados con la interacción del usuario, errores, notificaciones y otros eventos relevantes durante la ejecución de la aplicación.

## Descripción de los archivos fuente
- **`tui.log`**: Este archivo registra actividades relacionadas con la interfaz de texto de la aplicación. Incluye mensajes de error, registros de transacciones, y otros datos que pueden ser útiles para el monitoreo y depuración.  
  **Nota**: El archivo no fue encontrado en la ubicación especificada, lo que podría indicar que la aplicación aún no ha generado registros o que el path está mal configurado.

## Ejemplos de uso
1. **Verificar el contenido del log**:  
   Si el archivo existe, puedes usar comandos como `cat tui/.swarmforge/logs/tui.log` para revisar su contenido.  
   ```bash
   cat tui/.swarmforge/logs/tui.log
   ```

2. **Monitoreo en tiempo real**:  
   Usa `tail -f` para observar los registros en tiempo real mientras la aplicación está en ejecución:  
   ```bash
   tail -f tui/.swarmforge/logs/tui.log
   ```

3. **Configuración de logs**:  
   Asegúrate de que la aplicación tenga permisos de escritura en el directorio `logs` para generar registros correctamente.  

Si el archivo no existe, verifica que la aplicación haya sido ejecutada previamente, ya que los logs se generan durante su operación.