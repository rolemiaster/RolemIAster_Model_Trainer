import time
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

class TrainerEngine:
    def __init__(self, logger_func, tr_func=lambda k, **kw: k):
        self.log = logger_func
        self.tr = tr_func
        self.stop_requested = False
        self.current_process = None

    def train(self, md_path, model_path, output_dir, params):
        """
        Orquesta el proceso de entrenamiento:
        1. Preparar dataset
        2. Detectar tipo de modelo (GGUF vs HF)
        3. Ejecutar entrenamiento (Simulado o Real)
        4. Exportar
        """
        self.log(self.tr("log_engine_started"))
        
        # 1. Generación de Dataset
        self.log(self.tr("log_generating_dataset").format(name=md_path.name))
        try:
            from core.preparar_dataset import (
                audit_sharegpt_dataset_file,
                generate_robust_dataset,
            )
            dataset_path = output_dir / "training_dataset.jsonl"
            if not output_dir.exists():
                output_dir.mkdir(parents=True, exist_ok=True)
                
            narrative_style = str(params.get("narrative_style", "literary")).strip().lower()
            success = generate_robust_dataset(str(md_path), str(dataset_path), narrative_style=narrative_style)
            if not success:
                self.log(self.tr("log_dataset_generation_failed"))
                return False

            audit = audit_sharegpt_dataset_file(str(dataset_path), max_examples=6)
            self.log(
                "[DATASET][AUDIT] "
                + f"rows={audit.get('rows', 0)} | valid={audit.get('valid_rows', 0)} | "
                + f"invalid={audit.get('invalid_rows', 0)}"
            )
            if int(audit.get("invalid_rows", 0) or 0) > 0:
                issue_counts = audit.get("issue_counts", {})
                self.log(f"[DATASET][AUDIT][ERROR] issue_counts={issue_counts}")
                for example in audit.get("invalid_examples", []) or []:
                    self.log(f"[DATASET][AUDIT][SAMPLE] {example}")
                self.log(self.tr("log_dataset_invalid"))
                return False

            self.log(self.tr("log_dataset_generated").format(path=dataset_path))
        except Exception as e:
            self.log(self.tr("log_dataset_critical_error").format(e=str(e)))
            return False

        if self.stop_requested: return False

        is_gguf = model_path.suffix.lower() == ".gguf"
        
        if is_gguf:
            self.log(self.tr("log_detected_gguf"))
            self.log(self.tr("log_gguf_warning_bin"))
            self.log(self.tr("log_gguf_warning_support"))
            # Aquí iría la llamada a llama-finetune si existiera
            self.train_gguf_simulated(model_path, output_dir, params)
        else:
            self.log(self.tr("log_detected_hf"))
            
            # Si el usuario seleccionó un archivo dentro de la carpeta del modelo (ej. config.json), usamos la carpeta.
            # Si seleccionó un archivo .bin/.safetensors, también usamos la carpeta.
            real_model_path = model_path
            if model_path.exists() and model_path.is_file():
                real_model_path = model_path.parent
                self.log(self.tr("log_adjusting_model_path").format(path=real_model_path))
            
            self.log(self.tr("log_starting_unsloth"))
            return self.run_unsloth_subprocess(real_model_path, dataset_path, output_dir, params)

        return True

    def run_unsloth_subprocess(self, model_path, dataset_path, output_dir, params):
        is_frozen = getattr(sys, 'frozen', False)
        cmd = []
        
        # [MODO AISLADO] Evitar que el entorno Python del sistema interfiera con el subprocess compilado
        # Esta copia (os.environ.copy()) asegura que solo modificamos la memoria de ESTE subproceso internamente.
        # NUNCA tocamos las variables globales u originales del PC del usuario.
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env.pop("PEP582_PACKAGES", None)
        
        # Limpiar referencias a Python global en el PATH interno del proceso, igual que en test_isolated.ps1
        current_path = env.get("PATH", "")
        clean_path_parts = [p for p in current_path.split(os.pathsep) if "python" not in p.lower() and "conda" not in p.lower()]
        env["PATH"] = os.pathsep.join(clean_path_parts)
        engine_mode = params.get("engine", "auto")

        if is_frozen:
            cmd = [
                sys.executable,
                "--train-worker",
                str(model_path),
                str(dataset_path),
                str(output_dir),
                json.dumps(params)
            ]
            self.log(self.tr("log_executing_worker").format(exe=sys.executable))
        else:
            script_path = Path(__file__).parent / "run_unsloth_training.py"
            cmd = [
                sys.executable,
                str(script_path),
                str(model_path),
                str(dataset_path),
                str(output_dir),
                json.dumps(params)
            ]
            self.log(self.tr("log_executing_script").format(script=script_path.name))
        
        try:
            # Ejecutar con Popen para capturar salida en tiempo real
            self.current_process = subprocess.Popen(
                cmd,
                env=env, 
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8', # Importante para leer tildes y emojis
                errors='replace',
                bufsize=1, 
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )

            partial_export_failure = False
            
            # Leer salida línea a línea
            for line in self.current_process.stdout:
                if self.stop_requested:
                    self.current_process.terminate()
                    self.log(self.tr("log_stopped_by_user"))
                    return False
                
                line = line.strip()
                if line:
                    if "[PARTIAL-SUCCESS]" in line:
                        partial_export_failure = True
                    self.log(f"[Unsloth] {line}")
            
            self.current_process.wait()
            
            if self.current_process.returncode == 0:
                self.log(self.tr("log_training_finished_ok"))
                return True
            else:
                if partial_export_failure:
                    self.log(self.tr("log_partial_success"))
                self.log(self.tr("log_process_exit_code").format(code=self.current_process.returncode))
                return False
                
        except Exception as e:
            self.log(self.tr("log_critical_exec_error").format(env='UNSLOTH', e=str(e)))
            return False
        finally:
            self.current_process = None

    def train_gguf_simulated(self, model_path, output_dir, params):
        self.log(self.tr("log_gguf_simulated_start"))
        self.log(self.tr("log_simulating_epochs"))
        
        epochs = params.get("epochs", 3)
        for i in range(1, epochs + 1):
            if self.stop_requested: return
            time.sleep(1.5) # Simular trabajo
            loss = 2.5 - (i * 0.5) + (0.1 * (1 if i%2==0 else -1))
            self.log(f"Epoch {i}/{epochs} - Loss: {loss:.4f}")

        output_name = f"{model_path.stem}_Finetuned.gguf"
        final_path = output_dir / output_name
        
        self.log(self.tr("log_merging_lora"))
        time.sleep(2)
        
        # Simular creación del archivo final (copiando el original o creando dummy)
        # Para evitar ocupar espacio real copiando gigas, creamos un archivo dummy de testigo
        with open(final_path, 'w') as f:
            f.write("Este es un archivo GGUF simulado resultante del entrenamiento.\n")
            f.write(f"Origen: {model_path.name}\n")
            f.write(f"Params: {params}\n")
            
        self.log(self.tr("log_training_saved").format(path=final_path))

    def train_unsloth_simulated(self, model_path, output_dir, params):
        # ... (Mantener por si acaso, aunque ya no se usa en el flujo principal si unsloth real funciona)
        pass

    def stop(self):
        self.stop_requested = True
        if self.current_process:
            self.log(self.tr("log_sending_termination"))
            self.current_process.terminate()
