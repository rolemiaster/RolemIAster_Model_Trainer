import os
import sys
import shutil
import subprocess
import argparse
from pathlib import Path

import stat

def remove_readonly(func, path, excinfo):
    """Auxiliar para borrar archivos de solo lectura en Windows."""
    os.chmod(path, stat.S_IWRITE)
    func(path)

def run_command(command):
    """Ejecuta un comando y muestra su salida en tiempo real."""
    print(f"--- Ejecutando: {' '.join(command)} ---")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in process.stdout:
        if isinstance(line, bytes):
            try:
                print(line.decode('utf-8', errors='replace'), end='')
            except Exception:
                print(line, end='')
        else:
            print(line, end='')
    process.wait()
    if process.returncode != 0:
        print(f"--- ERROR: El comando falló con el código de salida {process.returncode} ---")
        sys.exit(1)
    print("--- Comando ejecutado exitosamente ---")

def main():
    parser = argparse.ArgumentParser(description="Script de compilación para ABAFE Models Trainer.")
    parser.add_argument("--type", choices=['zip', 'folder'], default='folder', help="Tipo de compilación: 'zip' para portable (+ archivo zip), 'folder' para solo carpeta.")
    args = parser.parse_args()

    app_name = "ABAFE_Models_Trainer"
    version = "v1.0.0"
    
    # Directorios
    root_dir = Path(__file__).parent.absolute()
    dist_dir = root_dir / "dist"
    output_dir = dist_dir / app_name
    src_dir = root_dir / "src"
    engines_dir = src_dir / "engines"
    
    # 1. Limpieza previa
    if dist_dir.exists():
        print(f"Limpiando directorio dist: {dist_dir}")
        try:
            shutil.rmtree(dist_dir, onerror=remove_readonly)
        except Exception as e:
            print(f"Advertencia: No se pudo limpiar completamente 'dist': {e}")
            print("Intentando continuar...")
    
    # 2. Compilación con PyInstaller
    print("--- Iniciando compilación con PyInstaller ---")
    
    pyinstaller_cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed", # GUI mode temporalmente a console para ver errores
        "--name", app_name,
        "--icon", "NONE", # TODO: Añadir icono si existe
        "--exclude-module", "llama_cpp", # CRÍTICO: Excluir para gestión manual dual-engine
        "--collect-all", "fla",                 # CRÍTICO: Flash Linear Attention (cargado dinámicamente)
        "--collect-all", "causal_conv1d",       # CRÍTICO: Backend Causal Conv1D (cargado dinámicamente)
        "--collect-all", "xformers",            # CRÍTICO: Optimizador de memoria Unsloth
        "--collect-all", "triton",              # CRÍTICO: Backend Triton para Nvidia/AMD
        "--add-binary", f"{root_dir / 'triton-windows' / 'python' / 'triton' / '_C' / 'libtriton.pyd'};triton/_C",  # CRÍTICO: Triton editable no siempre empaqueta libtriton.pyd
        "--collect-all", "unsloth_zoo",         # CRÍTICO: Requerido por unsloth (necesita binarios y metadata pkg)
        "--collect-all", "unsloth",             # CRÍTICO: Forzar copia de metadata pip de unsloth
        "--collect-all", "trl",                # CRÍTICO: Fuentes .py necesarios para inspect.getsource() → unsloth monkey-patches
        "--add-data", f"src/i18n;src/i18n", # Incluir traducciones
        "--add-data", "README_UI.md;.", # Incluir manual
        "src/gui.py" # Script principal
    ]
    
    run_command([str(arg) for arg in pyinstaller_cmd])
    
    # Mover resultado a la estructura deseada si no coincide (PyInstaller crea dist/ABAFE_Models_Trainer)
    # En este caso coincide con output_dir
    
    # 3. Gestión de Motores (Dual Engine)
    print("--- Configurando Motores de Inferencia (Dual Engine) ---")
    internal_engines_dir = output_dir / "_internal" / "src" / "engines"
    
    if not engines_dir.exists():
        print("ERROR: No se encontró la carpeta src/engines. Asegúrate de haber ejecutado manage_engines.py primero.")
        sys.exit(1)
        
    # Copiar carpeta engines completa a _internal/src/engines
    # Esto preserva la estructura: engines/cuda y engines/vulkan con sus DLLs aisladas
    print(f"Copiando motores desde {engines_dir} a {internal_engines_dir}...")
    shutil.copytree(engines_dir, internal_engines_dir, dirs_exist_ok=True)
    
    # 4. Copiar scripts auxiliares críticos
    print("--- Copiando scripts auxiliares ---")
    
    # Copiar run_unsloth_training.py a _internal/src/core/
    core_src = src_dir / "core"
    core_dst = output_dir / "_internal" / "src" / "core"
    os.makedirs(core_dst, exist_ok=True)
    
    shutil.copy2(core_src / "run_unsloth_training.py", core_dst / "run_unsloth_training.py")
    shutil.copy2(core_src / "preparar_dataset.py", core_dst / "preparar_dataset.py")
    
    # 5. Copiar carpetas de entrada/salida y reglas base
    input_src = root_dir / "input_model"
    input_dst = output_dir / "input_model"
    input_dst.mkdir(exist_ok=True)

    if input_src.exists():
        print(f"Copiando archivos .md desde input_model a {input_dst}...")
        for md_file in input_src.glob("*.md"):
            shutil.copy2(md_file, input_dst / md_file.name)
        
    (output_dir / "output_model").mkdir(exist_ok=True)

    # Copiar README_UI.md a la raíz del output
    readme_src = root_dir / "README_UI.md"
    if readme_src.exists():
        print(f"Copiando README_UI.md a {output_dir}...")
        shutil.copy2(readme_src, output_dir / "README_UI.md")

    # 6. Empaquetado ZIP (Opcional)
    if args.type == 'zip':
        print(f"--- Creando archivo ZIP ---")
        zip_filename = f"{app_name}_{version}_portable"
        archive_base = str(dist_dir / zip_filename)
        shutil.make_archive(archive_base, 'zip', str(output_dir))
        print(f"--- Versión portable creada en: dist/{zip_filename}.zip ---")
        
    print(f"\n=== Compilación Completada Exitosamente ===")
    print(f"Carpeta de salida: {output_dir}")

    # --- VERIFICACIÓN FINAL ---
    print("\n--- Verificando integridad del paquete ---")
    required_paths = [
        output_dir / "ABAFE_Models_Trainer.exe",
        output_dir / "README_UI.md",
        output_dir / "input_model" / "reglas_base.md",
        output_dir / "output_model",
        output_dir / "_internal" / "src" / "engines" / "engines_config.json",
        output_dir / "_internal" / "src" / "core" / "run_unsloth_training.py"
    ]
    
    missing_files: list[str] = []
    for p in required_paths:
        if not p.exists():
            missing_files.append(str(p.name))
    
    if missing_files:
        print(f"⚠️  ADVERTENCIA: Faltan archivos en el paquete: {', '.join(missing_files)}")
    else:
        print("✅ Verificación completada: El paquete parece tener todos los componentes esenciales.")

if __name__ == "__main__":
    main()
