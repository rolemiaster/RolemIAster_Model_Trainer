# Guía para Colaboradores - ABAFE Models Trainer

Esta guía detalla los pasos necesarios para configurar el entorno de desarrollo y poder colaborar en el proyecto **ABAFE Models Trainer**.

## 🖥️ Requisitos del Sistema

- **Sistema Operativo:** Windows 10/11 (64-bit).
- **Hardware:**
  - **GPU NVIDIA:** Se recomienda encarecidamente una GPU NVIDIA con al menos **8GB de VRAM** (arquitectura Ampere o superior recomendada para soporte óptimo de Flash Attention).
  - **RAM:** 16GB mínimo (32GB recomendado).

## 🛠️ Software Necesario (Core)

Antes de clonar el repositorio, asegúrate de tener instalados los siguientes programas:

1.  **Python 3.12.x:**
    - [Descargar Python 3.12](https://www.python.org/downloads/windows/).
    - Asegúrate de marcar la opción "Add Python to PATH" durante la instalación.
2.  **Visual Studio 2022:**
    - Se requiere para compilar extensiones de C++ y Triton kernels.
    - [Descargar Visual Studio 2022 Community](https://visualstudio.microsoft.com/vs/community/).
    - **Carga de trabajo obligatoria:** "Desarrollo para el escritorio con C++" (Desktop development with C++).
3.  **NVIDIA CUDA Toolkit 12.1:**
    - El proyecto está fijado a CUDA 12.1 para compatibilidad con los binarios de PyTorch.
    - [Descargar CUDA Toolkit 12.1](https://developer.nvidia.com/cuda-12-1-0-download-archive).
4.  **Git:**
    - Para la gestión del código.
    - [Descargar Git](https://git-scm.com/download/win).

## 🚀 Configuración del Entorno de Desarrollo

El proyecto incluye un script de automatización que gestiona la creación del entorno virtual y la instalación de dependencias complejas.

### 1. Clonar el Repositorio
```bash
git clone https://github.com/rolemiaster/RolemIAster_Model_Trainer.git
cd RolemIAster_Model_Trainer
```

### 2. Inicializar el Entorno (Modo DEV)
Ejecuta el archivo `run_trainer.bat`. Este script realizará lo siguiente:
- Detectará tu instalación de Visual Studio 2022.
- Verificará que tengas Python 3.12.
- Creará un entorno virtual en la carpeta `.venv`.
- Instalará todas las dependencias de `requirements.txt`.
- Configurará las variables de entorno para CUDA.

### 3. Dependencias Específicas
El proyecto utiliza versiones fijadas para garantizar la estabilidad. No utilices `--upgrade` al instalar paquetes manualmente.

- **PyTorch:** El script `run_trainer.bat` está configurado para manejar la instalación de Torch con soporte CUDA.
- **Triton (Windows):** Se instala una versión específica compatible con Windows (`triton-windows==3.6.0.post25`).
- **Unsloth:** Optimizado para entrenamiento rápido.

## 📦 Compilación y Build

Si deseas generar el ejecutable del programa para distribución:
1.  Asegúrate de haber ejecutado `run_trainer.bat` al menos una vez correctamente.
2.  Ejecuta `build.bat`. Esto utilizará `PyInstaller` y `build_trainer.py` para generar la carpeta de distribución en el directorio `dist`.

## ⚠️ Solución de Problemas Comunes

- **"No se encuentra el compilador MSVC":** Revisa que Visual Studio 2022 esté instalado y que la ruta en `run_trainer.bat` (línea 13) coincida con tu ubicación de instalación.
- **"CUDA_HOME no encontrado":** Verifica que CUDA 12.1 esté en `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1`. El script intenta forzar esta ruta por defecto.
- **Errores de Memoria (OOM):** Si al probar el entrenador recibes errores de memoria, intenta reducir el `Batch Size` a 1 o el `LoRA Rank` a 8 en la interfaz del GUI.

## 🔬 Banco de Pruebas
Si realizas cambios en el motor de entrenamiento o en las reglas, utiliza la pestaña **Test Bench** del GUI para validar que el modelo sigue funcionando correctamente bajo las reglas definidas en `reglas_base.md`.
