import os
import sys
import json
import shutil
import traceback
import platform
import torch
from datetime import datetime
from pathlib import Path

# --- PATH SETUP ---
# Asegurar que 'src' está en sys.path para imports internos
current_file = Path(__file__).resolve()
src_dir = current_file.parent.parent
if str(src_dir) not in sys.path:
    sys.path.append(str(src_dir))
# ------------------

# Configuración de entorno según sistema
IS_WINDOWS = sys.platform == "win32"

MODEL_HINT = str(sys.argv[1]).lower() if len(sys.argv) > 1 else ""
IS_QWEN35_OR_MOE = any(tag in MODEL_HINT for tag in ("qwen3.5", "moe", "a3b", "a17b", "qwen3.5-35b", "qwen3.5-397b"))

# En Windows, TorchInductor puede fallar al buscar APIs no disponibles.
# Forzamos modo sin compilación para estabilidad.
if IS_WINDOWS:
    os.environ.setdefault("UNSLOTH_COMPILE_DISABLE", "1")
    os.environ.setdefault("UNSLOTH_COMPILE_IGNORE_ERRORS", "1")
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

MIN_VALID_GGUF_BYTES = 100 * 1024 * 1024  # 100MB mínimo para descartar artefactos claramente inválidos

# Compatibilidad: algunas combinaciones de Unsloth/unsloth_zoo + TorchAO
# esperan dtypes sub-byte (int1..int7, uint1..uint7) no presentes en torch 2.5.x.
# Creamos aliases conservadores para permitir importación estable.
_compat_aliases = []
for i in range(1, 8):
    int_name = f"int{i}"
    uint_name = f"uint{i}"
    if not hasattr(torch, int_name):
        setattr(torch, int_name, torch.int8)
        _compat_aliases.append(int_name)
    if not hasattr(torch, uint_name):
        setattr(torch, uint_name, torch.uint8)
        _compat_aliases.append(uint_name)

if _compat_aliases:
    print(
        "[COMPAT] dtypes torch faltantes aliasados para compatibilidad: "
        + ", ".join(_compat_aliases)
    )

# --- PARCHE DE EMERGENCIA (GGUF COMPATIBILITY) ---
# Fix para error "AttributeError: type object 'MODEL_ARCH' has no attribute 'MISTRAL4'"
# El script de llama.cpp manipula sys.path para cargar su propio gguf-py, ignorando el .venv.
# CRÍTICO: Este parche DEBE ejecutarse ANTES del import de unsloth, porque unsloth_zoo.llama_cpp
# ejecuta _download_convert_hf_to_gguf() durante su inicialización (tiene @lru_cache).
try:
    import gguf
    # Forzar que sys.modules['gguf'] apunte al gguf del .venv (que tiene MISTRAL4 parchado)
    sys.modules['gguf'] = gguf
    if hasattr(gguf.MODEL_ARCH, "MISTRAL4"):
        print("[PATCH] gguf del .venv pre-cargado en sys.modules (MISTRAL4 disponible).")
    else:
        print("[WARN] gguf del .venv cargado pero MISTRAL4 no encontrado. Exportación GGUF puede fallar.")
except Exception as patch_e:
    print(f"[WARN] No se pudo aplicar parche GGUF sys.modules: {patch_e}")
# -------------------------------------------------

try:
    from unsloth import FastLanguageModel, FastModel
    from unsloth.save import save_to_gguf

except Exception as e:
    print("[DEPENDENCY ERROR] No se pudo importar Unsloth.")
    print(f"[DEPENDENCY ERROR] Detalle: {e}")
    print("[DEPENDENCY ERROR] Solucion recomendada:")
    print("  1) python -m pip install --upgrade unsloth unsloth_zoo")
    print("  2) Verifica version de torch compatible con tu version de unsloth")
    sys.exit(1)

from trl import SFTTrainer
from transformers import TrainingArguments
from datasets import load_dataset

try:
    from core.preparar_dataset import audit_sharegpt_dataset_file
except Exception:
    try:
        from preparar_dataset import audit_sharegpt_dataset_file
    except Exception:
        audit_sharegpt_dataset_file = None

from core.i18n_utils import get_translator

