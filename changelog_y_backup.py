import sys
import os
import argparse
import re
import subprocess
import datetime
import shutil
import fnmatch

# --- Funciones de prepend_to_file.py ---
def prepend_text_to_file(text_to_prepend, target_file_path):
    try:
        existing_content = ""
        if os.path.exists(target_file_path):
            with open(target_file_path, 'r', encoding='utf-8') as f_target:
                existing_content = f_target.read()
        
        with open(target_file_path, 'w', encoding='utf-8') as f_target:
            f_target.write(text_to_prepend)
            if text_to_prepend and \
               not text_to_prepend.endswith(('\n', '\r\n')) and \
               existing_content:
                f_target.write('\n')
            f_target.write(existing_content)
        print(f"Successfully prepended text to '{target_file_path}'")
        return True
    except Exception as e:
        print(f"Error in prepend_text_to_file: {e}")
        return False

# --- Funciones para gestionar versionCode en build.gradle.kts ---
def get_version_code_from_gradle(gradle_file_path):
    """
    Lee el versionCode actual de lemuroid-app/build.gradle.kts.
    Retorna el entero del versionCode o None si no lo encuentra.
    """
    try:
        with open(gradle_file_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Busca el patrón versionCode = XXX
        match = re.search(r"versionCode\s*=\s*(\d+)", content)
        if match:
            return int(match.group(1))
    except FileNotFoundError:
        print(f"ERROR: Gradle file '{gradle_file_path}' not found.")
    except Exception as e:
        print(f"ERROR: An exception occurred while reading '{gradle_file_path}': {e}")
    return None

def update_version_code_in_gradle(gradle_file_path, new_version_code):
    """
    Actualiza el versionCode en lemuroid-app/build.gradle.kts.
    Retorna True si tuvo éxito, False en caso contrario.
    """
    try:
        with open(gradle_file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Reemplaza el versionCode existente
        new_content, count = re.subn(
            r"(versionCode\s*=\s*)\d+",
            rf"\g<1>{new_version_code}",
            content
        )
        
        if count == 0:
            print(f"ERROR: No se encontró 'versionCode' en '{gradle_file_path}'.")
            return False
        
        with open(gradle_file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        
        print(f"Actualizado versionCode a {new_version_code} en '{gradle_file_path}'.")
        return True
    except Exception as e:
        print(f"ERROR al actualizar versionCode: {e}")
        return False

# --- Funciones de backup_project.py (original) ---
def get_current_version_from_changelog(changelog_file="changelog.md"):
    try:
        with open(changelog_file, "r", encoding="utf-8") as f:
            for line in f: # Busca el formato específico del título de la entrada
                match = re.search(r"-\s*(?:(?:Alfa|Beta|Final)_)?v(\d+)\s*$", line.strip(), re.IGNORECASE)
                if match:
                    return int(match.group(1))
            # Fallback si no encuentra el formato exacto en la línea de título
            f.seek(0) 
            for line in f:
                match = re.search(r"(?:(?:Alfa|Beta|Final)_)?v(\d+)", line.strip(), re.IGNORECASE)
                if match:
                    return int(match.group(1))
    except FileNotFoundError:
        print(f"INFO: Changelog file '{changelog_file}' not found. Assuming version 0.")
        return 0
    except Exception as e:
        print(f"ERROR: An exception occurred while reading '{changelog_file}': {e}")
    return 0

def get_latest_log_entry_details(changelog_file):
    """
    Lee la entrada de log más reciente y devuelve su título, descripción y número de versión.
    Retorna (título, descripción, versión_int) o (None, None, 0) si no se encuentra o hay error.
    """
    try:
        with open(changelog_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Regex para capturar la entrada más reciente (la primera en el archivo)
        # Asume que cada entrada está separada por '****************************************************************************************************'
        # y que el título y la versión están en la segunda línea de la cabecera.
        # Y la descripción está después de "- Description:\n"
        entry_pattern = re.compile(
            r"^\*+\n"  # Línea de asteriscos
            r"^(?P<date_time_title_line>.*?-\s*(?P<title>.*?)\s*-\s*v(?P<version>\d+))\s*\n"  # Línea de título con fecha, título y versión
            r"^\*+\n"  # Línea de asteriscos
            r"(?:.*?-\s*Description:\n\s*(?P<description>.*?)\n\n)?",  # Bloque de descripción (opcional)
            re.MULTILINE | re.DOTALL
        )
        match = entry_pattern.search(content)

        if match:
            title = match.group("title").strip()
            version_str = match.group("version")
            description_block = match.group("description")
            description = description_block.strip() if description_block else ""
            return title, description, int(version_str)
            
    except FileNotFoundError:
        return None, None, 0
    except Exception as e:
        print(f"Error parsing latest log entry: {e}")
    return None, None, 0


def read_exclusions_from_file(file_path="changelog_y_backup_archivosignorados.txt"):
    if not os.path.exists(file_path):
        return []
    with open(file_path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

def create_backup(version_num_str, script_name_to_exclude="changelog_y_backup.py", include_venv=False, include_media=False):
    project_root = os.path.dirname(os.path.abspath(__file__))
    backup_dir_name = "Backups"
    backup_dir_path = os.path.join(project_root, backup_dir_name)

    if not os.path.exists(backup_dir_path):
        os.makedirs(backup_dir_path, exist_ok=True)
        print(f"Creando carpeta de backups: {backup_dir_path}")

    print(f"Versión para el backup: v{version_num_str}") # Usa el string formateado
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    # Obtener dinámicamente el nombre del proyecto desde el nombre de la carpeta
    project_name = os.path.basename(project_root)
    # Asegurarse que version_num_str ya tiene el formato correcto (ej. "060")
    backup_filename_base = f"{project_name}_v{version_num_str}_Backup_{timestamp}"
    backup_base_path_for_shutil = os.path.join(backup_dir_path, backup_filename_base)
    backup_filepath_zip = f"{backup_base_path_for_shutil}.zip"
    print(f"\nCreando backup: {backup_filepath_zip}")

    exclusions = [
        f"./{backup_dir_name}/*", "./temp_log_entry*.txt", "./*.pyc",
        "./__pycache__", f"./{script_name_to_exclude}",
        "./get_last_version.py", "./backup_project.py", "./prepend_to_file.py",
        "./.git/*", "./.vscode/*", "./*.sqlite-journal",
        "./ControlSolarANX/instance/*.sqlite-journal",
        "./temp_test_miner_apis.py", # Añadir el script de prueba a exclusiones
        "./dist/*", "./build/*", "./spec/*" # Excluir carpetas de compilación que causan problemas de permisos
    ]
    valid_exclusions = []
    for exclusion in exclusions:
        if exclusion.endswith("/*") or os.path.exists(os.path.join(project_root, exclusion.replace("./", ""))):
            valid_exclusions.append(exclusion)
        elif "*" not in exclusion: # Si no es un patrón glob y no existe, no lo incluimos
             pass


    try:
        import zipfile
        
        exclusions_from_file = read_exclusions_from_file()
        
        # Lógica para manejar la exclusión del venv
        final_exclusions = list(exclusions_from_file)
        venv_patterns = ['.venv', '.venvwin7', '.venv_3.13.3']
        
        if include_venv:
            print("INFO: Incluyendo directorios de entorno virtual en el backup.")
            final_exclusions = [ex for ex in final_exclusions if ex not in venv_patterns]
        else:
            print("INFO: Excluyendo directorios de entorno virtual del backup. (Comportamiento por defecto)")
            for vp in venv_patterns:
                if vp not in final_exclusions:
                    final_exclusions.append(vp)

        with zipfile.ZipFile(backup_filepath_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(project_root):
                # Excluir directorios multimedia si se solicita
                if not include_media:
                    # Solo actuar si estamos en el directorio 'src'
                    if os.path.normpath(os.path.relpath(root, project_root)) == 'src':
                        if 'audiopack' in dirs:
                            print("INFO: Excluyendo directorio 'audiopack'.")
                            dirs.remove('audiopack')
                        if 'imagepack' in dirs:
                            print("INFO: Excluyendo directorio 'imagepack'.")
                            dirs.remove('imagepack')
                
                # Excluir directorios
                dirs[:] = [d for d in dirs if d not in final_exclusions and d not in ['Backups', '.git', '.vscode', '__pycache__', 'dist', 'build', 'spec']]
                
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, project_root)
                    
                    # Comprobar si el archivo o su ruta coincide con algún patrón de exclusión
                    is_excluded = False
                    for pattern in final_exclusions:
                        if fnmatch.fnmatch(arcname, pattern):
                            is_excluded = True
                            break
                    
                    if is_excluded:
                        continue

                    # Exclusiones adicionales que no se manejan bien con patrones
                    if file == script_name_to_exclude or file.endswith('.pyc') or file.endswith('.sqlite-journal'):
                        continue
                    if 'ModelosLLM' in root and file.endswith('.gguf'):
                        continue
                        
                    zf.write(file_path, arcname)
        
        print(f"Backup creado exitosamente: {backup_filepath_zip}")
        return True
    except Exception as e:
        print(f"ERROR al crear backup con zipfile: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Calcula una nueva versión, añade una entrada al log de cambios y crea un backup versionado del proyecto.",
        epilog="""
EJEMPLO DE USO CORRECTO:
  python changelog_y_backup.py "changelog.md" "Mi Nuevo Feature" "Descripción detallada de los cambios." --changes_list "- Cambio 1" "- Cambio 2"
  python changelog_y_backup.py --backuponly
""",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Argumentos posicionales ahora opcionales con nargs='?'
    parser.add_argument("changelog_file", nargs='?', default="changelog.md", help="Ruta al archivo de log. DEBE SER 'changelog.md' en la raíz del proyecto.")
    parser.add_argument("log_title", nargs='?', default="", help="Título para la nueva entrada del log.")
    parser.add_argument("log_description", nargs='?', default="", help="Descripción detallada de los cambios.")
    
    parser.add_argument("--changes_list", nargs='*', help="Lista de cambios específicos.")
    parser.add_argument("--known_issues", nargs='*', help="Lista de problemas conocidos.")
    parser.add_argument("--backuponly", action="store_true", help="Solo genera el backup ZIP sin modificar changelog ni version.")

    args = parser.parse_args()

    project_root = os.path.dirname(os.path.abspath(__file__))
    expected_changelog_path = os.path.join(project_root, "changelog.md")

    # Obtener versión actual (siempre necesaria para el nombre del backup)
    gradle_file_path = os.path.join(project_root, "lemuroid-app", "build.gradle.kts")
    current_gradle_version = get_version_code_from_gradle(gradle_file_path)
    current_changelog_version = get_current_version_from_changelog(args.changelog_file)
    
    if current_gradle_version is not None:
        current_version_int = current_gradle_version
        print(f"INFO: Versión base (Gradle): {current_version_int}")
    else:
        current_version_int = current_changelog_version
        print(f"INFO: Versión base (Changelog): {current_version_int}")

    NUEVAVERSION_STR_FORMATTED = f"{current_version_int:03d}"

    # --- LÓGICA BACKUP ONLY ---
    if args.backuponly:
        print("\n--- MODO BACKUP ONLY ---")
        print("Saltando creación de changelog y actualización de versión.")
        
        # Preguntas de backup
        user_input_venv = input("\n¿Desea incluir el entorno virtual en el backup? (y/N): ").lower().strip()
        include_venv_in_backup = user_input_venv == 'y'

        user_input_media = input("¿Desea incluir los archivos multimedia (audiopack, imagepack) en el backup? (y/N): ").lower().strip()
        include_media_in_backup = user_input_media == 'y'

        script_filename = os.path.basename(__file__)
        if not create_backup(NUEVAVERSION_STR_FORMATTED, script_name_to_exclude=script_filename, include_venv=include_venv_in_backup, include_media=include_media_in_backup):
            print("Fallo al crear el backup.")
            sys.exit(1)
        else:
            print(f"\nBackup (v{NUEVAVERSION_STR_FORMATTED}) completado exitosamente.")
            sys.exit(0)

    # --- LÓGICA NORMAL (CHANGELOG) ---
    
    # Validación de argumentos requeridos si NO es backup only
    if os.path.abspath(args.changelog_file) != expected_changelog_path:
        print("--- ERROR DE USO ---")
        print(f"Argumento 'changelog_file' incorrecto o no encontrado.")
        print('EJEMPLO: python changelog_y_backup.py "changelog.md" "Título" "Descripción"')
        sys.exit(1)

    if not args.log_title.strip() or not args.log_description.strip():
        print("ERROR: El título y la descripción del log son obligatorios.")
        sys.exit(1)

    # Pregunta Fase
    phase = ""
    while phase not in ['alfa', 'beta', 'final']:
        phase_input = input("Introduce la fase de desarrollo (alfa/beta/final): ").lower().strip()
        if phase_input in ['alfa', 'beta', 'final']:
            phase = phase_input
        else:
            print("Entrada inválida. Por favor, elige entre 'alfa', 'beta', o 'final'.")
    args.phase = phase

    last_title, last_desc, last_version_from_details = get_latest_log_entry_details(args.changelog_file)

    # Normalizar descripciones
    normalized_arg_desc = ' '.join(args.log_description.strip().split())
    normalized_last_desc = ' '.join(last_desc.strip().split()) if last_desc else ""

    is_duplicate_content = False
    if last_title and args.log_title.strip() == last_title and \
       normalized_arg_desc == normalized_last_desc and \
       current_version_int == last_version_from_details :
        is_duplicate_content = True
        print("INFO: La última entrada de log parece ser idéntica. No se añadirá nueva entrada.")
        NUEVAVERSION_INT = current_version_int
    else:
        NUEVAVERSION_INT = current_version_int + 1
        print(f"Versión actual detectada: v{current_version_int} -> Nueva: v{NUEVAVERSION_INT}")
        if not update_version_code_in_gradle(gradle_file_path, NUEVAVERSION_INT):
             print("ADVERTENCIA: No se pudo actualizar el versionCode en Gradle.")
    
    NUEVAVERSION_STR_FORMATTED = f"{NUEVAVERSION_INT:03d}"
    
    if not is_duplicate_content:
        # Formatear log
        now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
        phase_prefix = f"{args.phase.capitalize()}_" if args.phase != 'final' else ""
        full_version_string = f"{phase_prefix}v{NUEVAVERSION_STR_FORMATTED}"
        
        log_entry_title_line = f"{now_str} - {args.log_title} - {full_version_string}"
        
        full_log_entry = f"****************************************************************************************************\n"
        full_log_entry += f"{log_entry_title_line}\n"
        full_log_entry += f"****************************************************************************************************\n"
        full_log_entry += f"- Description:\n  {args.log_description}\n\n"
        
        if args.changes_list:
            full_log_entry += f"- Changes:\n"
            for change in args.changes_list:
                full_log_entry += f"  {change}\n"
            full_log_entry += f"\n"

        if args.known_issues:
            full_log_entry += f"- Known Issues:\n"
            for issue in args.known_issues:
                full_log_entry += f"  {issue}\n"
            full_log_entry += f"\n"
        
        if not full_log_entry.endswith('\n'):
            full_log_entry += '\n'

        print("\nEntrada de log a añadir:")
        print(full_log_entry)

        if not prepend_text_to_file(full_log_entry, args.changelog_file):
            print("Fallo al añadir la entrada al log de cambios. Abortando.")
            sys.exit(1)
    else:
        print(f"Se usará la versión existente v{NUEVAVERSION_STR_FORMATTED} para el backup.")

    # Crear backup
    user_input_venv = input("\n¿Desea incluir el entorno virtual en el backup? (y/N): ").lower().strip()
    include_venv_in_backup = user_input_venv == 'y'

    user_input_media = input("¿Desea incluir los archivos multimedia (audiopack, imagepack) en el backup? (y/N): ").lower().strip()
    include_media_in_backup = user_input_media == 'y'

    script_filename = os.path.basename(__file__)
    if not create_backup(NUEVAVERSION_STR_FORMATTED, script_name_to_exclude=script_filename, include_venv=include_venv_in_backup, include_media=include_media_in_backup):
        print("Fallo al crear el backup.")
    else:
        print(f"\nProceso de registro (v{NUEVAVERSION_STR_FORMATTED}) y backup completado exitosamente.")
