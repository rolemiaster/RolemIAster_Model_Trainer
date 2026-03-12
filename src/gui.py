import os
import sys
import json
import glob
import traceback
from datetime import datetime
from pathlib import Path

# --- FIX: Dummy Stream for No-Console Mode (PyInstaller --windowed) ---
# En modo --windowed, sys.stdout/stderr son None y sys.__stdout__/__stderr__
# apuntan a handles de Windows inválidos. Librerías como unsloth, huggingface_hub
# y tqdm intentan escribir/flush en ellos, causando:
#   - NoneType crashes (si son None)
#   - OSError: [Errno 22] Invalid argument (si el handle existe pero es inválido)
# Parcheamos las 4 referencias redirigiendo a un archivo log.
_WINDOWED_MODE = sys.stdout is None or sys.stderr is None
if _WINDOWED_MODE:
    # Determinar ruta del log junto al ejecutable
    if getattr(sys, 'frozen', False):
        _log_path = os.path.join(os.path.dirname(sys.executable), 'crash_log.txt')
    else:
        _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'crash_log.txt')
    try:
        _log_file = open(_log_path, 'w', encoding='utf-8')
    except Exception:
        _log_file = None

    class _LogStream:
        encoding = 'utf-8'
        name = '<log_stream>'
        def __init__(self, log_file):
            self._log = log_file
        def write(self, data, *args, **kwargs):
            if self._log:
                try:
                    self._log.write(str(data))
                    self._log.flush()
                except Exception:
                    pass
        def flush(self, *args, **kwargs):
            if self._log:
                try:
                    self._log.flush()
                except Exception:
                    pass
        def isatty(self): return False
        def readable(self): return False
        def writable(self): return True
        def seekable(self): return False
        def fileno(self): raise OSError('LogStream no tiene file descriptor')
        def close(self): pass

    _log_stream = _LogStream(_log_file)
    if sys.stdout is None: sys.stdout = _log_stream
    if sys.stderr is None: sys.stderr = _log_stream
    # CRÍTICO: unsloth/import_fixes.py captura sys.__stdout__ como _original_stream
    # y llama .flush() sobre él. Si es un handle inválido de Windows → OSError.
    sys.__stdout__ = sys.stdout
    sys.__stderr__ = sys.stderr
# -----------------------------------------------------------

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QPushButton, QComboBox, 
                               QSpinBox, QDoubleSpinBox, QGroupBox, QTextEdit, 
                               QMessageBox, QTabWidget, QFileDialog, QLineEdit,
                               QListWidget, QListWidgetItem, QCheckBox, QInputDialog)
from PySide6.QtCore import Qt, QThread, Signal
import re

# Asegurar que el CWD es la raíz de model_trainer
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# En modo compilado, TRAINER_ROOT es donde está el ejecutable, no _MEIPASS
if getattr(sys, 'frozen', False):
    TRAINER_ROOT = Path(sys.executable).parent
else:
    TRAINER_ROOT = Path(__file__).parent.parent.absolute()

INPUT_DIR = TRAINER_ROOT / "input_model"
OUTPUT_DIR = TRAINER_ROOT / "output_model"
UI_CONFIG_PATH = TRAINER_ROOT / "trainer_ui_config.json"
# README se empaqueta en la raíz del ejecutable o en _MEIPASS según build_trainer.py
# En este caso build_trainer lo pone en "." (raíz app), así que en frozen está junto al exe.
README_PATH = TRAINER_ROOT / "README_UI.md" if getattr(sys, 'frozen', False) else TRAINER_ROOT / "README_UI.md"

# Añadir src al path para importar módulos internos
sys.path.append(str(TRAINER_ROOT / "src"))
if getattr(sys, 'frozen', False):
     # En frozen, src está dentro de _internal/src (ya en sys.path por defecto) o _MEIPASS
     pass

try:
    from core.trainer_engine import TrainerEngine
    from core.model_downloader import ModelDownloader
except ImportError as e:
    print(f"Error importando core: {e}")
    TrainerEngine = None
    ModelDownloader = None

try:
    from core.test_bench_engine import get_default_test_cases
except Exception:
    get_default_test_cases = None