def run_training(model_id_or_path, dataset_path, output_dir, params):
    lang = params.get("lang", "en")
    tr = get_translator(lang)

    print(f"--- {tr('log_starting_unsloth_real')} ---")
    print(f"Modelo: {model_id_or_path}")
    print(f"Dataset: {dataset_path}")
    print(f"Params: {params}")
    resume_previous_run_dir = params.get("resume_previous_run_dir")
    resume_mode = bool(resume_previous_run_dir)
    adapters_dir = None
    resume_base_model_path = model_id_or_path
    if resume_mode:
        previous_run_dir = Path(resume_previous_run_dir)
        adapters_dir = previous_run_dir / "lora_adapters"
        if not adapters_dir.exists():
            raise FileNotFoundError(f"No se encontró carpeta LoRA para retomar: {adapters_dir}")

        adapter_cfg_path = adapters_dir / "adapter_config.json"
        if adapter_cfg_path.exists():
            try:
                with open(adapter_cfg_path, "r", encoding="utf-8") as f:
                    adapter_cfg = json.load(f)
                base_from_adapter = adapter_cfg.get("base_model_name_or_path")
                if base_from_adapter:
                    resume_base_model_path = base_from_adapter
            except Exception as cfg_e:
                print(f"[WARN] No se pudo leer adapter_config.json para base_model_name_or_path: {cfg_e}")

    if callable(audit_sharegpt_dataset_file):
        dataset_audit = audit_sharegpt_dataset_file(str(dataset_path), max_examples=8)
        print(
            "[DATASET][AUDIT] "
            + f"rows={dataset_audit.get('rows', 0)} | "
            + f"valid={dataset_audit.get('valid_rows', 0)} | "
            + f"invalid={dataset_audit.get('invalid_rows', 0)}"
        )
        if int(dataset_audit.get("invalid_rows", 0) or 0) > 0:
            print(f"[DATASET][AUDIT][ERROR] issue_counts={dataset_audit.get('issue_counts', {})}")
            for example in dataset_audit.get("invalid_examples", []) or []:
                print(f"[DATASET][AUDIT][SAMPLE] {example}")
            raise RuntimeError(
                "Dataset inválido: contiene salidas que no cumplen el contrato canónico de turno. "
                "Se aborta el entrenamiento para evitar aprendizaje defectuoso."
            )
    else:
        print("[WARN] No se pudo cargar audit_sharegpt_dataset_file; se omite auditoría previa de dataset.")

    # 1. Configuración
    model_name_lower = str(model_id_or_path).lower()
    is_4b = "4b" in model_name_lower
    is_8b_or_more = any(tag in model_name_lower for tag in ("8b", "14b", "32b"))
    is_14b_or_more = any(tag in model_name_lower for tag in ("14b", "32b"))
    is_qwen35 = "qwen3.5" in model_name_lower or "qwen3_5" in model_name_lower
    per_device_batch = int(params.get("batch_size", 2))
    grad_acc_steps = int(params.get("gradient_accumulation_steps", params.get("grad_accumulation_steps", 4)))
    detected_vram_gb = None
    oom_guard_enabled = False

    # Detectar si FLA (flash-linear-attention) está disponible.
    # FLA proporciona kernels Triton optimizados para las capas GDN de Qwen3.5.
    # Sin FLA, Transformers usa un fallback PyTorch que consume ~2x más VRAM.
    fla_available = False
    if is_qwen35:
        try:
            import fla
            from fla.ops.gated_delta_rule import chunk_gated_delta_rule
            fla_available = True
        except ImportError:
            pass

    if is_qwen35:
        print(f"[QWEN3.5] Modelo Qwen3.5 detectado. FLA kernels: {'SÍ' if fla_available else 'NO (fallback PyTorch)'}")

    if torch.cuda.is_available():
        try:
            detected_vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            print(f"[HW] VRAM detectada: {detected_vram_gb:.2f} GB")
        except Exception:
            detected_vram_gb = None

    # Protección anti-OOM para modelos grandes en 16 GB VRAM aprox.
    if oom_guard_enabled and is_8b_or_more:
        if per_device_batch > 1:
            print(
                f"[OOM-GUARD] Modelo grande detectado ({model_id_or_path}). "
                f"Reduciendo batch_size {per_device_batch} -> 1 para estabilidad."
            )
            per_device_batch = 1
        if grad_acc_steps > 1:
            print(f"[OOM-GUARD] Reduciendo gradient_accumulation_steps {grad_acc_steps} -> 1.")
            grad_acc_steps = 1

    # En GPUs de ~16GB, 4B con seq 2048 + batch 4 puede seguir causando OOM.
    if oom_guard_enabled and is_4b and detected_vram_gb is not None and detected_vram_gb <= 16.5:
        if per_device_batch > 2:
            print(f"[OOM-GUARD] 4B en <=16.5GB VRAM: batch_size {per_device_batch} -> 2.")
            per_device_batch = 2
        if grad_acc_steps > 2:
            print(f"[OOM-GUARD] 4B en <=16.5GB VRAM: grad_acc {grad_acc_steps} -> 2.")
            grad_acc_steps = 2

    # ── Qwen3.5 sin FLA: ajustes automáticos de VRAM ──
    # Sin los kernels FLA, el fallback PyTorch de las capas GDN consume ~2x
    # más VRAM. Ajustamos batch/seq/grad_acc para que quepa en 16 GB.
    # Qwen3.5: Sin FLA los requirements de memoria se disparan (Atencion cuadratica naive en PyTorch)
    # Forzamos reduccion de hiperparametros para 16GB VRAM
    if is_qwen35 and not fla_available:
        original_batch = per_device_batch
        original_grad = grad_acc_steps
        # Sin FLA, batch_size > 1 revienta las 16GB VRAM durante el calculo del gradiente MoE
        per_device_batch = 1
        grad_acc_steps = max(1, original_batch * original_grad)
        print(
            f"[QWEN3.5][COMPAT] Sin kernels FLA -> OOM prevention en VRAM: "
            f"batch {original_batch}->{per_device_batch}, "
            f"grad_acc {original_grad}->{grad_acc_steps}"
        )

    max_seq_length = int(params.get("max_seq_length", 2048)) # Soportamos contextos largos para las reglas
    if is_qwen35 and not fla_available and max_seq_length > 896:
        print(f"[QWEN3.5][COMPAT] max_seq_length {max_seq_length}->896 (OOM prevention buffer)")
        max_seq_length = 896
    elif oom_guard_enabled and is_14b_or_more:
        max_seq_length = 512
        print("[OOM-GUARD] Modelo 14B+ detectado: max_seq_length 2048 -> 512.")
    elif oom_guard_enabled and is_8b_or_more:
        max_seq_length = 1024
        print("[OOM-GUARD] Modelo 8B detectado: max_seq_length 2048 -> 1024.")
    elif oom_guard_enabled and is_4b and detected_vram_gb is not None and detected_vram_gb <= 16.5:
        max_seq_length = 1024
        print("[OOM-GUARD] Modelo 4B en <=16.5GB VRAM: max_seq_length 2048 -> 1024.")
    dtype = None # Auto-detect
    load_in_4bit = True # Obligatorio para eficiencia VRAM

    # 2. Cargar Modelo y Tokenizer
    if resume_mode:
        print(f"[RESUME] Cargando modelo LoRA desde run previa: {adapters_dir}")
        if str(resume_base_model_path) != str(model_id_or_path):
            print(f"[RESUME] Base detectada en adapter_config: {resume_base_model_path}")
        model_load_path = str(adapters_dir)
    else:
        print(tr("log_loading_base_model"))
        model_load_path = model_id_or_path

    # Detección de arquitectura MoE (Mixture of Experts)
    is_moe = any(tag in model_name_lower for tag in ("moe", "a3b", "a17b", "qwen3.5-35b", "qwen3.5-397b"))
    model_loader_class = FastModel if is_moe else FastLanguageModel
    if is_moe:
        print(f"[ARCH] Arquitectura MoE detectada ({model_name_lower}). Usando FastModel en lugar de FastLanguageModel.")

    try:
        model, tokenizer = model_loader_class.from_pretrained(
            model_name = model_load_path,
            max_seq_length = max_seq_length,
            dtype = dtype,
            load_in_4bit = load_in_4bit,
        )
    except RuntimeError as e:
        error_msg = str(e)
        if "no kernel image is available" in error_msg or "automatic conversion of the weights" in error_msg:
            print("\n")
            print("=========================================================================")
            print("[CRÍTICO] Fallo de Quantización 4-bit detectado (BitsAndBytes sin soporte)")
            print("[CRÍTICO] La GPU actual (posiblemente RTX 5000 / sm_120) no es compatible")
            print("[CRÍTICO] con los kernels binarios de la versión actual de bitsandbytes.")
            print("[CRÍTICO] Activando Salvavidas: Forzando carga BFLOAT16 nativa a 16-bit...")
            print("=========================================================================\n")
            
            # Limpiar VRAM corrupta del crasheo previo
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
            
            # Recargar a 16-bit puros (más pesado, pero no depende de bitsandbytes ASM)
            model, tokenizer = model_loader_class.from_pretrained(
                model_name = model_load_path,
                max_seq_length = max_seq_length,
                dtype = torch.bfloat16,  # 5090 mastica bfloat16 perfectamente
                load_in_4bit = False,
            )
        else:
            raise e

    # -- Qwen3.5: descargar encoder visual y proyectores a CPU (solo usamos texto) --
    # Qwen3.5 es multimodal (texto + vision). El encoder visual y su proyector
    # ocupan VRAM innecesariamente ya que solo se usa texto para el juego.
    # IMPORTANTE: requires_grad_(False) NO libera VRAM, hay que mover a CPU.
    if is_qwen35:
        vision_modules = ["visual", "vision_projector", "multimodal_projector"]
        freed_modules = []
        
        for v_name in vision_modules:
            v_mod = getattr(model, v_name, None)
            if v_mod is None:
                inner = getattr(model, "model", None)
                if inner is not None:
                    v_mod = getattr(inner, v_name, None)
            
            if v_mod is not None:
                v_mod.to("cpu")
                v_mod.requires_grad_(False)
                freed_modules.append(v_name)
                
        if freed_modules:
            torch.cuda.empty_cache()
            vram_after = torch.cuda.memory_allocated(0) / (1024**3) if torch.cuda.is_available() else 0
            moved_str = ", ".join(freed_modules)
            print(f"[QWEN3.5] Componentes visuales podados a CPU [{moved_str}]. VRAM post-descarga: {vram_after:.2f} GB")

    # [DIAGNOSTICO] Verificar estado de CUDA/GPU
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        vram_total = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        vram_allocated = torch.cuda.memory_allocated(0) / (1024**3)
        print(f"[DIAG] CUDA Disponible: Sí | Dispositivo: {gpu_name} | VRAM Total: {vram_total:.2f} GB | Alloc: {vram_allocated:.2f} GB")
        
        # Check text model specifically to avoid false positive if visual is on CPU
        try:
            embed = model.get_input_embeddings() if hasattr(model, "get_input_embeddings") else model
            text_device = str(next(embed.parameters()).device) if hasattr(embed, "parameters") else str(getattr(embed, "device", "unknown"))
        except (StopIteration, AttributeError):
            text_device = "unknown"
        if "cpu" in text_device and not ("cuda" in str(set(str(p.device) for p in model.parameters()))):
            print(f"[DIAG] {tr('log_model_on_cpu_warning')}")
    else:
        print("[DIAG] ¡ALERTA! CUDA NO DETECTADO. Entrenando en CPU (Lentitud esperada).")

    if not resume_mode:
        # 3. Configurar LoRA (Adaptadores)
        print("Configurando adaptadores LoRA...")
        
        # Para MoE (Qwen3.5), los nombres de las proyecciones varían (incluyen expertos).
        # EVITAR 'all-linear' en 4-bit con MoE ya que inyecta LoRA en capas particionadas/incompatibles
        # causando crashes CUBLAS_STATUS_EXECUTION_FAILED en forward pass. Usamos proyecciones seguras.
        target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

        model = model_loader_class.get_peft_model(
            model,
            r = int(params.get("rank", 32)),
            target_modules = target_modules,
            lora_alpha = int(params.get("alpha", 64)),
            lora_dropout = 0, # Optimizado es 0
            bias = "none",    # Optimizado es none
            use_gradient_checkpointing = "unsloth", # Ahorra VRAM
            random_state = 3407,
            use_rslora = False,
            loftq_config = None,
        )

        # 4. Preparar Dataset
        print("Cargando dataset...")
        dataset = load_dataset("json", data_files=str(dataset_path), split="train")

        role_alias = {
            "human": "user",
            "user": "user",
            "gpt": "assistant",
            "assistant": "assistant",
            "system": "system",
        }

        def _normalize_chat_messages(convo):
            normalized = []
            for msg in convo or []:
                if not isinstance(msg, dict):
                    continue
                raw_role = str(msg.get("from", msg.get("role", "user"))).strip().lower()
                role = role_alias.get(raw_role, "user")
                content = str(msg.get("value", msg.get("content", "")))
                normalized.append({"role": role, "content": content})
            return normalized

        def _render_fallback_chat(messages):
            parts = []
            for msg in messages:
                role = str(msg.get("role", "user")).upper()
                content = str(msg.get("content", ""))
                parts.append(f"[{role}] {content}")
            return "\n".join(parts)

        # --- THINK-SUPPRESSION: Protección contra templates con lógica de razonamiento ---
        # Modelos como Qwen 3.5, DeepSeek-R1, etc. incluyen bloques <think> en su
        # chat_template nativo. Si entrenamos con ese template, el SFT se contamina:
        # el modelo aprende a esperar/generar bloques de razonamiento que no existen
        # en nuestro dataset (JSON puro). Sobreescribimos con ChatML limpio.
        _THINK_PATTERNS = ("enable_thinking", "<think>", "<thought>", "reasoning_content")
        _CHATML_CLEAN = (
            "{% for message in messages %}"
            "{% if message['role'] == 'system' %}"
            "<|im_start|>system\n{{ message['content'] }}<|im_end|>\n"
            "{% elif message['role'] == 'user' %}"
            "<|im_start|>user\n{{ message['content'] }}<|im_end|>\n"
            "{% elif message['role'] == 'assistant' %}"
            "<|im_start|>assistant\n{{ message['content'] }}<|im_end|>\n"
            "{% endif %}"
            "{% endfor %}"
            "{% if add_generation_prompt %}"
            "<|im_start|>assistant\n"
            "{% endif %}"
        )

        thinking_mode = str(params.get("thinking_mode", "suppress")).strip().lower()
        if thinking_mode == "suppress" and hasattr(tokenizer, "chat_template"):
            original_template = str(getattr(tokenizer, "chat_template", "") or "")
            has_thinking = any(pat in original_template for pat in _THINK_PATTERNS)
            if has_thinking:
                tokenizer.chat_template = _CHATML_CLEAN
                print(
                    "[THINK-SUPPRESSION] Template nativo contiene lógica de razonamiento "
                    f"(patrones detectados: {[p for p in _THINK_PATTERNS if p in original_template]}). "
                    "Sobreescrito con ChatML limpio para entrenamiento SFT."
                )
            else:
                print("[THINK-SUPPRESSION] Template nativo no contiene lógica de razonamiento. Sin cambios.")
        elif thinking_mode == "native":
            print("[THINK-SUPPRESSION] Modo 'nativo' seleccionado. Template del modelo sin modificar.")
        else:
            print(f"[THINK-SUPPRESSION] Modo: {thinking_mode}. Sin intervención en template.")

        use_native_chat_template = hasattr(tokenizer, "apply_chat_template")
        if use_native_chat_template:
            try:
                tokenizer.apply_chat_template(
                    [{"role": "user", "content": "ping"}],
                    tokenize=False,
                    add_generation_prompt=False,
                )
            except Exception as template_error:
                print(
                    "[DATASET][WARN] apply_chat_template no disponible para este tokenizer; "
                    f"se usara fallback plano. Detalle: {template_error}"
                )
                use_native_chat_template = False
        print(
            "[DATASET] Render de chats para entrenamiento: "
            + ("tokenizer.apply_chat_template (nativo)" if use_native_chat_template else "fallback plano [ROLE] contenido")
        )

        def formatting_prompts_func(examples):
            convos = examples["conversations"]
            texts = []
            for convo in convos:
                normalized = _normalize_chat_messages(convo)
                if use_native_chat_template:
                    texts.append(
                        tokenizer.apply_chat_template(
                            normalized,
                            tokenize=False,
                            add_generation_prompt=False,
                        )
                    )
                else:
                    texts.append(_render_fallback_chat(normalized))
            return {"text": texts}

        dataset = dataset.map(formatting_prompts_func, batched = True,)
        
        # --- FILTRADO DE EJEMPLOS LARGOS (PROTECCIÓN EOS) ---
        # Descartamos ejemplos que superen max_seq_length en lugar de dejar que
        # el SFTTrainer los trunque silenciosamente. Truncar amputaría el token
        # de parada <|im_end|>, enseñando al modelo a NO emitir EOS en respuestas
        # largas (causa raíz de verborrea infinita y JSON incompleto).
        original_size = len(dataset)
        try:
            _tok_ref = tokenizer  # captura para closure
            _max_len = max_seq_length

            def _fits_in_context(example):
                toks = _tok_ref(example["text"], truncation=False)["input_ids"]
                return len(toks) <= _max_len

            dataset = dataset.filter(_fits_in_context, num_proc=1)
            filtered_out = original_size - len(dataset)
            if filtered_out > 0:
                pct = (filtered_out / original_size) * 100
                print(
                    f"[DATASET] Filtrados {filtered_out}/{original_size} ejemplos ({pct:.1f}%) "
                    f"que superaban max_seq_length={max_seq_length} tokens. "
                    f"Esto preserva el token de parada en el 100% del dataset restante."
                )
            else:
                print(f"[DATASET] Todos los ejemplos caben en max_seq_length={max_seq_length}. Sin filtrado.")
        except Exception as filter_e:
            print(f"[WARN] No se pudo filtrar por longitud: {filter_e}. Se continuará sin filtrado.")
        # ---------------------------------------------------

        # Eliminar columna 'conversations' para que TRL no intente re-procesarla
        # (TRL lee 'conversations' raw con roles "human"/"gpt" y falla porque espera "user"/"assistant")
        if "conversations" in dataset.column_names:
            dataset = dataset.remove_columns(["conversations"])

        # 5. Configurar Entrenador (SFTTrainer)
        print("Configurando Trainer...")
        trainer = SFTTrainer(
            model = model,
            tokenizer = tokenizer,
            train_dataset = dataset,
            dataset_text_field = "text",
            max_seq_length = max_seq_length,
            dataset_num_proc = 2,
            packing = False,
            args = TrainingArguments(
                per_device_train_batch_size = per_device_batch,
                gradient_accumulation_steps = grad_acc_steps,
                gradient_checkpointing = True, # Escencial para salvar 10+ GB de VRAM en modelos MoE sin FLA
                warmup_steps = 5,
                max_steps = -1,
                num_train_epochs = int(params.get("epochs", 3)),
                learning_rate = float(params.get("learning_rate", 2e-4)),
                fp16 = not torch.cuda.is_bf16_supported(),
                bf16 = torch.cuda.is_bf16_supported(),
                logging_steps = 1,
                optim = "adamw_8bit",
                weight_decay = 0.01,
                lr_scheduler_type = "linear",
                seed = 3407,
                output_dir = "outputs",
            ),
        )

        # --- TRAIN ON RESPONSES ONLY ---
        # Enmascarar system prompt y user prompt en la función de loss
        # Usamos los tokens ChatML reales producidos por apply_chat_template en Qwen
        try:
            from unsloth.chat_templates import train_on_responses_only
            trainer = train_on_responses_only(
                trainer,
                instruction_part = "<|im_start|>user\n",
                response_part = "<|im_start|>assistant\n",
            )
            print("[UNSLOTH] 'train_on_responses_only' activado: el loss solo se calculará sobre el output JSON.")
        except ImportError:
            print("[WARN] No se pudo importar 'train_on_responses_only' de unsloth. Entrenando sobre toda la secuencia.")
        except Exception as e:
            print(f"[WARN] Error al configurar 'train_on_responses_only': {e}")
        # -------------------------------

        # 6. Entrenar
        print(f">>> {tr('log_training_real_time')} <<<")
        trainer.train()
        print(tr("log_training_finished"))

        model_name = Path(str(model_id_or_path).rstrip("/\\")).name or "model"
        safe_model_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in model_name)
        run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_output_dir = output_dir / f"run_{safe_model_name}_{run_stamp}"
        run_output_dir.mkdir(parents=True, exist_ok=True)
        adapters_dir = run_output_dir / "lora_adapters"
        lora_saved_ok = False
    else:
        previous_run_dir = Path(resume_previous_run_dir)
        print(f"[RESUME] Retomando run previa: {previous_run_dir}")
        print(f"[RESUME] Cargando adaptadores LoRA desde: {adapters_dir}")
        run_output_dir = previous_run_dir
        lora_saved_ok = True

    export_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    export_start_ts = datetime.now().timestamp()
    export_work_dir = output_dir / f"gguf_work_{export_stamp}"
    export_work_dir.mkdir(parents=True, exist_ok=True)

    def _exc_text(exc: Exception) -> str:
        msg = str(exc).strip()
        if msg:
            return msg
        return f"{type(exc).__name__}: {repr(exc)}"

    def _log_exception(prefix: str, exc: Exception):
        print(f"[DEBUG] {prefix}: {_exc_text(exc)}")
        tb = traceback.format_exc()
        if tb and tb.strip() and "NoneType: None" not in tb:
            print(f"[DEBUG] {prefix} traceback:\n{tb}")

    try:
        if not resume_mode:
            print(f"Guardando adaptadores LoRA en: {adapters_dir}")
            model.save_pretrained(str(adapters_dir))
            
            # Guardar metadata de entrenamiento
            training_meta = {
                "rank": int(params.get("rank", 32)),
                "alpha": int(params.get("alpha", 64)),
                "batch_size": int(params.get("batch_size", 2)),
                "effective_batch_size": per_device_batch * grad_acc_steps,
                "learning_rate": float(params.get("learning_rate", 2e-4)),
                "epochs": int(params.get("epochs", 3)),
                "max_seq_length": max_seq_length,
                "grad_accumulation_steps": grad_acc_steps,
                "train_on_responses_only": True
            }
            meta_path = run_output_dir / "training_meta.json"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(training_meta, f, indent=4, ensure_ascii=False)
            print(f"[EXPORT] Metadata de entrenamiento guardada en: {meta_path}")
            
            lora_saved_ok = True
        else:
            print(f"[RESUME] Se reutilizan adaptadores existentes: {adapters_dir}")

        # Preparar carpeta HF mergeada para la conversion GGUF.
        print(tr("log_preparing_merge"))
        print(f"[EXPORT] Carpeta de trabajo de export: {export_work_dir}")
        merged_mode_used = None
        merge_last_error = None
        merge4_error = None
        legacy_error = None
        manual_merge_error = None
        dequant_merge_error = None
        merged_model_dir = None
        manual_merged_model = None
        manual_merge_needs_dequant = False

        merge_16_dir = export_work_dir / "merge_16bit"
        merge_4_dir = export_work_dir / "merge_4bit"
        merge_legacy_dir = export_work_dir / "merge_legacy"
        merge_dequant_dir = export_work_dir / "merge_dequant_fp16"
        for d in (merge_16_dir, merge_4_dir, merge_legacy_dir):
            d.mkdir(parents=True, exist_ok=True)

        def _has_merge_weights(path: Path) -> bool:
            return any(path.glob("*.safetensors")) or any(path.glob("*.bin"))

        def _validate_merge_dir(path: Path, mode_name: str):
            if not (path / "config.json").exists():
                raise RuntimeError(f"{mode_name} no generó config.json")
            if not _has_merge_weights(path):
                raise RuntimeError(f"{mode_name} no generó archivos de pesos")

        def _sanitize_quantization_config(path: Path):
            config_path = path / "config.json"
            if not config_path.exists():
                return

            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if isinstance(cfg.get("quantization_config"), dict):
                    cfg.pop("quantization_config", None)
                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=2)
                    print("[EXPORT] config.json saneado: quantization_config eliminado.")
            except Exception as cfg_e:
                print(f"[WARN] No se pudo sanear config.json antes de GGUF: {cfg_e}")

        def _has_quantized_state_artifacts(path: Path) -> bool:
            """
            Detecta claves de estado de cuantización (ej. *.absmax, *.quant_state.*)
            que rompen el convertidor HF->GGUF de llama.cpp.
            """
            try:
                from safetensors import safe_open
            except Exception as imp_e:
                print(f"[WARN] No se pudo importar safetensors para inspección de pesos: {imp_e}")
                return False

            suspicious_suffixes = (
                ".absmax",
                ".nested_absmax",
                ".quant_map",
                ".nested_quant_map",
            )

            for safetensor_file in path.glob("*.safetensors"):
                try:
                    with safe_open(str(safetensor_file), framework="pt", device="cpu") as sf:
                        for key in sf.keys():
                            if key.endswith(suspicious_suffixes) or ".quant_state." in key:
                                print(
                                    "[WARN] Detectado tensor de cuantización no compatible con GGUF "
                                    f"en {safetensor_file.name}: {key}"
                                )
                                return True
                except Exception as scan_e:
                    print(f"[WARN] No se pudo inspeccionar {safetensor_file.name}: {scan_e}")
            return False

        # Intento 1: merge 16bit (más compatible para conversión HF -> GGUF)
        try:
            model.save_pretrained_merged(
                str(merge_16_dir),
                tokenizer,
                save_method = "merged_16bit",
            )
            _validate_merge_dir(merge_16_dir, "merge_16bit")
            merged_mode_used = "merged_16bit"
            merged_model_dir = merge_16_dir
            print("[EXPORT] Merge preparado con modo merged_16bit.")
        except Exception as merge_e:
            merge_last_error = merge_e
            print(f"[WARN] Fallo merge merged_16bit: {merge_e}")
            _log_exception("merge_16bit", merge_e)

        # Intento 2: fallback a 4bit forzado (si 16bit no está disponible)
        if merged_mode_used is None:
            try:
                model.save_pretrained_merged(
                    str(merge_4_dir),
                    tokenizer,
                    save_method = "forced_merged_4bit",
                )
                _validate_merge_dir(merge_4_dir, "forced_merged_4bit")
                merged_mode_used = "forced_merged_4bit"
                merged_model_dir = merge_4_dir
                print("[EXPORT] Fallback aplicado: merge preparado con modo forced_merged_4bit.")
            except Exception as merge_e2:
                print(f"[WARN] Fallo merge forced_merged_4bit: {merge_e2}")
                _log_exception("forced_merged_4bit", merge_e2)
                try:
                    print("[WARN] Reintentando forced_merged_4bit con safe_serialization=False...")
                    model.save_pretrained_merged(
                        str(merge_4_dir),
                        tokenizer,
                        save_method = "forced_merged_4bit",
                        safe_serialization = False,
                    )
                    _validate_merge_dir(merge_4_dir, "forced_merged_4bit (safe_serialization=False)")
                    merged_mode_used = "forced_merged_4bit_nonsafe"
                    merged_model_dir = merge_4_dir
                    print("[EXPORT] Fallback aplicado: merge 4bit guardado con safe_serialization=False.")
                except Exception as merge_e2b:
                    merge4_error = RuntimeError(
                        f"forced_merged_4bit falló: ({merge_e2}) | reintento no-safe: ({merge_e2b})"
                    )
                    _log_exception("forced_merged_4bit reintento no-safe", merge_e2b)
                    print(
                        "[WARN] Fallo merge forced_merged_4bit (incluyendo reintento no-safe): "
                        f"{merge4_error}"
                    )

            # Intento 3: fallback legacy si 4bit no dejó pesos usables
            if merged_mode_used is None:
                try:
                    try:
                        model.save_pretrained_merged(str(merge_legacy_dir), tokenizer)
                    except TypeError:
                        model.save_pretrained_merged(str(merge_legacy_dir))

                    _validate_merge_dir(merge_legacy_dir, "legacy merge")
                    merged_mode_used = "legacy_default"
                    merged_model_dir = merge_legacy_dir
                    print("[EXPORT] Fallback aplicado: merge preparado con API legacy.")
                except Exception as legacy_e:
                    legacy_error = legacy_e
                    print(f"[WARN] Fallo merge legacy de Unsloth: {legacy_e}")
                    _log_exception("legacy merge", legacy_e)

                    # Intento 4: merge manual PEFT como último recurso.
                    try:
                        if not hasattr(model, "merge_and_unload"):
                            raise RuntimeError("El modelo no expone merge_and_unload para fallback manual")

                        manual_merged_model = model.merge_and_unload()
                        try:
                            manual_merged_model.save_pretrained(
                                str(merge_legacy_dir),
                                safe_serialization=True,
                                save_original_format=False,
                            )
                        except Exception as manual_save_e:
                            _log_exception("manual merge save_pretrained safe=True", manual_save_e)
                            print(
                                "[WARN] save_pretrained manual con safe_serialization=True falló: "
                                f"{_exc_text(manual_save_e)}. Reintentando con safe_serialization=False..."
                            )
                            manual_merged_model.save_pretrained(
                                str(merge_legacy_dir),
                                safe_serialization=False,
                                save_original_format=False,
                            )
                        tokenizer.save_pretrained(str(merge_legacy_dir))

                        _validate_merge_dir(merge_legacy_dir, "manual merge")

                        manual_merge_needs_dequant = _has_quantized_state_artifacts(merge_legacy_dir)
                        if manual_merge_needs_dequant:
                            print(
                                "[WARN] El merge manual contiene artefactos de cuantización. "
                                "Se forzará dequantize(fp16) antes de GGUF."
                            )

                        merged_mode_used = "manual_merge_and_unload"
                        merged_model_dir = merge_legacy_dir
                        print("[EXPORT] Fallback aplicado: merge manual con merge_and_unload.")
                    except Exception as manual_e:
                        manual_merge_error = manual_e
                        print(f"[WARN] Fallo merge manual con merge_and_unload: {manual_e}")
                        _log_exception("manual merge_and_unload", manual_e)

        # Intento 5: dequantizar a fp16 y guardar como HF full precision
        # para evitar el bloqueo de serialización de merges 4bit.
        if merged_mode_used is None or manual_merge_needs_dequant:
            merge_dequant_dir.mkdir(parents=True, exist_ok=True)
            try:
                if manual_merge_needs_dequant:
                    print("[WARN] Reprocesando merge manual: dequantize(fp16) obligatorio para compatibilidad GGUF...")
                else:
                    print("[WARN] Intentando fallback extra: merge manual + dequantize(fp16)...")

                if manual_merged_model is None:
                    if not hasattr(model, "merge_and_unload"):
                        raise RuntimeError("No se puede dequantizar: modelo sin merge_and_unload")
                    manual_merged_model = model.merge_and_unload()

                dequant_model = manual_merged_model
                if hasattr(dequant_model, "dequantize"):
                    print(f"[EXPORT] {tr('log_dequantizing')}")
                    try:
                        maybe_model = dequant_model.dequantize(dtype=torch.float16)
                    except TypeError:
                        maybe_model = dequant_model.dequantize()
                    if maybe_model is not None:
                        dequant_model = maybe_model
                else:
                    print(f"[WARN] {tr('log_dequantize_warning')}")

                try:
                    dequant_model.save_pretrained(
                        str(merge_dequant_dir),
                        safe_serialization=True,
                        save_original_format=False,
                        max_shard_size="5GB",
                    )
                except Exception as dequant_save_e:
                    _log_exception("dequant fallback save_pretrained safe=True", dequant_save_e)
                    print(
                        "[WARN] Guardado dequant fp16 con safe_serialization=True falló: "
                        f"{_exc_text(dequant_save_e)}. Reintentando con safe_serialization=False..."
                    )
                    dequant_model.save_pretrained(
                        str(merge_dequant_dir),
                        safe_serialization=False,
                        save_original_format=False,
                        max_shard_size="5GB",
                    )

                tokenizer.save_pretrained(str(merge_dequant_dir))
                _validate_merge_dir(merge_dequant_dir, "manual_dequant_fp16")
                merged_mode_used = "manual_dequant_fp16"
                merged_model_dir = merge_dequant_dir
                print("[EXPORT] Fallback aplicado: merge dequantizado fp16 listo para GGUF.")
            except Exception as dequant_e:
                dequant_merge_error = dequant_e
                _log_exception("manual dequant fallback", dequant_e)
                print(f"[WARN] Fallo fallback dequantizado fp16: {dequant_e}")
                if manual_merge_needs_dequant:
                    # El merge manual no es convertible a GGUF sin dequantización exitosa.
                    merged_mode_used = None
                    merged_model_dir = None

        if merged_model_dir is None:
            raise RuntimeError(
                "Fallo merge merged_16bit, forced_merged_4bit, legacy, manual y dequant fp16. "
                f"Errores: 16bit=({merge_last_error}) | 4bit=({merge4_error}) | "
                f"legacy=({legacy_error}) | manual=({manual_merge_error}) | "
                f"dequant_fp16=({dequant_merge_error})"
            )

        # Convertir el modelo ya mergeado a GGUF sin invocar save_pretrained_gguf
        # (evitamos un segundo merge implícito dentro de Unsloth).
        _sanitize_quantization_config(merged_model_dir)

        model_config = getattr(model, "config", None)
        model_type = getattr(model_config, "model_type", None)
        if not model_type:
            merged_config_path = merged_model_dir / "config.json"
            if merged_config_path.exists():
                with open(merged_config_path, "r", encoding="utf-8") as f:
                    merged_cfg = json.load(f)
                model_type = merged_cfg.get("model_type")
        if not model_type:
            raise RuntimeError("No se pudo determinar model_type para la conversión GGUF.")

        model_dtype = "float16"
        torch_dtype_cfg = getattr(model_config, "torch_dtype", None)
        if isinstance(torch_dtype_cfg, str):
            if "bfloat16" in torch_dtype_cfg.lower():
                model_dtype = "bfloat16"
            elif "float16" in torch_dtype_cfg.lower():
                model_dtype = "float16"
        elif torch_dtype_cfg == torch.bfloat16:
            model_dtype = "bfloat16"
        elif torch_dtype_cfg == torch.float16:
            model_dtype = "float16"

        first_conversion = None
        if merged_mode_used == "manual_dequant_fp16":
            model_dtype = "float16"
            first_conversion = "f16"

        source_name = resume_base_model_path if resume_mode else model_id_or_path
        base_model_name = Path(str(source_name).rstrip("/\\")).name or "model"
        
        # Añadir timestamp (usando guión en lugar de dos puntos para compatibilidad con Windows)
        ts_suffix = datetime.now().strftime("_%Y-%m-%d_%H-%M")
        model_name_for_gguf = f"{base_model_name}{ts_suffix}"

        print(
            "Exportando modelo completo a GGUF (q4_k_m) desde carpeta mergeada "
            f"({merged_mode_used})..."
        )
        gguf_locations, _, _ = save_to_gguf(
            model_name = model_name_for_gguf,
            model_type = model_type,
            model_dtype = model_dtype,
            is_sentencepiece = False,
            model_directory = str(merged_model_dir),
            quantization_method = ["q4_k_m"],
            first_conversion = first_conversion,
            is_vlm = False,
            is_gpt_oss = False,
        )

        gguf_candidates = []
        for location in gguf_locations or []:
            gguf_path = Path(location)
            if gguf_path.exists() and gguf_path.suffix.lower() == ".gguf":
                gguf_candidates.append(gguf_path)

        merged_gguf_dir = Path(f"{merged_model_dir}_gguf")
        if merged_gguf_dir.exists():
            gguf_candidates.extend(list(merged_gguf_dir.glob("*.gguf")))

        gguf_candidates = [p for p in gguf_candidates if p.exists() and p.stat().st_mtime >= (export_start_ts - 5)]
        if not gguf_candidates:
            raise RuntimeError("No se encontró ningún GGUF generado tras la conversión.")

        unique_candidates = {}
        for candidate in gguf_candidates:
            unique_candidates[str(candidate.resolve())] = candidate
        gguf_candidates = sorted(unique_candidates.values(), key=lambda p: p.stat().st_mtime, reverse=True)

        preferred = [p for p in gguf_candidates if ".Q4_K_M" in p.name.upper()]
        final_gguf = preferred[0] if preferred else gguf_candidates[0]

        target_gguf = output_dir / final_gguf.name
        if final_gguf.resolve() != target_gguf.resolve():
            if target_gguf.exists():
                target_gguf.unlink()
            shutil.move(str(final_gguf), str(target_gguf))
            print(f"[EXPORT] GGUF movido a output_model: {target_gguf}")
        else:
            print(f"[EXPORT] GGUF ya generado en output_model: {target_gguf}")

        final_size = target_gguf.stat().st_size
        if final_size < MIN_VALID_GGUF_BYTES:
            target_gguf.unlink(missing_ok=True)
            raise RuntimeError(
                f"GGUF inválido detectado ({final_size} bytes). "
                "El archivo era demasiado pequeño y fue eliminado."
            )

        try:
            sidecar_meta_path = Path(str(target_gguf) + ".training.json")
            training_meta = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "base_model": str(model_id_or_path),
                "output_gguf": target_gguf.name,
                "rank": int(params.get("rank", 32)),
                "alpha": int(params.get("alpha", 64)),
                "requested_batch_size": int(params.get("batch_size", 2)),
                "requested_grad_accumulation_steps": int(
                    params.get("gradient_accumulation_steps", params.get("grad_accumulation_steps", 4))
                ),
                "per_device_batch_size": int(per_device_batch),
                "grad_accumulation_steps": int(grad_acc_steps),
                "effective_batch_size": int(per_device_batch * grad_acc_steps),
                "learning_rate": float(params.get("learning_rate", 2e-4)),
                "epochs": int(params.get("epochs", 3)),
                "max_seq_length": int(max_seq_length),
                "qwen35": bool(is_qwen35),
                "fla_available": bool(fla_available),
            }
            with open(sidecar_meta_path, "w", encoding="utf-8") as f:
                json.dump(training_meta, f, indent=2, ensure_ascii=False)
            print(f"[EXPORT] Metadata de entrenamiento guardada junto al GGUF: {sidecar_meta_path}")
        except Exception as meta_e:
            print(f"[WARN] No se pudo guardar metadata de entrenamiento junto al GGUF: {meta_e}")

        print(f"GGUF guardado en {output_dir}")
    except Exception as e:
        print(f"Error en guardado/exportacion final: {e}")
        if lora_saved_ok and adapters_dir.exists():
            status_file = run_output_dir / "GGUF_EXPORT_FAILED.txt"
            status_file.write_text(
                "Entrenamiento completado, pero la exportacion GGUF fallo.\n"
                f"Error: {e}\n"
                f"LoRA disponible en: {adapters_dir}\n"
                "Puedes relanzar solo la exportacion GGUF sin reentrenar.\n",
                encoding="utf-8",
            )
            print("[PARTIAL-SUCCESS] Entrenamiento OK, export GGUF fallida.")
            print(f"[PARTIAL-SUCCESS] Se conserva LoRA en: {adapters_dir}")
            print(f"[PARTIAL-SUCCESS] Detalle guardado en: {status_file}")
            raise RuntimeError(
                "Entrenamiento completado, pero la exportacion GGUF fallo. "
                f"Se conservó LoRA en: {adapters_dir}"
            ) from e

        print(f"Limpiando carpeta de salida fallida: {run_output_dir}")
        shutil.rmtree(run_output_dir, ignore_errors=True)
        raise

if __name__ == "__main__":
    # Argumentos: model_path dataset_path output_dir json_params
    if len(sys.argv) < 5:
        print("Error: Faltan argumentos.")
        sys.exit(1)
        
    model_arg = sys.argv[1]
    dataset_arg = sys.argv[2]
    output_arg = sys.argv[3]
    params_arg = sys.argv[4]
    
    # Decodificar params
    try:
        params = json.loads(params_arg.replace("'", '"')) # Simple fix for cmd quotes
    except:
        params = {}

    run_training(model_arg, Path(dataset_arg), Path(output_arg), params)

def run_training_internal(model_path, dataset_path, output_dir, params):
    """
    Wrapper para ser llamado directamente desde Python (multiprocessing)
    sin pasar por sys.argv.
    """
    return run_training(str(model_path), Path(dataset_path), Path(output_dir), params)
