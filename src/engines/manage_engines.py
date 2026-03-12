import os
import sys
import json
import subprocess
import shutil

# Constantes
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # .../src/engines
SRC_DIR = os.path.dirname(BASE_DIR)                   # .../src
PROJECT_ROOT = os.path.dirname(SRC_DIR)               # .../ABAFE_Models_Trainer
CONFIG_FILE = os.path.join(BASE_DIR, "engines_config.json")
VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")

def load_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def get_python_executable():
    """Returns the path to the python executable in the .venv, or sys.executable if not found."""
    if os.path.exists(VENV_PYTHON):
        return VENV_PYTHON
    print("Warning: .venv python not found, using sys.executable")
    return sys.executable

def check_health():
    config = load_config()
    target_python = get_python_executable()
    print(f"--- ENGINE HEALTH CHECK (using {target_python}) ---")
    
    all_ok = True
    for engine_name, data in config.items():
        # target_dir is relative to project root usually, but let's assume config has "engines/cuda"
        target_path = os.path.join(PROJECT_ROOT, data['target_dir'])
        
        # Check folder existence
        if not os.path.exists(target_path):
            print(f"[{engine_name.upper()}] MISSING: Folder not found at {target_path}")
            all_ok = False
            continue
            
        # Check integrity (smoke test import)
        test_script = f"import sys; sys.path.insert(0, r'{target_path}'); import llama_cpp; print(f'OK: {{llama_cpp.__file__}}')"
        
        try:
            result = subprocess.run(
                [target_python, "-c", test_script],
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode == 0:
                print(f"[{engine_name.upper()}] OK: Verified import.")
            else:
                print(f"[{engine_name.upper()}] ERROR: Import failed.")
                print(f"  Stdout: {result.stdout}")
                print(f"  Stderr: {result.stderr}")
                all_ok = False
        except Exception as e:
            print(f"[{engine_name.upper()}] EXCEPTION: {e}")
            all_ok = False
            
    return all_ok

def install_engine(engine_name):
    config = load_config()
    target_python = get_python_executable()
    
    if engine_name not in config:
        print(f"Error: Unknown engine '{engine_name}'")
        return False
        
    data = config[engine_name]
    target_path = os.path.join(PROJECT_ROOT, data['target_dir'])
    
    print(f"--- INSTALLING {engine_name.upper()} ENGINE ---")
    print(f"Python: {target_python}")
    print(f"Target: {target_path}")
    print(f"Description: {data['description']}")
    
    # 1. Clean target if exists
    if os.path.exists(target_path):
        print("Cleaning existing directory...")
        try:
            shutil.rmtree(target_path)
        except Exception as e:
            print(f"Warning: Could not fully clean directory: {e}")
            
    os.makedirs(target_path, exist_ok=True)
    
    # 2. Prepare Environment
    env = os.environ.copy()
    
    # CRITICAL FIX for Windows/CMake builds:
    # Explicitly tell CMake where the Python executable AND LIBRARY are.
    # scikit-build-core sometimes fails to find it in venvs, specially WindowsApps version.
    # Windows CMD requires double quotes, not single quotes.
    
    python_exec_path = target_python.replace("\\", "/") 
    
    # Calculate lib path dynamically
    # 1. First check if we manually copied it to .venv/libs (User Strategy)
    venv_lib_path = os.path.join(PROJECT_ROOT, ".venv", "libs", "python312.lib")
    
    # 2. Fallback to system path
    base_prefix = sys.base_prefix
    lib_name = f"python3{sys.version_info.minor}.lib"
    system_lib_path = os.path.join(base_prefix, "libs", lib_name)
    
    cmake_extra_args = f'-DPython3_EXECUTABLE="{python_exec_path}"'
    
    if os.path.exists(venv_lib_path):
        lib_path_safe = venv_lib_path.replace("\\", "/")
        cmake_extra_args += f' -DPython3_LIBRARY="{lib_path_safe}"'
        print(f"DEBUG: Using local venv python lib at {venv_lib_path}")
    elif os.path.exists(system_lib_path):
        lib_path_safe = system_lib_path.replace("\\", "/")
        cmake_extra_args += f' -DPython3_LIBRARY="{lib_path_safe}"'
        print(f"DEBUG: Using system python lib at {system_lib_path}")
    
    # Also check for local include dir first
    venv_include_path = os.path.join(PROJECT_ROOT, ".venv", "include")
    include_path = os.path.join(base_prefix, "include")
    
    if os.path.exists(venv_include_path):
        include_path_safe = venv_include_path.replace("\\", "/")
        cmake_extra_args += f' -DPython3_INCLUDE_DIR="{include_path_safe}"'
        print(f"DEBUG: Using local venv include at {venv_include_path}")
    elif os.path.exists(include_path):
        include_path_safe = include_path.replace("\\", "/")
        cmake_extra_args += f' -DPython3_INCLUDE_DIR="{include_path_safe}"'
        print(f"DEBUG: Using system include at {include_path}")

    if 'env_vars' in data:
        for k, v in data['env_vars'].items():
            # If CMAKE_ARGS exists, append to it
            if k == "CMAKE_ARGS":
                env[k] = v + " " + cmake_extra_args
            else:
                env[k] = v
            print(f"ENV: {k}={env[k]}")
            
    # If CMAKE_ARGS wasn't in config but we have a build, ensures it's set
    if "CMAKE_ARGS" not in env and data.get('package_source') != 'local':
         env["CMAKE_ARGS"] = cmake_extra_args
         print(f"ENV: CMAKE_ARGS={env['CMAKE_ARGS']}")
            
    # 3. Build Command
    # Determine package spec
    if data.get('package_source') == 'local':
        wheel_file = data.get('local_wheel')
        wheel_path = os.path.join(PROJECT_ROOT, wheel_file)
        if not os.path.exists(wheel_path):
             print(f"❌ Error: Local wheel not found at {wheel_path}")
             return False
        package_spec = wheel_path
        print(f"Source: Local Wheel ({wheel_path})")
    elif data.get('package_source') == 'git':
        package_spec = data.get('git_url')
        if not package_spec:
            print(f"❌ Error: git_url not specified for git source")
            return False
        print(f"Source: Git ({package_spec})")
    else:
        package_spec = f"{data['package']}=={data['version']}"
        print(f"Source: PyPI ({package_spec})")
    
    cmd = [
        target_python, "-m", "pip", "install",
        package_spec,
        "--target", target_path,
        "--no-build-isolation"  # CRITICAL: Bypass scikit-build-core's internal detection
    ]
    cmd.extend(data['install_args'])
    
    # 4. Execute
    try:
        subprocess.check_call(cmd, env=env)
        print(f"✅ {engine_name.upper()} installed successfully.")
        
        # 5. POST-INSTALL CLEANUP: Remove bundled numpy to prevent conflicts
        # The project relies on a global numpy version compatible with NNCF (<2.3.0).
        # Installing with --target forces a bundled numpy (often newer) into the engine folder,
        # causing collisions. We remove it so the engine uses the global one.
        numpy_target = os.path.join(target_path, "numpy")
        if os.path.exists(numpy_target):
            print(f"🧹 Cleaning bundled numpy from {target_path} to avoid conflicts...")
            try:
                shutil.rmtree(numpy_target)
                # Also clean dist-info
                for item in os.listdir(target_path):
                    if item.startswith("numpy-") and item.endswith(".dist-info"):
                        shutil.rmtree(os.path.join(target_path, item))
                print("   Bundled numpy removed. Engine will use global numpy.")
            except Exception as cleanup_err:
                print(f"⚠️ Warning: Could not clean bundled numpy: {cleanup_err}")

        # Create a marker file
        with open(os.path.join(target_path, "engine_info.txt"), "w") as f:
            f.write(f"Engine: {engine_name}\nVersion: {data['version']}\nSource: {data.get('package_source')}\n")
            
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Installation failed with code {e.returncode}")
        return False

def uninstall_global():
    target_python = get_python_executable()
    print(f"--- UNINSTALLING GLOBAL LLAMA-CPP-PYTHON using {target_python} ---")
    print("This ensures the system relies solely on the 'engines/' folder.")
    cmd = [target_python, "-m", "pip", "uninstall", "-y", "llama-cpp-python"]
    try:
        subprocess.check_call(cmd)
        print("✅ Global package uninstalled.")
    except subprocess.CalledProcessError:
        print("⚠️ Failed or already uninstalled.")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="RolemIAster Engine Manager")
    parser.add_argument("--check", action="store_true", help="Check status")
    parser.add_argument("--install", choices=["cuda", "vulkan", "all"], help="Install engines")
    parser.add_argument("--clean-global", action="store_true", help="Uninstall global package")
    
    args = parser.parse_args()
    
    if args.check:
        sys.exit(0 if check_health() else 1)
        
    if args.install:
        if args.install == "all":
            install_engine("cuda")
            install_engine("vulkan")
        else:
            install_engine(args.install)
            
    if args.clean_global:
        uninstall_global()
        
    if not (args.check or args.install or args.clean_global):
        parser.print_help()

if __name__ == "__main__":
    main()