class DownloadThread(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(bool, str)

    def __init__(self, repo_id, tr_func):
        super().__init__()
        self.repo_id = repo_id
        self.downloader = None
        self.tr = tr_func

    def run(self):
        self.log_signal.emit(self.tr("log_download_starting").format(repo_id=self.repo_id))
        try:
            if ModelDownloader is None:
                raise RuntimeError("ModelDownloader no está disponible en este entorno.")

            self.downloader = ModelDownloader(self.log_signal.emit)
            local_path = self.downloader.download_model(self.repo_id, str(INPUT_DIR))
            
            if local_path:
                self.finished_signal.emit(True, local_path)
            else:
                self.finished_signal.emit(False, self.tr("log_download_failed"))
        except Exception as e:
            self.log_signal.emit(self.tr("log_download_error").format(e=str(e)))
            self.finished_signal.emit(False, str(e))

class TrainerThread(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(bool, str)

    def __init__(self, md_path, model_path, params, tr_func):
        super().__init__()
        self.md_path = md_path
        self.model_path = model_path
        self.params = params
        self.engine = None
        self.tr = tr_func

    def run(self):
        self.log_signal.emit(self.tr("log_training_starting"))
        self.log_signal.emit(self.tr("log_training_rules").format(name=self.md_path.name))
        self.log_signal.emit(self.tr("log_training_model").format(name=self.model_path.name))
        
        try:
            if TrainerEngine is None:
                raise RuntimeError("TrainerEngine no está disponible en este entorno.")

            self.engine = TrainerEngine(self.log_signal.emit)
            resume_run_dir = self.params.get("resume_previous_run_dir")
            if resume_run_dir:
                success = self.engine.run_unsloth_subprocess(
                    model_path=self.model_path,
                    dataset_path=OUTPUT_DIR / "training_dataset.jsonl",
                    output_dir=OUTPUT_DIR,
                    params=self.params,
                )
            else:
                success = self.engine.train(
                    md_path=self.md_path,
                    model_path=self.model_path,
                    output_dir=OUTPUT_DIR,
                    params=self.params
                )
            if success:
                self.finished_signal.emit(True, self.tr("log_training_success"))
            else:
                self.finished_signal.emit(False, self.tr("log_training_failed"))
        except Exception as e:
            self.log_signal.emit(self.tr("log_unhandled_error").format(e=str(e)))
            self.finished_signal.emit(False, self.tr("log_critical_error").format(e=str(e)))

    def stop(self):
        if self.engine:
            self.engine.stop()


class TestBenchThread(QThread):
    log_signal = Signal(str)
    case_signal = Signal(dict)
    finished_signal = Signal(bool, object)

    def __init__(self, config, selected_case_ids, tr_func):
        super().__init__()
        self.config = dict(config)
        self.selected_case_ids = list(selected_case_ids)
        self.engine = None
        self.tr = tr_func

    def run(self):
        try:
            from core.test_bench_engine import TestBenchEngine

            self.log_signal.emit(self.tr("log_testbench_starting"))
            self.engine = TestBenchEngine(self.log_signal.emit)
            report = self.engine.run_suite(
                config=self.config,
                selected_case_ids=self.selected_case_ids,
                case_callback=self.case_signal.emit,
            )
            self.finished_signal.emit(True, report)
        except Exception as e:
            self.log_signal.emit(self.tr("log_testbench_error").format(e=str(e)))
            self.finished_signal.emit(False, {"error": str(e)})

    def stop(self):
        if self.engine:
            self.engine.request_stop()

class LoRATrainerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self._is_loading_config = True
        self.current_lang = "es"
        self.i18n = {}
        self.load_i18n()
        
        self.md_file = None
        self.model_file = None
        
        # Cola de trabajos: Lista de dicts {md, model, params, status, item_widget}
        self.job_queue = []
        self.is_queue_running = False
        self.current_job_index = -1

        self.test_bench_cases = []
        if callable(get_default_test_cases):
            self.test_bench_cases = get_default_test_cases()
        self.test_bench_report = None
        self.test_bench_thread = None
        self.test_bench_sequence_active = False
        self.test_bench_sequence_stop_requested = False
        self.test_bench_sequence_queue = []
        self.test_bench_sequence_reports = []
        self.test_bench_sequence_base_config = {}
        self.test_bench_sequence_case_ids = []
        self.thread = None
        self.download_thread = None
        
        self.init_ui()
        self.load_manual()
        self.scan_input_folder() # Intento inicial automático
        self.load_user_config()
        self._is_loading_config = False

    def load_i18n(self):
        # Usar resource_path para localizar i18n tanto en dev como en exe
        base_i18n = Path(resource_path("src/i18n"))
        i18n_path = base_i18n / f"{self.current_lang}.json"
        
        if i18n_path.exists():
            with open(i18n_path, 'r', encoding='utf-8') as f:
                self.i18n = json.load(f)
        else:
             print(f"Warning: Traducción no encontrada en {i18n_path}")

    def tr(self, key):
        return self.i18n.get(key, key)

    def load_user_config(self):
        if not UI_CONFIG_PATH.exists():
            return
        try:
            with open(UI_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            self.combo_engine.setCurrentIndex(int(cfg.get("engine_index", 0)))
            self.spin_rank.setValue(int(cfg.get("rank", self.spin_rank.value())))
            self.spin_alpha.setValue(int(cfg.get("alpha", self.spin_alpha.value())))
            self.spin_batch.setValue(int(cfg.get("batch_size", self.spin_batch.value())))
            self.spin_epochs.setValue(int(cfg.get("epochs", self.spin_epochs.value())))
            self.spin_lr.setValue(float(cfg.get("learning_rate", self.spin_lr.value())))

            if hasattr(self, "txt_tb_model"):
                self.txt_tb_model.setText(str(cfg.get("testbench_model_ref", "")).strip())
            if hasattr(self, "txt_tb_rules"):
                saved_rules_path = str(cfg.get("testbench_rules_path", "")).strip()
                if saved_rules_path:
                    self.txt_tb_rules.setText(saved_rules_path)
                elif not self.txt_tb_rules.text().strip():
                    resolved_rules = self._resolve_test_bench_rules_path()
                    if resolved_rules:
                        self.txt_tb_rules.setText(resolved_rules)
            if hasattr(self, "check_tb_fallback"):
                self.check_tb_fallback.setChecked(bool(cfg.get("testbench_enable_fallback", True)))
            if hasattr(self, "spin_tb_first_pass"):
                self.spin_tb_first_pass.setValue(float(cfg.get("testbench_min_first", self.spin_tb_first_pass.value())))
            if hasattr(self, "spin_tb_final"):
                self.spin_tb_final.setValue(float(cfg.get("testbench_min_final", self.spin_tb_final.value())))
            if hasattr(self, "spin_tb_fallback"):
                self.spin_tb_fallback.setValue(float(cfg.get("testbench_max_fallback", self.spin_tb_fallback.value())))
            if hasattr(self, "spin_tb_n_ctx"):
                self.spin_tb_n_ctx.setValue(int(cfg.get("testbench_n_ctx", self.spin_tb_n_ctx.value())))
            if hasattr(self, "spin_tb_max_tokens"):
                self.spin_tb_max_tokens.setValue(int(cfg.get("testbench_max_tokens", self.spin_tb_max_tokens.value())))
            if hasattr(self, "spin_tb_temperature"):
                self.spin_tb_temperature.setValue(float(cfg.get("testbench_temperature", self.spin_tb_temperature.value())))
            if hasattr(self, "spin_tb_case_count"):
                self.spin_tb_case_count.setValue(int(cfg.get("testbench_case_count", self.spin_tb_case_count.value())))
            if hasattr(self, "check_tb_narrative_gate"):
                self.check_tb_narrative_gate.setChecked(bool(cfg.get("testbench_enable_narrative_gate", True)))
            if hasattr(self, "spin_tb_narrative_min"):
                self.spin_tb_narrative_min.setValue(float(cfg.get("testbench_min_narrative_score", self.spin_tb_narrative_min.value())))
            if hasattr(self, "spin_tb_narrative_hard_fail"):
                self.spin_tb_narrative_hard_fail.setValue(float(cfg.get("testbench_max_narrative_hard_fail_rate", self.spin_tb_narrative_hard_fail.value())))
            if hasattr(self, "combo_tb_prompt_mode"):
                self._set_test_bench_prompt_mode(cfg.get("testbench_prompt_mode", "isolated"))
            if hasattr(self, "check_tb_run_all_modes"):
                self.check_tb_run_all_modes.setChecked(bool(cfg.get("testbench_run_all_modes", False)))

            selected_cases = cfg.get("testbench_selected_cases", None)
            if hasattr(self, "list_tb_cases") and isinstance(selected_cases, list):
                self._set_selected_test_case_ids(selected_cases)

            md_value = cfg.get("md_file", "")
            if md_value:
                md_path = Path(md_value)
                if md_path.exists():
                    self.set_md_file(md_path)

            model_value = cfg.get("model_file", "")
            if model_value:
                model_path = Path(model_value)
                if model_path.exists() or str(model_path).strip():
                    self.set_model_file(model_path)
        except Exception as e:
            print(f"Warning: No se pudo cargar la config UI: {e}")

    def save_user_config(self):
        try:
            cfg = {
                "engine_index": self.combo_engine.currentIndex(),
                "rank": self.spin_rank.value(),
                "alpha": self.spin_alpha.value(),
                "batch_size": self.spin_batch.value(),
                "epochs": self.spin_epochs.value(),
                "learning_rate": self.spin_lr.value(),
                "md_file": str(self.md_file) if self.md_file else "",
                "model_file": str(self.model_file) if self.model_file else "",
                "testbench_model_ref": self.txt_tb_model.text().strip() if hasattr(self, "txt_tb_model") else "",
                "testbench_rules_path": self.txt_tb_rules.text().strip() if hasattr(self, "txt_tb_rules") else "",
                "testbench_enable_fallback": self.check_tb_fallback.isChecked() if hasattr(self, "check_tb_fallback") else True,
                "testbench_min_first": self.spin_tb_first_pass.value() if hasattr(self, "spin_tb_first_pass") else 85.0,
                "testbench_min_final": self.spin_tb_final.value() if hasattr(self, "spin_tb_final") else 95.0,
                "testbench_max_fallback": self.spin_tb_fallback.value() if hasattr(self, "spin_tb_fallback") else 20.0,
                "testbench_n_ctx": self.spin_tb_n_ctx.value() if hasattr(self, "spin_tb_n_ctx") else 8192,
                "testbench_max_tokens": self.spin_tb_max_tokens.value() if hasattr(self, "spin_tb_max_tokens") else 768,
                "testbench_temperature": self.spin_tb_temperature.value() if hasattr(self, "spin_tb_temperature") else 0.2,
                "testbench_case_count": self.spin_tb_case_count.value() if hasattr(self, "spin_tb_case_count") else max(1, len(self.test_bench_cases)),
                "testbench_enable_narrative_gate": self.check_tb_narrative_gate.isChecked() if hasattr(self, "check_tb_narrative_gate") else True,
                "testbench_min_narrative_score": self.spin_tb_narrative_min.value() if hasattr(self, "spin_tb_narrative_min") else 70.0,
                "testbench_max_narrative_hard_fail_rate": self.spin_tb_narrative_hard_fail.value() if hasattr(self, "spin_tb_narrative_hard_fail") else 5.0,
                "testbench_prompt_mode": self._get_test_bench_prompt_mode() if hasattr(self, "combo_tb_prompt_mode") else "isolated",
                "testbench_run_all_modes": self.check_tb_run_all_modes.isChecked() if hasattr(self, "check_tb_run_all_modes") else False,
                "testbench_selected_cases": self._get_selected_test_case_ids() if hasattr(self, "list_tb_cases") else [],
            }
            with open(UI_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Warning: No se pudo guardar la config UI: {e}")

    def init_ui(self):
        self.setWindowTitle(self.tr("app_title"))
        self.setMinimumSize(800, 700)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # Header (Idioma)
        header_layout = QHBoxLayout()
        lang_label = QLabel(self.tr("language"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["es", "en"])
        self.lang_combo.setCurrentText(self.current_lang)
        self.lang_combo.currentTextChanged.connect(self.change_language)
        header_layout.addWidget(lang_label)
        header_layout.addWidget(self.lang_combo)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # Tabs
        self.tabs = QTabWidget()
        self.tab_trainer = QWidget()
        self.tab_test_bench = QWidget()
        self.tab_manual = QWidget()
        
        self.tabs.addTab(self.tab_trainer, self.tr("tab_trainer"))
        self.tabs.addTab(self.tab_test_bench, self.tr("tab_testbench"))
        self.tabs.addTab(self.tab_manual, self.tr("tab_manual"))
        main_layout.addWidget(self.tabs)
        
        self.init_trainer_tab()
        self.init_test_bench_tab()
        self.init_manual_tab()

    def init_trainer_tab(self):
        layout = QVBoxLayout(self.tab_trainer)
        
        # Input Section
        self.group_input = QGroupBox(self.tr("input_section"))
        input_layout = QVBoxLayout()
        
        # MD Selector
        md_layout = QHBoxLayout()
        self.lbl_md = QLabel(self.tr("md_found"))
        self.txt_md = QLineEdit()
        self.txt_md.setReadOnly(True)
        self.btn_md = QPushButton(self.tr("select_file"))
        self.btn_md.clicked.connect(self.select_md_file)
        md_layout.addWidget(self.lbl_md)
        md_layout.addWidget(self.txt_md)
        md_layout.addWidget(self.btn_md)
        input_layout.addLayout(md_layout)

        # Model Selector
        model_layout = QHBoxLayout()
        self.lbl_model = QLabel(self.tr("model_found"))
        
        self.combo_model = QComboBox()
        self.combo_model.setEditable(True)
        self.combo_model.setPlaceholderText("Ruta archivo local o ID HuggingFace (ej. unsloth/Qwen2.5-7B-bnb-4bit)")
        
        # Presets REALES (Repositorios Oficiales de Qwen - Modelos Base Puros)
        model_presets = [
            "", 
            # --- Qwen 3.5 (La nueva generación) ---
            "Qwen/Qwen3.5-35B-A3B",    # MoE (Ha subido de 32B a 35B en esta versión)
            "Qwen/Qwen3.5-27B",        # Nuevo tamaño (reemplaza al 14B/32B denso)
            "Qwen/Qwen3.5-9B",         # Nuevo tamaño (reemplaza al 8B)
            "Qwen/Qwen3.5-4B",         # El pequeño
            
            # --- Qwen 3 (La que usas actualmente) ---
            # Nota: Asumo la nomenclatura estándar basada en los tamaños que me diste.
            "Qwen/Qwen3-32B-A3B",      # El MoE de la v3
            "Qwen/Qwen3-14B",          # Denso
            "Qwen/Qwen3-8B",           # Denso
            "Qwen/Qwen3-4B",           # Denso/Híbrido
            
            # --- Qwen 2.5 (Legacy - Muy estables) ---
            "Qwen/Qwen2.5-32B",        
            "Qwen/Qwen2.5-14B",        
            "Qwen/Qwen2.5-7B",         
            "Qwen/Qwen2.5-3B",         
            "Qwen/Qwen2.5-1.5B",       
            "Qwen/Qwen2.5-0.5B",       
        ]
        self.combo_model.addItems(model_presets)
        self.combo_model.editTextChanged.connect(self.on_model_text_changed)
        
        self.btn_model = QPushButton(self.tr("select_file"))
        self.btn_model.clicked.connect(self.select_model_file)
        
        self.btn_download = QPushButton(self.tr("download_btn"))
        self.btn_download.clicked.connect(self.start_download)
        
        model_layout.addWidget(self.lbl_model)
        model_layout.addWidget(self.combo_model, 1) # Stretch para que ocupe espacio
        model_layout.addWidget(self.btn_download)
        model_layout.addWidget(self.btn_model)
        input_layout.addLayout(model_layout)
        
        self.group_input.setLayout(input_layout)
        layout.addWidget(self.group_input)
        
        # Parameters Section
        self.group_params = QGroupBox(self.tr("params_section"))
        params_layout = QVBoxLayout()
        
        # Engine Selector
        row_engine = QHBoxLayout()
        self.lbl_engine = QLabel(self.tr("engine_label"))
        self.combo_engine = QComboBox()
        self.combo_engine.addItems(["Unsloth (Auto)"]) 
        row_engine.addWidget(self.lbl_engine)
        row_engine.addWidget(self.combo_engine)
        
        params_layout.addLayout(row_engine)
        
        row1 = QHBoxLayout()
        row1.addWidget(QLabel(self.tr("lora_rank")))
        self.spin_rank = QSpinBox()
        self.spin_rank.setRange(8, 256)
        self.spin_rank.setValue(32)
        row1.addWidget(self.spin_rank)
        row1.addWidget(QLabel(self.tr("lora_alpha")))
        self.spin_alpha = QSpinBox()
        self.spin_alpha.setRange(8, 512)
        self.spin_alpha.setValue(64)
        row1.addWidget(self.spin_alpha)
        params_layout.addLayout(row1)
        
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(self.tr("batch_size")))
        self.spin_batch = QSpinBox()
        self.spin_batch.setRange(1, 64)
        self.spin_batch.setValue(2)
        row2.addWidget(self.spin_batch)
        row2.addWidget(QLabel(self.tr("epochs")))
        self.spin_epochs = QSpinBox()
        self.spin_epochs.setRange(1, 20)
        self.spin_epochs.setValue(3)
        row2.addWidget(self.spin_epochs)
        params_layout.addLayout(row2)
        
        row3 = QHBoxLayout()
        row3.addWidget(QLabel(self.tr("learning_rate")))
        self.spin_lr = QDoubleSpinBox()
        self.spin_lr.setDecimals(5)
        self.spin_lr.setRange(0.00001, 0.01)
        self.spin_lr.setSingleStep(0.0001)
        self.spin_lr.setValue(0.0002)
        row3.addWidget(self.spin_lr)
        row3.addStretch()
        params_layout.addLayout(row3)
        
        self.group_params.setLayout(params_layout)
        layout.addWidget(self.group_params)
        
        # Queue Section
        self.group_queue = QGroupBox(self.tr("queue_section"))
        queue_layout = QVBoxLayout()
        
        queue_buttons = QHBoxLayout()
        self.btn_add_queue = QPushButton(self.tr("add_queue_btn"))
        self.btn_add_queue.clicked.connect(self.add_to_queue)
        self.btn_remove_queue = QPushButton(self.tr("remove_queue_btn"))
        self.btn_remove_queue.clicked.connect(self.remove_from_queue)
        
        self.btn_start_queue = QPushButton(self.tr("start_queue_btn"))
        # Estilo botón iniciar cola: Azul con texto blanco
        self.btn_start_queue.setStyleSheet("font-weight: bold; font-size: 14px; background-color: #007bff; color: white; border-radius: 5px; padding: 5px;")
        self.btn_start_queue.clicked.connect(self.start_queue_processing)
        
        queue_buttons.addWidget(self.btn_add_queue)
        queue_buttons.addWidget(self.btn_remove_queue)
        queue_buttons.addWidget(self.btn_start_queue)
        queue_layout.addLayout(queue_buttons)
        
        self.list_queue = QListWidget()
        # Estilo lista: Texto blanco para tema oscuro
        self.list_queue.setStyleSheet("""
            QListWidget::item { color: white; padding: 5px; }
            QListWidget::item:selected { background-color: #555; }
        """)
        queue_layout.addWidget(self.list_queue)
        
        self.group_queue.setLayout(queue_layout)
        layout.addWidget(self.group_queue)
        
        # Process Button (Direct)
        self.btn_process = QPushButton(self.tr("process_btn"))
        self.btn_process.setMinimumHeight(40)
        # Estilo botón proceso: Verde con texto blanco
        self.btn_process.setStyleSheet("font-weight: bold; font-size: 14px; background-color: #28a745; color: white; border-radius: 5px; padding: 5px;")
        self.btn_process.clicked.connect(self.start_processing_direct)
        layout.addWidget(self.btn_process)
        
        # Console
        self.group_console = QGroupBox(self.tr("console_section"))
        console_layout = QVBoxLayout()
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        # Estilo consola: Fondo oscuro, texto claro
        self.console.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4; font-family: Consolas, Monospace;")
        console_layout.addWidget(self.console)
        self.group_console.setLayout(console_layout)
        layout.addWidget(self.group_console)

    def init_manual_tab(self):
        layout = QVBoxLayout(self.tab_manual)
        self.manual_viewer = QTextEdit()
        self.manual_viewer.setReadOnly(True)
        layout.addWidget(self.manual_viewer)

    def init_test_bench_tab(self):
        layout = QVBoxLayout(self.tab_test_bench)

        self.group_tb_target = QGroupBox(self.tr("testbench_target_section"))
        target_layout = QVBoxLayout()

        row_model = QHBoxLayout()
        self.lbl_tb_model = QLabel(self.tr("testbench_model_ref"))
        self.txt_tb_model = QLineEdit()
        self.txt_tb_model.setPlaceholderText(self.tr("testbench_model_placeholder"))
        self.txt_tb_model.textChanged.connect(self.update_test_bench_status)
        if self.model_file:
            self.txt_tb_model.setText(str(self.model_file))
        self.btn_tb_model_file = QPushButton(self.tr("testbench_select_file"))
        self.btn_tb_model_file.clicked.connect(self.select_testbench_model_file)
        # Eliminado selector de carpeta por petición del usuario (solo GGUF)
        
        row_model.addWidget(self.lbl_tb_model)
        row_model.addWidget(self.txt_tb_model, 1)
        row_model.addWidget(self.btn_tb_model_file)
        target_layout.addLayout(row_model)

        row_rules = QHBoxLayout()
        self.lbl_tb_rules = QLabel(self.tr("testbench_rules_path"))
        self.txt_tb_rules = QLineEdit()
        self.txt_tb_rules.setPlaceholderText(self.tr("testbench_rules_placeholder"))
        default_rules_path = INPUT_DIR / "reglas_base.md"
        if default_rules_path.exists():
            self.txt_tb_rules.setText(str(default_rules_path))
        self.btn_tb_rules = QPushButton(self.tr("testbench_select_file"))
        self.btn_tb_rules.clicked.connect(self.select_testbench_rules_file)
        row_rules.addWidget(self.lbl_tb_rules)
        row_rules.addWidget(self.txt_tb_rules, 1)
        row_rules.addWidget(self.btn_tb_rules)
        target_layout.addLayout(row_rules)

        self.group_tb_target.setLayout(target_layout)
        layout.addWidget(self.group_tb_target)

        self.group_tb_runtime = QGroupBox(self.tr("testbench_runtime_section"))
        runtime_layout = QVBoxLayout()

        row_rt_1 = QHBoxLayout()
        self.lbl_tb_n_ctx = QLabel(self.tr("testbench_n_ctx"))
        row_rt_1.addWidget(self.lbl_tb_n_ctx)
        self.spin_tb_n_ctx = QSpinBox()
        self.spin_tb_n_ctx.setRange(1024, 32768)
        self.spin_tb_n_ctx.setValue(8192)
        row_rt_1.addWidget(self.spin_tb_n_ctx)

        self.lbl_tb_max_tokens = QLabel(self.tr("testbench_max_tokens"))
        row_rt_1.addWidget(self.lbl_tb_max_tokens)
        self.spin_tb_max_tokens = QSpinBox()
        self.spin_tb_max_tokens.setRange(64, 4096)
        self.spin_tb_max_tokens.setValue(768)
        row_rt_1.addWidget(self.spin_tb_max_tokens)

        self.lbl_tb_temperature = QLabel(self.tr("testbench_temperature"))
        row_rt_1.addWidget(self.lbl_tb_temperature)
        self.spin_tb_temperature = QDoubleSpinBox()
        self.spin_tb_temperature.setRange(0.0, 1.5)
        self.spin_tb_temperature.setDecimals(2)
        self.spin_tb_temperature.setSingleStep(0.05)
        self.spin_tb_temperature.setValue(0.2)
        row_rt_1.addWidget(self.spin_tb_temperature)
        runtime_layout.addLayout(row_rt_1)

        row_rt_2 = QHBoxLayout()
        self.check_tb_fallback = QCheckBox(self.tr("testbench_enable_fallback"))
        self.check_tb_fallback.setChecked(True)
        row_rt_2.addWidget(self.check_tb_fallback)
        self.lbl_tb_prompt_mode = QLabel(self.tr("testbench_prompt_mode_label"))
        row_rt_2.addWidget(self.lbl_tb_prompt_mode)
        self.combo_tb_prompt_mode = QComboBox()
        self.combo_tb_prompt_mode.setMinimumWidth(290)
        row_rt_2.addWidget(self.combo_tb_prompt_mode)
        self._populate_test_bench_prompt_modes()
        self.check_tb_run_all_modes = QCheckBox(self.tr("testbench_run_all_modes"))
        self.check_tb_run_all_modes.setChecked(False)
        row_rt_2.addWidget(self.check_tb_run_all_modes)
        row_rt_2.addStretch()
        runtime_layout.addLayout(row_rt_2)

        row_rt_3 = QHBoxLayout()
        self.lbl_tb_case_count = QLabel(self.tr("testbench_case_count"))
        row_rt_3.addWidget(self.lbl_tb_case_count)
        self.spin_tb_case_count = QSpinBox()
        self.spin_tb_case_count.setRange(1, 5000)
        self.spin_tb_case_count.setValue(max(1, len(self.test_bench_cases) if self.test_bench_cases else 6))
        row_rt_3.addWidget(self.spin_tb_case_count)
        row_rt_3.addStretch()
        runtime_layout.addLayout(row_rt_3)

        row_thresholds = QHBoxLayout()
        self.lbl_tb_min_first = QLabel(self.tr("testbench_min_first_pass"))
        row_thresholds.addWidget(self.lbl_tb_min_first)
        self.spin_tb_first_pass = QDoubleSpinBox()
        self.spin_tb_first_pass.setRange(0.0, 100.0)
        self.spin_tb_first_pass.setDecimals(1)
        self.spin_tb_first_pass.setValue(85.0)
        self.spin_tb_first_pass.setSuffix("%")
        row_thresholds.addWidget(self.spin_tb_first_pass)

        self.lbl_tb_min_final = QLabel(self.tr("testbench_min_final"))
        row_thresholds.addWidget(self.lbl_tb_min_final)
        self.spin_tb_final = QDoubleSpinBox()
        self.spin_tb_final.setRange(0.0, 100.0)
        self.spin_tb_final.setDecimals(1)
        self.spin_tb_final.setValue(95.0)
        self.spin_tb_final.setSuffix("%")
        row_thresholds.addWidget(self.spin_tb_final)

        self.lbl_tb_max_fallback = QLabel(self.tr("testbench_max_fallback"))
        row_thresholds.addWidget(self.lbl_tb_max_fallback)
        self.spin_tb_fallback = QDoubleSpinBox()
        self.spin_tb_fallback.setRange(0.0, 100.0)
        self.spin_tb_fallback.setDecimals(1)
        self.spin_tb_fallback.setValue(20.0)
        self.spin_tb_fallback.setSuffix("%")
        row_thresholds.addWidget(self.spin_tb_fallback)
        runtime_layout.addLayout(row_thresholds)

        row_narrative = QHBoxLayout()
        self.check_tb_narrative_gate = QCheckBox(self.tr("testbench_enable_narrative_gate"))
        self.check_tb_narrative_gate.setChecked(True)
        row_narrative.addWidget(self.check_tb_narrative_gate)
        self.lbl_tb_narrative_min = QLabel(self.tr("testbench_min_narrative_score"))
        row_narrative.addWidget(self.lbl_tb_narrative_min)
        self.spin_tb_narrative_min = QDoubleSpinBox()
        self.spin_tb_narrative_min.setRange(0.0, 100.0)
        self.spin_tb_narrative_min.setDecimals(1)
        self.spin_tb_narrative_min.setValue(70.0)
        self.spin_tb_narrative_min.setSuffix("%")
        row_narrative.addWidget(self.spin_tb_narrative_min)
        self.lbl_tb_narrative_hard_fail = QLabel(self.tr("testbench_max_narrative_hard_fail_rate"))
        row_narrative.addWidget(self.lbl_tb_narrative_hard_fail)
        self.spin_tb_narrative_hard_fail = QDoubleSpinBox()
        self.spin_tb_narrative_hard_fail.setRange(0.0, 100.0)
        self.spin_tb_narrative_hard_fail.setDecimals(1)
        self.spin_tb_narrative_hard_fail.setValue(5.0)
        self.spin_tb_narrative_hard_fail.setSuffix("%")
        row_narrative.addWidget(self.spin_tb_narrative_hard_fail)
        row_narrative.addStretch()
        runtime_layout.addLayout(row_narrative)

        self.group_tb_runtime.setLayout(runtime_layout)
        layout.addWidget(self.group_tb_runtime)

        self.group_tb_cases = QGroupBox(self.tr("testbench_cases_section"))
        cases_layout = QVBoxLayout()
        self.list_tb_cases = QListWidget()
        self.list_tb_cases.setSelectionMode(QListWidget.NoSelection)
        cases_layout.addWidget(self.list_tb_cases)
        self.group_tb_cases.setLayout(cases_layout)
        layout.addWidget(self.group_tb_cases)

        self.group_tb_actions = QGroupBox(self.tr("testbench_actions_section"))
        actions_layout = QHBoxLayout()
        self.btn_tb_run = QPushButton(self.tr("testbench_run_btn"))
        self.btn_tb_run.clicked.connect(self.start_test_bench)
        self.btn_tb_stop = QPushButton(self.tr("testbench_stop_btn"))
        self.btn_tb_stop.clicked.connect(self.stop_test_bench)
        self.btn_tb_stop.setEnabled(False)
        self.btn_tb_export = QPushButton(self.tr("testbench_export_btn"))
        self.btn_tb_export.clicked.connect(self.export_test_bench_report)
        self.btn_tb_export.setEnabled(False)
        actions_layout.addWidget(self.btn_tb_run)
        actions_layout.addWidget(self.btn_tb_stop)
        actions_layout.addWidget(self.btn_tb_export)
        actions_layout.addStretch()
        self.group_tb_actions.setLayout(actions_layout)
        layout.addWidget(self.group_tb_actions)

        self.lbl_tb_summary = QLabel(self.tr("testbench_summary_empty"))
        layout.addWidget(self.lbl_tb_summary)

        self.group_tb_results = QGroupBox(self.tr("testbench_results_section"))
        results_layout = QVBoxLayout()
        self.txt_tb_output = QTextEdit()
        self.txt_tb_output.setReadOnly(True)
        self.txt_tb_output.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4; font-family: Consolas, Monospace;")
        results_layout.addWidget(self.txt_tb_output)
        self.group_tb_results.setLayout(results_layout)
        layout.addWidget(self.group_tb_results)

        self._populate_test_bench_cases()
        self.update_test_bench_status()

    def _log_test_bench_diagnostics(self, payload):
        diagnostics = payload.get("diagnostics", {}) or {}
        first_fail = diagnostics.get("first_pass_fail_by_check", []) or []
        final_fail = diagnostics.get("final_fail_by_check", []) or []
        first_parse_failures = diagnostics.get("first_pass_json_parse_failures", 0)
        final_parse_failures = diagnostics.get("final_json_parse_failures", 0)
        narrative_gate_enabled = bool(diagnostics.get("narrative_gate_enabled", False))
        prompt_mode = str(payload.get("prompt_mode", diagnostics.get("prompt_mode", "isolated")))

        self.log_test_bench(f"[diagnostics] prompt_mode={prompt_mode}")

        self.log_test_bench(
            f"[diagnostics] first_pass_json_parse_failures={first_parse_failures} | final_json_parse_failures={final_parse_failures}"
        )
        if narrative_gate_enabled:
            self.log_test_bench(
                "[diagnostics] narrative_gate="
                + f"on | final_avg={diagnostics.get('final_narrative_avg_score', 0)} "
                + f"| final_hard_fail_rate={diagnostics.get('final_narrative_hard_fail_rate', 0)}% "
                + f"| min_score={diagnostics.get('narrative_min_score', 0)} "
                + f"| max_hard_fail_rate={diagnostics.get('narrative_max_hard_fail_rate', 0)}%"
            )
        if first_fail:
            compact_first = ", ".join(
                f"{item.get('check', 'unknown')}:{item.get('count', 0)}"
                for item in first_fail
                if isinstance(item, dict)
            )
            self.log_test_bench(f"[diagnostics] first_pass_fail_by_check={compact_first}")
        if final_fail:
            compact_final = ", ".join(
                f"{item.get('check', 'unknown')}:{item.get('count', 0)}"
                for item in final_fail
                if isinstance(item, dict)
            )
            self.log_test_bench(f"[diagnostics] final_fail_by_check={compact_final}")

        reinforcement_samples = payload.get("reinforcement_samples", []) or []
        if reinforcement_samples:
            self.log_test_bench(f"[diagnostics] reinforcement_samples={len(reinforcement_samples)}")

    def _populate_test_bench_cases(self):
        self.list_tb_cases.clear()

        if not self.test_bench_cases:
            item = QListWidgetItem(self.tr("testbench_no_cases"))
            item.setFlags(Qt.ItemIsEnabled)
            self.list_tb_cases.addItem(item)
            return

        for case in self.test_bench_cases:
            item = QListWidgetItem(f"{case.case_id} — {case.title}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, case.case_id)
            self.list_tb_cases.addItem(item)

    def _get_selected_test_case_ids(self):
        selected_ids = []
        for idx in range(self.list_tb_cases.count()):
            item = self.list_tb_cases.item(idx)
            if item and item.checkState() == Qt.Checked:
                selected_ids.append(item.data(Qt.UserRole))
        return [x for x in selected_ids if x]

    def _set_selected_test_case_ids(self, case_ids):
        selected = {str(x) for x in (case_ids or []) if str(x).strip()}
        for idx in range(self.list_tb_cases.count()):
            item = self.list_tb_cases.item(idx)
            if not item:
                continue
            case_id = item.data(Qt.UserRole)
            if not case_id:
                continue
            item.setCheckState(Qt.Checked if str(case_id) in selected else Qt.Unchecked)

    def _populate_test_bench_prompt_modes(self):
        if not hasattr(self, "combo_tb_prompt_mode"):
            return

        selected_mode = self._get_test_bench_prompt_mode()
        self.combo_tb_prompt_mode.blockSignals(True)
        self.combo_tb_prompt_mode.clear()
        self.combo_tb_prompt_mode.addItem(self.tr("testbench_prompt_mode_isolated"), "isolated")
        self.combo_tb_prompt_mode.addItem(self.tr("testbench_prompt_mode_compact"), "compact")
        self.combo_tb_prompt_mode.addItem(self.tr("testbench_prompt_mode_full"), "full")
        self.combo_tb_prompt_mode.blockSignals(False)
        self._set_test_bench_prompt_mode(selected_mode)

    def _get_test_bench_prompt_mode(self):
        if not hasattr(self, "combo_tb_prompt_mode"):
            return "isolated"

        value = self.combo_tb_prompt_mode.currentData()
        if isinstance(value, str) and value.strip():
            return value.strip().lower()

        text = self.combo_tb_prompt_mode.currentText().strip().lower()
        if "compact" in text or "compacto" in text:
            return "compact"
        if "full" in text or "completo" in text:
            return "full"
        return "isolated"

    def _set_test_bench_prompt_mode(self, mode):
        if not hasattr(self, "combo_tb_prompt_mode"):
            return

        normalized = str(mode or "isolated").strip().lower()
        idx = self.combo_tb_prompt_mode.findData(normalized)
        if idx < 0:
            idx = self.combo_tb_prompt_mode.findData("isolated")
        if idx >= 0:
            self.combo_tb_prompt_mode.setCurrentIndex(idx)

    def _get_prompt_mode_label(self, mode):
        normalized = str(mode or "isolated").strip().lower()
        mapping = {
            "isolated": self.tr("testbench_prompt_mode_isolated"),
            "compact": self.tr("testbench_prompt_mode_compact"),
            "full": self.tr("testbench_prompt_mode_full"),
        }
        return mapping.get(normalized, normalized)

    def _get_test_bench_sequence_modes(self):
        return ["isolated", "compact", "full"]

    def _resolve_test_bench_rules_path(self):
        candidates = []

        if hasattr(self, "txt_tb_rules"):
            raw_value = self.txt_tb_rules.text().strip()
            if raw_value:
                candidates.append(raw_value)

        if self.md_file:
            candidates.append(str(self.md_file))

        candidates.append(str(INPUT_DIR / "reglas_base.md"))

        for candidate in candidates:
            if not candidate:
                continue
            path = Path(candidate)
            if path.exists() and path.is_file():
                return str(path)

        return candidates[0] if candidates else ""

    def _build_test_bench_config(self):
        engine_idx = self.combo_engine.currentIndex() if hasattr(self, "combo_engine") else 0
        engine_map = {0: "auto", 1: "cuda", 2: "vulkan"}
        rules_path = self._resolve_test_bench_rules_path()

        if hasattr(self, "txt_tb_rules") and rules_path and not self.txt_tb_rules.text().strip():
            self.txt_tb_rules.setText(rules_path)

        return {
            "model_ref": self.txt_tb_model.text().strip(),
            "rules_md_path": rules_path,
            "session_dump_path": str(TRAINER_ROOT / "session_test.txt"),
            "n_ctx": self.spin_tb_n_ctx.value(),
            "max_tokens": self.spin_tb_max_tokens.value(),
            "temperature": float(self.spin_tb_temperature.value()),
            "engine": engine_map.get(engine_idx, "auto"),
            "prompt_mode": self._get_test_bench_prompt_mode(),
            "target_case_count": int(self.spin_tb_case_count.value()) if hasattr(self, "spin_tb_case_count") else len(self._get_selected_test_case_ids()),
            "enable_narrative_gate": self.check_tb_narrative_gate.isChecked() if hasattr(self, "check_tb_narrative_gate") else True,
            "min_narrative_score": float(self.spin_tb_narrative_min.value()) if hasattr(self, "spin_tb_narrative_min") else 70.0,
            "max_narrative_hard_fail_rate": float(self.spin_tb_narrative_hard_fail.value()) if hasattr(self, "spin_tb_narrative_hard_fail") else 5.0,
            "enable_fallback": self.check_tb_fallback.isChecked(),
            "min_first_pass_rate": float(self.spin_tb_first_pass.value()),
            "min_final_rate": float(self.spin_tb_final.value()),
            "max_fallback_rate": float(self.spin_tb_fallback.value()),
        }

    def select_testbench_model_file(self):
        fname, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("testbench_select_model_title"),
            str(INPUT_DIR),
            "GGUF Files (*.gguf);;All Files (*)",
        )
        if fname:
            self.txt_tb_model.setText(fname)
            self.update_test_bench_status()

    def select_testbench_rules_file(self):
        fname, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("testbench_select_rules_title"),
            str(INPUT_DIR),
            "Markdown Files (*.md);;All Files (*)",
        )
        if fname:
            self.txt_tb_rules.setText(fname)

    def log_test_bench(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.txt_tb_output.append(f"[{timestamp}] {str(message)}")
        self.txt_tb_output.verticalScrollBar().setValue(self.txt_tb_output.verticalScrollBar().maximum())

    def start_test_bench(self):
        if self.test_bench_thread and self.test_bench_thread.isRunning():
            return

        config = self._build_test_bench_config()
        selected_case_ids = self._get_selected_test_case_ids()

        if not config.get("model_ref"):
            QMessageBox.warning(self, self.tr("testbench_error_title"), self.tr("testbench_error_model_missing"))
            return
        model_ref = str(config.get("model_ref", "")).strip().lower()
        if not model_ref.endswith(".gguf"):
            QMessageBox.warning(self, self.tr("testbench_error_title"), self.tr("testbench_error_model_not_gguf"))
            return
        if not selected_case_ids:
            QMessageBox.warning(self, self.tr("testbench_error_title"), self.tr("testbench_error_cases_missing"))
            return

        if not self.test_bench_cases:
            QMessageBox.warning(self, self.tr("testbench_error_title"), self.tr("testbench_no_cases"))
            return

        self.test_bench_report = None
        self.txt_tb_output.clear()
        self.lbl_tb_summary.setText(self.tr("testbench_running"))
        run_all_modes = bool(getattr(self, "check_tb_run_all_modes", None) and self.check_tb_run_all_modes.isChecked())

        if run_all_modes:
            self.test_bench_sequence_active = True
            self.test_bench_sequence_stop_requested = False
            self.test_bench_sequence_queue = self._get_test_bench_sequence_modes()
            self.test_bench_sequence_reports = []
            self.test_bench_sequence_base_config = dict(config)
            self.test_bench_sequence_case_ids = list(selected_case_ids)
            self.log_test_bench(self.tr("testbench_sequence_starting_log"))
            self._run_next_test_bench_sequence()
        else:
            self.test_bench_sequence_active = False
            self.test_bench_sequence_stop_requested = False
            self.test_bench_sequence_queue = []
            self.test_bench_sequence_reports = []
            self.test_bench_sequence_base_config = {}
            self.test_bench_sequence_case_ids = []
            self.log_test_bench(self.tr("testbench_starting_log"))
            self.log_test_bench(f"[config] prompt_mode={config.get('prompt_mode', 'isolated')}")
            self.log_test_bench(f"[config] target_case_count={config.get('target_case_count')}")
            self.log_test_bench(
                "[config] narrative_gate="
                + f"{config.get('enable_narrative_gate')} | min_narrative_score={config.get('min_narrative_score')} "
                + f"| max_narrative_hard_fail_rate={config.get('max_narrative_hard_fail_rate')}"
            )
            self._start_test_bench_thread(config=config, selected_case_ids=selected_case_ids)

        self.update_test_bench_status()

    def _start_test_bench_thread(self, config, selected_case_ids):
        self.test_bench_thread = TestBenchThread(config=config, selected_case_ids=selected_case_ids, tr_func=self.tr)
        self.test_bench_thread.log_signal.connect(self.log_test_bench)
        self.test_bench_thread.case_signal.connect(self.on_test_bench_case_result)
        self.test_bench_thread.finished_signal.connect(self.on_test_bench_finished)
        self.test_bench_thread.start()

    def _run_next_test_bench_sequence(self):
        if self.test_bench_sequence_stop_requested:
            self._finalize_test_bench_sequence()
            return

        if not self.test_bench_sequence_queue:
            self._finalize_test_bench_sequence()
            return

        mode = self.test_bench_sequence_queue.pop(0)
        cfg = dict(self.test_bench_sequence_base_config or {})
        cfg["prompt_mode"] = mode

        mode_label = self._get_prompt_mode_label(mode)
        self.log_test_bench(self.tr("testbench_sequence_mode_log").format(mode=mode_label))
        self.log_test_bench(f"[config] prompt_mode={mode}")
        self.log_test_bench(f"[config] target_case_count={cfg.get('target_case_count')}")
        self.log_test_bench(
            "[config] narrative_gate="
            + f"{cfg.get('enable_narrative_gate')} | min_narrative_score={cfg.get('min_narrative_score')} "
            + f"| max_narrative_hard_fail_rate={cfg.get('max_narrative_hard_fail_rate')}"
        )

        self._start_test_bench_thread(config=cfg, selected_case_ids=self.test_bench_sequence_case_ids)
        self.update_test_bench_status()

    def _finalize_test_bench_sequence(self):
        reports = [r for r in self.test_bench_sequence_reports if isinstance(r, dict)]
        stopped = bool(self.test_bench_sequence_stop_requested)

        self.test_bench_sequence_active = False
        self.test_bench_sequence_stop_requested = False
        self.test_bench_sequence_queue = []
        self.test_bench_sequence_base_config = {}
        self.test_bench_sequence_case_ids = []

        if not reports:
            if stopped:
                self.lbl_tb_summary.setText(self.tr("testbench_sequence_aborted"))
                self.log_test_bench(self.tr("testbench_sequence_aborted_log"))
            self.test_bench_report = None
            self.update_test_bench_status()
            return

        mode_rows = []
        for report in reports:
            mode = str(report.get("prompt_mode", "isolated"))
            mode_rows.append(
                {
                    "mode": mode,
                    "label": self._get_prompt_mode_label(mode),
                    "first_pass_rate": float(report.get("first_pass_rate", 0.0) or 0.0),
                    "final_rate": float(report.get("final_rate", 0.0) or 0.0),
                    "fallback_rate": float(report.get("fallback_rate", 0.0) or 0.0),
                    "verdict": bool(report.get("verdict", False)),
                    "total": int(report.get("total", 0) or 0),
                }
            )

        details = " | ".join(
            f"{row['mode']}: 1ª={row['first_pass_rate']:.2f}% / final={row['final_rate']:.2f}%"
            for row in mode_rows
        )
        self.lbl_tb_summary.setText(
            self.tr("testbench_summary_multi_format").format(details=details)
        )

        reinforcement_samples = []
        for report in reports:
            mode = str(report.get("prompt_mode", "isolated"))
            for sample in report.get("reinforcement_samples", []) or []:
                if isinstance(sample, dict):
                    tagged = dict(sample)
                    tagged["prompt_mode"] = mode
                    reinforcement_samples.append(tagged)

        count = len(mode_rows)
        avg_first = sum(r["first_pass_rate"] for r in mode_rows) / count
        avg_final = sum(r["final_rate"] for r in mode_rows) / count
        avg_fallback = sum(r["fallback_rate"] for r in mode_rows) / count

        self.test_bench_report = {
            "multi_mode": True,
            "mode_order": [r["mode"] for r in mode_rows],
            "runs": reports,
            "summary_by_mode": mode_rows,
            "average_first_pass_rate": round(avg_first, 2),
            "average_final_rate": round(avg_final, 2),
            "average_fallback_rate": round(avg_fallback, 2),
            "verdict": all(bool(r.get("verdict", False)) for r in reports),
            "reinforcement_samples": reinforcement_samples,
        }

        if stopped:
            self.log_test_bench(self.tr("testbench_sequence_aborted_log"))
        else:
            self.log_test_bench(self.tr("testbench_sequence_finished_log"))
        self.test_bench_sequence_reports = []
        self.update_test_bench_status()

    def stop_test_bench(self):
        if self.test_bench_thread and self.test_bench_thread.isRunning():
            if self.test_bench_sequence_active:
                self.test_bench_sequence_stop_requested = True
                self.test_bench_sequence_queue = []
            self.test_bench_thread.stop()
            self.log_test_bench(self.tr("testbench_stop_requested"))

    def on_test_bench_case_result(self, result):
        case_id = result.get("case_id", "unknown")
        passed_first = bool(result.get("passed_first", False))
        passed_final = bool(result.get("passed_final", False))
        fallback_used = bool(result.get("fallback_used", False))

        status = self.tr("testbench_case_ok") if passed_final else self.tr("testbench_case_fail")
        first_status = self.tr("testbench_case_ok") if passed_first else self.tr("testbench_case_fail")
        fallback_text = self.tr("testbench_case_fallback_yes") if fallback_used else self.tr("testbench_case_fallback_no")

        self.log_test_bench(
            self.tr("testbench_case_log_format").format(
                case_id=case_id,
                final_status=status,
                first_status=first_status,
                fallback=fallback_text,
            )
        )

        final_parse_meta = result.get("final_parse_meta", {}) or {}
        parse_source = str(final_parse_meta.get("source", "") or "").strip()
        if parse_source:
            self.log_test_bench(f"  parse_source={parse_source}")

        failed_checks = [
            str(detail.get("check", ""))
            for detail in (result.get("final_checks", []) or [])
            if isinstance(detail, dict) and not bool(detail.get("ok", False))
        ]
        if failed_checks:
            self.log_test_bench(f"  failed_checks={', '.join(x for x in failed_checks if x)}")

        errors = result.get("errors", []) or []
        for err in errors:
            self.log_test_bench(f"  - {err}")

    def on_test_bench_finished(self, success, payload):
        self.test_bench_thread = None

        if self.test_bench_sequence_active:
            if success and isinstance(payload, dict):
                self.test_bench_sequence_reports.append(payload)
                mode = str(payload.get("prompt_mode", "isolated"))
                verdict = self.tr("testbench_verdict_pass") if payload.get("verdict") else self.tr("testbench_verdict_fail")
                self.log_test_bench(
                    self.tr("testbench_sequence_mode_result").format(
                        mode=self._get_prompt_mode_label(mode),
                        verdict=verdict,
                        first=payload.get("first_pass_rate", 0),
                        final=payload.get("final_rate", 0),
                        fallback=payload.get("fallback_rate", 0),
                    )
                )
                self._log_test_bench_diagnostics(payload)
            else:
                error_msg = ""
                if isinstance(payload, dict):
                    error_msg = payload.get("error", "")
                self.log_test_bench(self.tr("testbench_summary_error").format(error=error_msg or "unknown"))
                self.test_bench_sequence_stop_requested = True

            if self.test_bench_sequence_stop_requested or not self.test_bench_sequence_queue:
                self._finalize_test_bench_sequence()
            else:
                self._run_next_test_bench_sequence()
            return

        if success and isinstance(payload, dict):
            self.test_bench_report = payload
            verdict = self.tr("testbench_verdict_pass") if payload.get("verdict") else self.tr("testbench_verdict_fail")
            self.lbl_tb_summary.setText(
                self.tr("testbench_summary_format").format(
                    verdict=verdict,
                    first=payload.get("first_pass_rate", 0),
                    final=payload.get("final_rate", 0),
                    fallback=payload.get("fallback_rate", 0),
                    total=payload.get("total", 0),
                )
            )
            self.log_test_bench(self.tr("testbench_finished_log"))
            self._log_test_bench_diagnostics(payload)
        else:
            error_msg = ""
            if isinstance(payload, dict):
                error_msg = payload.get("error", "")
            self.lbl_tb_summary.setText(self.tr("testbench_summary_error").format(error=error_msg or "unknown"))

        self.update_test_bench_status()

    def export_test_bench_report(self):
        if not isinstance(self.test_bench_report, dict):
            return

        export_dir = OUTPUT_DIR / "test_bench_reports"
        export_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = export_dir / f"test_bench_report_{ts}.json"
        with open(target, "w", encoding="utf-8") as f:
            json.dump(self.test_bench_report, f, ensure_ascii=False, indent=2)
        self.log_test_bench(f"{self.tr('testbench_export_done')} {target}")

        reinforcement_samples = self.test_bench_report.get("reinforcement_samples", []) or []
        if reinforcement_samples:
            target_jsonl = export_dir / f"test_bench_reinforcement_{ts}.jsonl"
            with open(target_jsonl, "w", encoding="utf-8") as f:
                for row in reinforcement_samples:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.log_test_bench(f"{self.tr('testbench_export_done')} {target_jsonl}")

    def update_test_bench_status(self):
        running = bool(self.test_bench_thread and self.test_bench_thread.isRunning())
        has_model = bool(getattr(self, "txt_tb_model", None) and self.txt_tb_model.text().strip())

        if hasattr(self, "btn_tb_run"):
            self.btn_tb_run.setEnabled((not running) and has_model)
        if hasattr(self, "btn_tb_stop"):
            self.btn_tb_stop.setEnabled(running)
        if hasattr(self, "btn_tb_export"):
            self.btn_tb_export.setEnabled((not running) and isinstance(self.test_bench_report, dict))

        if hasattr(self, "txt_tb_model"):
            self.txt_tb_model.setEnabled(not running)
        if hasattr(self, "btn_tb_model_file"):
            self.btn_tb_model_file.setEnabled(not running)
        if hasattr(self, "txt_tb_rules"):
            self.txt_tb_rules.setEnabled(not running)
        if hasattr(self, "btn_tb_rules"):
            self.btn_tb_rules.setEnabled(not running)
        if hasattr(self, "list_tb_cases"):
            self.list_tb_cases.setEnabled(not running)
        if hasattr(self, "spin_tb_n_ctx"):
            self.spin_tb_n_ctx.setEnabled(not running)
        if hasattr(self, "spin_tb_max_tokens"):
            self.spin_tb_max_tokens.setEnabled(not running)
        if hasattr(self, "spin_tb_temperature"):
            self.spin_tb_temperature.setEnabled(not running)
        if hasattr(self, "spin_tb_case_count"):
            self.spin_tb_case_count.setEnabled(not running)
        if hasattr(self, "check_tb_narrative_gate"):
            self.check_tb_narrative_gate.setEnabled(not running)
        if hasattr(self, "spin_tb_narrative_min"):
            self.spin_tb_narrative_min.setEnabled(not running)
        if hasattr(self, "spin_tb_narrative_hard_fail"):
            self.spin_tb_narrative_hard_fail.setEnabled(not running)
        if hasattr(self, "check_tb_fallback"):
            self.check_tb_fallback.setEnabled(not running)
        if hasattr(self, "combo_tb_prompt_mode"):
            self.combo_tb_prompt_mode.setEnabled(not running)
        if hasattr(self, "check_tb_run_all_modes"):
            self.check_tb_run_all_modes.setEnabled(not running)
        if hasattr(self, "spin_tb_first_pass"):
            self.spin_tb_first_pass.setEnabled(not running)
        if hasattr(self, "spin_tb_final"):
            self.spin_tb_final.setEnabled(not running)
        if hasattr(self, "spin_tb_fallback"):
            self.spin_tb_fallback.setEnabled(not running)

    def load_manual(self):
        if not README_PATH.exists():
            self.manual_viewer.setText(self.tr("manual_error"))
            return

        try:
            with open(README_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if self.current_lang == "es":
                start_marker = "# MANUAL DE USO (ES)"
                end_marker = "# USER MANUAL (EN)"
            else:
                start_marker = "# USER MANUAL (EN)"
                end_marker = None
                
            start_idx = content.find(start_marker)
            if start_idx != -1:
                if end_marker:
                    end_idx = content.find(end_marker)
                    text = content[start_idx:end_idx].strip() if end_idx != -1 else content[start_idx:].strip()
                else:
                    text = content[start_idx:].strip()
                
                if text.endswith("---"):
                    text = text[:-3].strip()
                    
                self.manual_viewer.setMarkdown(text)
            else:
                self.manual_viewer.setMarkdown(content)

        except Exception as e:
            self.manual_viewer.setText(f"{self.tr('manual_error')} {e}")

    def change_language(self, lang):
        self.current_lang = lang
        self.load_i18n()
        self.update_ui_texts()
        self.load_manual()
        self.refresh_queue_list() # Actualizar textos de la lista
        self.update_test_bench_status()

    def update_ui_texts(self):
        self.setWindowTitle(self.tr("app_title"))
        self.tabs.setTabText(0, self.tr("tab_trainer"))
        self.tabs.setTabText(1, self.tr("tab_testbench"))
        self.tabs.setTabText(2, self.tr("tab_manual"))
        
        self.lbl_md.setText(self.tr("md_found"))
        self.lbl_model.setText(self.tr("model_found"))
        self.btn_md.setText(self.tr("select_file"))
        self.btn_model.setText(self.tr("select_file"))
        self.btn_download.setText(self.tr("download_btn"))
        self.btn_process.setText(self.tr("process_btn"))
        self.btn_add_queue.setText(self.tr("add_queue_btn"))
        self.btn_remove_queue.setText(self.tr("remove_queue_btn"))
        self.btn_start_queue.setText(self.tr("start_queue_btn"))
        self.lbl_engine.setText(self.tr("engine_label"))

        self.group_input.setTitle(self.tr("input_section"))
        self.group_params.setTitle(self.tr("params_section"))
        self.group_queue.setTitle(self.tr("queue_section"))
        self.group_console.setTitle(self.tr("console_section"))

        self.group_tb_target.setTitle(self.tr("testbench_target_section"))
        self.group_tb_runtime.setTitle(self.tr("testbench_runtime_section"))
        self.group_tb_cases.setTitle(self.tr("testbench_cases_section"))
        self.group_tb_actions.setTitle(self.tr("testbench_actions_section"))
        self.group_tb_results.setTitle(self.tr("testbench_results_section"))

        self.lbl_tb_model.setText(self.tr("testbench_model_ref"))
        self.lbl_tb_rules.setText(self.tr("testbench_rules_path"))
        self.txt_tb_model.setPlaceholderText(self.tr("testbench_model_placeholder"))
        self.txt_tb_rules.setPlaceholderText(self.tr("testbench_rules_placeholder"))
        self.btn_tb_model_file.setText(self.tr("testbench_select_file"))
        self.btn_tb_rules.setText(self.tr("testbench_select_file"))
        self.check_tb_fallback.setText(self.tr("testbench_enable_fallback"))
        self.lbl_tb_prompt_mode.setText(self.tr("testbench_prompt_mode_label"))
        self._populate_test_bench_prompt_modes()

        self.check_tb_run_all_modes.setText(self.tr("testbench_run_all_modes"))
        self.lbl_tb_n_ctx.setText(self.tr("testbench_n_ctx"))
        self.lbl_tb_max_tokens.setText(self.tr("testbench_max_tokens"))
        self.lbl_tb_temperature.setText(self.tr("testbench_temperature"))
        self.lbl_tb_case_count.setText(self.tr("testbench_case_count"))
        self.check_tb_narrative_gate.setText(self.tr("testbench_enable_narrative_gate"))
        self.lbl_tb_narrative_min.setText(self.tr("testbench_min_narrative_score"))
        self.lbl_tb_narrative_hard_fail.setText(self.tr("testbench_max_narrative_hard_fail_rate"))
        self.lbl_tb_min_first.setText(self.tr("testbench_min_first_pass"))
        self.lbl_tb_min_final.setText(self.tr("testbench_min_final"))
        self.lbl_tb_max_fallback.setText(self.tr("testbench_max_fallback"))
        self.btn_tb_run.setText(self.tr("testbench_run_btn"))
        self.btn_tb_stop.setText(self.tr("testbench_stop_btn"))
        self.btn_tb_export.setText(self.tr("testbench_export_btn"))

        if not self.test_bench_report:
            self.lbl_tb_summary.setText(self.tr("testbench_summary_empty"))
        
        # Update engine combo items safely
        current_idx = self.combo_engine.currentIndex()
        self.combo_engine.clear()
        self.combo_engine.addItems([
            self.tr("engine_auto"),
            self.tr("engine_cuda"),
            self.tr("engine_vulkan")
        ])
        if current_idx >= 0:
            self.combo_engine.setCurrentIndex(current_idx)
        
        self.update_status()

    def log(self, message):
        self.console.append(message)
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

    def scan_input_folder(self):
        # Auto-detectar si hay archivos en input_model al inicio
        if not INPUT_DIR.exists():
            INPUT_DIR.mkdir(parents=True, exist_ok=True)
            
        md_files = list(INPUT_DIR.glob("*.md"))
        base_model_files = (
            list(INPUT_DIR.glob("*.safetensors"))
            + list(INPUT_DIR.glob("*.bin"))
            + list(INPUT_DIR.glob("*.pt"))
            + list(INPUT_DIR.glob("*.pth"))
            + list(INPUT_DIR.glob("*.ckpt"))
        )
        
        if md_files and not self.md_file:
            self.set_md_file(md_files[0])
            
        if base_model_files and not self.model_file:
            self.set_model_file(base_model_files[0])
        
        self.update_status()

    def select_md_file(self):
        fname, _ = QFileDialog.getOpenFileName(self, self.tr("select_file"), str(INPUT_DIR), "Markdown Files (*.md)")
        if fname:
            self.set_md_file(Path(fname))

    def select_model_file(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            self.tr("select_file"),
            str(INPUT_DIR)
        )
        if folder:
            self.set_model_file(Path(folder))

    def set_md_file(self, path):
        self.md_file = path
        self.txt_md.setText(str(path))
        self.update_status()

    def set_model_file(self, path):
        previous_model = str(self.model_file) if self.model_file else ""
        if previous_model == str(path):
            return

        self.model_file = path
        self.combo_model.blockSignals(True)
        self.combo_model.setCurrentText(str(path))
        self.combo_model.blockSignals(False)

        if hasattr(self, "txt_tb_model"):
            current_tb = self.txt_tb_model.text().strip()
            if not current_tb or current_tb == previous_model:
                self.txt_tb_model.setText(str(path))

        self.prompt_smart_configuration()
        self.update_status()

    def on_model_text_changed(self, text):
        previous_model = str(self.model_file) if self.model_file else ""
        if text.strip() == previous_model:
            return

        if text.strip():
            self.model_file = Path(text.strip())

            if hasattr(self, "txt_tb_model"):
                current_tb = self.txt_tb_model.text().strip()
                if not current_tb or current_tb == previous_model:
                    self.txt_tb_model.setText(str(self.model_file))
                    
            self.prompt_smart_configuration()
        else:
            self.model_file = None
        self.update_status()

    def start_download(self):
        repo_id = self.combo_model.currentText().strip()
        if not repo_id:
            QMessageBox.warning(self, self.tr("dlg_error_title"), self.tr("dlg_invalid_model_id"))
            return
            
        # Deshabilitar interfaz
        self.btn_download.setEnabled(False)
        self.btn_process.setEnabled(False)
        self.combo_model.setEnabled(False)
        self.log(f"{self.tr('downloading')} {repo_id}")
        
        self.download_thread = DownloadThread(repo_id, self.tr)
        self.download_thread.log_signal.connect(self.log)
        self.download_thread.finished_signal.connect(self.on_download_finished)
        self.download_thread.start()

    def on_download_finished(self, success, result_path):
        self.btn_download.setEnabled(True)
        self.combo_model.setEnabled(True)
        
        if success:
            self.log(f"{self.tr('download_success')} {result_path}")
            # Actualizar el modelo seleccionado a la ruta local
            self.set_model_file(Path(result_path))
            QMessageBox.information(self, self.tr("dlg_success_title"), self.tr("dlg_download_success_body").format(path=result_path))
        else:
            self.log(f"{self.tr('download_error')} {result_path}")
            QMessageBox.critical(self, self.tr("dlg_error_title"), self.tr("dlg_download_failed_body").format(path=result_path))
            
        self.update_status()

    def detect_model_size(self):
        if not self.model_file:
            return "unknown"
        
        name = str(self.model_file).lower()
        
        # Micro: < 4B (0.5b, 1.5b, 1b, 3b)
        if re.search(r'\b(0\.5b|1\.5b|1b|3b|mini)\b', name):
            return "micro"
        # Small: 4B - 8B (4b, 7b, 8b)
        if re.search(r'\b(4b|7b|8b)\b', name):
            return "small"
        # Medium: 9B - 14B (9b, 10b, 11b, 12b, 13b, 14b, nemo)
        elif re.search(r'\b(9b|10b|11b|12b|13b|14b|nemo)\b', name):
            return "medium"
        # Large: > 14B (27b, 30b, 32b, 34b, 70b)
        elif re.search(r'\b(27b|30b|32b|34b|70b)\b', name):
            return "large"
        
        return "unknown"

    def prompt_smart_configuration(self):
        if getattr(self, "_is_loading_config", False):
            return
            
        size_cat = self.detect_model_size()
        
        if size_cat != "unknown":
            reply = QMessageBox.question(
                self, 
                self.tr("dlg_smart_profile_title"),
                self.tr("dlg_smart_profile_body").format(size=size_cat.upper()),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self.apply_smart_configuration(size_cat)
        else:
            # Fallback
            items = [
                self.tr("dlg_smart_noapply"),
                "Micro (< 4B)",
                "Small (4B - 8B)",
                "Medium (9B - 14B)",
                "Large (> 14B)"
            ]
            item, ok = QInputDialog.getItem(
                self, 
                self.tr("dlg_smart_unknown_title"), 
                self.tr("dlg_smart_unknown_body"), 
                items, 0, False
            )
            if ok and item != items[0]:
                if "Micro" in item: size_cat = "micro"
                elif "Small" in item: size_cat = "small"
                elif "Medium" in item: size_cat = "medium"
                elif "Large" in item: size_cat = "large"
                
                if size_cat != "unknown":
                    self.apply_smart_configuration(size_cat)

    def apply_smart_configuration(self, size_cat):
        # Default / Unknown fallback variables
        rank, alpha, batch, lr, epochs = 32, 64, 2, 0.0002, 3
        
        if size_cat == "micro":
            # 1.5B-3B: Rank 16, Batch 8, LR 3e-4, Ep 5
            rank, alpha, batch, lr, epochs = 16, 32, 8, 0.0003, 5
        elif size_cat == "small":
            # 4B-8B: Rank 32, Batch 4, LR 2e-4, Ep 5
            rank, alpha, batch, lr, epochs = 32, 64, 4, 0.0002, 5
        elif size_cat == "medium":
            # 9B-14B: Rank 64, Batch 2, LR 1e-4, Ep 4
            rank, alpha, batch, lr, epochs = 64, 128, 2, 0.0001, 4
        elif size_cat == "large":
            # >14B: Rank 64, Batch 1, LR 5e-5, Ep 3
            rank, alpha, batch, lr, epochs = 64, 128, 1, 0.00005, 3
            
        self.spin_rank.setValue(rank)
        self.spin_alpha.setValue(alpha)
        self.spin_batch.setValue(batch)
        self.spin_lr.setValue(lr)
        self.spin_epochs.setValue(epochs)
        
        self.log(self.tr("log_smart_config_applied").format(size=size_cat.upper(), rank=rank, alpha=alpha, batch=batch, epochs=epochs, lr=lr))



    def update_status(self):
        # Determinar si el modelo existe localmente
        model_exists = False
        if self.model_file:
            if self.model_file.exists():
                model_exists = True
            # Si es una ruta relativa que podría estar en input_model
            elif (INPUT_DIR / self.model_file).exists():
                self.model_file = INPUT_DIR / self.model_file
                model_exists = True

        # Botón de descarga activo si hay texto pero no es archivo local
        if self.model_file and not model_exists and str(self.model_file).strip() != "":
            self.btn_download.setEnabled(True)
            self.btn_process.setEnabled((not self.is_queue_running) and bool(self.md_file))
            self.btn_add_queue.setEnabled(bool(self.md_file))
        elif model_exists:
            self.btn_download.setEnabled(False) # Ya lo tenemos
            self.btn_process.setEnabled((not self.is_queue_running) and bool(self.md_file))
            self.btn_add_queue.setEnabled(bool(self.md_file))
        else:
            self.btn_download.setEnabled(False)
            self.btn_process.setEnabled(False)
            self.btn_add_queue.setEnabled(False)

        self.update_test_bench_status()

    def _build_safe_model_name(self, model_path: Path) -> str:
        model_name = Path(str(model_path).rstrip("/\\")).name or "model"
        return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in model_name)

    def _find_latest_previous_run(self, model_path: Path):
        safe_name = self._build_safe_model_name(model_path)
        candidates = []
        for run_dir in OUTPUT_DIR.glob(f"run_{safe_name}_*"):
            if not run_dir.is_dir():
                continue
            adapters_dir = run_dir / "lora_adapters"
            if not adapters_dir.exists() or not adapters_dir.is_dir():
                continue

            has_adapter_config = (adapters_dir / "adapter_config.json").exists()
            has_adapter_weights = any(
                adapters_dir.glob("*.safetensors")
            ) or any(adapters_dir.glob("*.bin")) or any(adapters_dir.glob("*.pt"))

            if has_adapter_config and has_adapter_weights:
                candidates.append(run_dir)
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)


    def get_current_params(self):
        engine_idx = self.combo_engine.currentIndex()
        # 0: Auto, 1: CUDA, 2: Vulkan
        engine_map = {0: "auto", 1: "cuda", 2: "vulkan"}

        return {
            "rank": self.spin_rank.value(),
            "alpha": self.spin_alpha.value(),
            "batch_size": self.spin_batch.value(),
            "epochs": self.spin_epochs.value(),
            "learning_rate": self.spin_lr.value(),
            "engine": engine_map.get(engine_idx, "auto"),
            "lang": getattr(self, "current_lang", "en"),
        }

    # --- QUEUE SYSTEM ---
    def add_to_queue(self):
        if not self.md_file or not self.model_file:
            return

        # Validación: Comprobar si el modelo existe localmente
        model_exists = False
        if self.model_file.exists():
            model_exists = True
        elif (INPUT_DIR / self.model_file.name).exists():
            self.model_file = INPUT_DIR / self.model_file.name
            model_exists = True
        elif (INPUT_DIR / str(self.model_file).replace("/", "_").replace("\\", "_")).exists():
            self.model_file = INPUT_DIR / str(self.model_file).replace("/", "_").replace("\\", "_")
            model_exists = True

        if not model_exists:
            answer = QMessageBox.question(
                self,
                self.tr("dlg_model_not_downloaded_title"),
                self.tr("dlg_model_not_downloaded_body").format(model=self.model_file),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if answer == QMessageBox.Yes:
                self.start_download()
            return

        params = self.get_current_params()

        job = {
            "md": self.md_file,
            "model": self.model_file,
            "params": params,
            "status": "pending"
        }
        self.job_queue.append(job)
        self.refresh_queue_list()
        self.log(f"{self.tr('queue_added')} {self.md_file.name}")

    def remove_from_queue(self):
        row = self.list_queue.currentRow()
        if row >= 0:
            # Si intentamos borrar el job actual en ejecución, bloqueamos o avisamos (simple: permitir borrado de pendientes)
            if self.is_queue_running and row == self.current_job_index:
                return # No borrar lo que se está ejecutando
            
            self.job_queue.pop(row)
            # Ajustar índice si borramos uno anterior
            if self.is_queue_running and row < self.current_job_index:
                self.current_job_index -= 1
                
            self.refresh_queue_list()

    def refresh_queue_list(self):
        self.list_queue.clear()
        for i, job in enumerate(self.job_queue):
            status_key = f"status_{job['status']}" # status_pending, status_processing...
            status_text = self.tr(status_key)
            
            text = self.tr("queue_item_format").format(
                status=status_text,
                md=job["md"].name,
                model=job["model"].name,
                rank=job["params"]["rank"],
                alpha=job["params"]["alpha"],
                batch=job["params"]["batch_size"],
                epochs=job["params"]["epochs"]
            )
            item = QListWidgetItem(text)
            
            # Colores ajustados para legibilidad en tema oscuro
            # Usamos colores más oscuros de fondo y nos aseguramos que el texto sea blanco (por stylesheet)
            if job['status'] == 'processing':
                item.setBackground(Qt.darkCyan) # Cyan oscuro
            elif job['status'] == 'done':
                item.setBackground(Qt.darkGreen) # Verde oscuro
            elif job['status'] == 'error':
                item.setBackground(Qt.darkRed) # Rojo oscuro
            # Pending se queda con el fondo por defecto
                
            self.list_queue.addItem(item)

    def start_queue_processing(self):
        if not self.job_queue:
            self.log(self.tr("queue_empty"))
            return
            
        pending_jobs = [j for j in self.job_queue if j['status'] == 'pending']
        if not pending_jobs:
             self.log(self.tr("queue_finished"))
             return

        self.is_queue_running = True
        self.update_status() # Desactiva botones manuales
        self.btn_start_queue.setEnabled(False)
        self.btn_remove_queue.setEnabled(False)
        
        # Encontrar el primer pendiente
        for i, job in enumerate(self.job_queue):
            if job['status'] == 'pending':
                self.current_job_index = i
                break
        
        self.process_current_queue_job()

    def process_current_queue_job(self):
        if self.current_job_index >= len(self.job_queue):
            self.finish_queue()
            return

        job = self.job_queue[self.current_job_index]
        if job['status'] != 'pending': # Skip completed
            self.current_job_index += 1
            self.process_current_queue_job()
            return

        job['status'] = 'processing'
        self.refresh_queue_list()
        
        self.log(self.tr("log_queue_processing_item").format(current=self.current_job_index + 1, total=len(self.job_queue)))

        params = dict(job['params'])
        previous_run = self._find_latest_previous_run(job['model'])
        if previous_run is not None:
            answer = QMessageBox.question(
                self,
                self.tr("resume_previous_prompt_title"),
                self.tr("resume_previous_prompt_body").format(model=job['model'].name, run=str(previous_run)),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                params["resume_previous_run_dir"] = str(previous_run)
                self.log(f"{self.tr('resume_previous_started')} {previous_run}")
            else:
                self.log(self.tr("resume_previous_new_train"))
        
        self.thread = TrainerThread(job['md'], job['model'], params, self.tr)
        self.thread.log_signal.connect(self.log)
        self.thread.finished_signal.connect(self.on_queue_job_finished)
        self.thread.start()

    def on_queue_job_finished(self, success, message):
        self.log(message)
        job = self.job_queue[self.current_job_index]
        job['status'] = 'done' if success else 'error'
        self.refresh_queue_list()
        
        # Next job
        self.current_job_index += 1
        self.process_current_queue_job()

    def finish_queue(self):
        self.is_queue_running = False
        self.log(self.tr("queue_finished"))
        self.btn_start_queue.setEnabled(True)
        self.btn_remove_queue.setEnabled(True)
        self.update_status()

    # --- DIRECT PROCESSING ---
    def start_processing_direct(self):
        if not self.md_file:
            QMessageBox.critical(self, self.tr("dlg_error_title"), self.tr("error_no_md"))
            return
        if not self.model_file:
            QMessageBox.critical(self, self.tr("dlg_error_title"), self.tr("error_no_model"))
            return

        # Validación: Comprobar si el modelo existe localmente
        model_exists = False
        if self.model_file.exists():
            model_exists = True
        elif (INPUT_DIR / self.model_file.name).exists():
            self.model_file = INPUT_DIR / self.model_file.name
            model_exists = True
        elif (INPUT_DIR / str(self.model_file).replace("/", "_").replace("\\", "_")).exists():
            self.model_file = INPUT_DIR / str(self.model_file).replace("/", "_").replace("\\", "_")
            model_exists = True

        if not model_exists:
            answer = QMessageBox.question(
                self,
                self.tr("dlg_model_not_downloaded_title"),
                self.tr("dlg_model_not_downloaded_body").format(model=self.model_file),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if answer == QMessageBox.Yes:
                self.start_download()
            return

        params = self.get_current_params()

        previous_run = self._find_latest_previous_run(self.model_file)
        if previous_run is not None:
            answer = QMessageBox.question(
                self,
                self.tr("resume_previous_prompt_title"),
                self.tr("resume_previous_prompt_body").format(model=self.model_file.name, run=str(previous_run)),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                params["resume_previous_run_dir"] = str(previous_run)
                self.log(f"{self.tr('resume_previous_started')} {previous_run}")
            else:
                self.log(self.tr("resume_previous_new_train"))
        
        self.btn_process.setEnabled(False)
        self.log(self.tr("status_training"))
        
        self.thread = TrainerThread(self.md_file, self.model_file, params, self.tr)
        self.thread.log_signal.connect(self.log)
        self.thread.finished_signal.connect(self.on_direct_finished)
        self.thread.start()

    def on_direct_finished(self, success, message):
        self.log(message)
        self.btn_process.setEnabled(True)

    def closeEvent(self, event):
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            self.thread.wait(2000)

        if self.test_bench_thread and self.test_bench_thread.isRunning():
            self.test_bench_thread.stop()
            self.test_bench_thread.wait(2000)

        self.save_user_config()
        super().closeEvent(event)

if __name__ == "__main__":
    # Soporte para modo Worker (Entrenamiento en subproceso usando el mismo EXE)
    if len(sys.argv) > 1 and sys.argv[1] == "--train-worker":
        try:
            # Argumentos esperados: --train-worker model_path dataset_path output_dir params_json
            if len(sys.argv) < 6:
                print("Error: Faltan argumentos para el worker.")
                sys.exit(1)
            
            from core.run_unsloth_training import run_training_internal
            
            model_arg = sys.argv[2]
            dataset_arg = sys.argv[3]
            output_arg = sys.argv[4]
            params_arg = sys.argv[5]
            
            params = json.loads(params_arg.replace("'", '"'))
            
            # Ejecutar entrenamiento
            run_training_internal(model_arg, dataset_arg, output_arg, params)
            sys.exit(0)
        except Exception as e:
            print(f"FATAL WORKER ERROR: {e}")
            traceback.print_exc()
            sys.exit(1)

    try:
        app = QApplication(sys.argv)
        window = LoRATrainerGUI()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        # En modo windowed, los errores son invisibles sin esto
        crash_msg = f"FATAL GUI ERROR: {e}\n{traceback.format_exc()}"
        print(crash_msg)  # Va al LogStream (archivo) en modo windowed
        # Intentar también escribir a crash_log.txt directamente
        try:
            if getattr(sys, 'frozen', False):
                _crash_path = os.path.join(os.path.dirname(sys.executable), 'crash_log.txt')
            else:
                _crash_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'crash_log.txt')
            with open(_crash_path, 'a', encoding='utf-8') as f:
                f.write(crash_msg)
        except Exception:
            pass
        sys.exit(1)
