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
    # CRTICO: unsloth/import_fixes.py captura sys.__stdout__ como _original_stream
    # y llama .flush() sobre él. Si es un handle inválido de Windows → OSError.
    sys.__stdout__ = sys.stdout
    sys.__stderr__ = sys.stderr
# -----------------------------------------------------------

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QPushButton, QComboBox, 
                               QSpinBox, QDoubleSpinBox, QGroupBox, QTextEdit, 
                               QMessageBox, QTabWidget, QFileDialog, QLineEdit,
                               QListWidget, QListWidgetItem, QCheckBox, QInputDialog, QDialog, QTableWidget, QTableWidgetItem, QHeaderView,
                               QTreeWidget, QTreeWidgetItem, QSplitter)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
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
            self._result_success = True
            self._result_payload = report
        except Exception as e:
            self.log_signal.emit(self.tr("log_testbench_error").format(e=str(e)))
            self._result_success = False
            self._result_payload = {"error": str(e)}

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
        self.test_bench_autotune_active = False
        self._current_sequence_item = None
        self.tb_model_queue = []
        self.is_tb_queue_running = False
        self.current_tb_queue_index = -1
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
            with open(i18n_path, 'r', encoding='utf-8-sig') as f:
                self.i18n = json.load(f)
        else:
             print(f"Warning: Traducción no encontrada en {i18n_path}")

    def tr(self, key, default=None):
        return self.i18n.get(key, default if default is not None else key)

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

            if hasattr(self, "combo_thinking_mode"):
                saved_thinking = str(cfg.get("thinking_mode", "suppress")).strip().lower()
                idx = self.combo_thinking_mode.findData(saved_thinking)
                if idx >= 0:
                    self.combo_thinking_mode.setCurrentIndex(idx)

            if hasattr(self, "combo_narrative_style"):
                saved_narrative = str(cfg.get("narrative_style", "functional")).strip().lower()
                idx = self.combo_narrative_style.findData(saved_narrative)
                if idx >= 0:
                    self.combo_narrative_style.setCurrentIndex(idx)

            if hasattr(self, "combo_train_on_responses"):
                saved_toro = str(cfg.get("train_on_responses", "on")).strip().lower()
                idx = self.combo_train_on_responses.findData(saved_toro)
                if idx >= 0:
                    self.combo_train_on_responses.setCurrentIndex(idx)

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
            if hasattr(self, "combo_tb_language"):
                lang = cfg.get("testbench_target_language", "Español")
                idx = self.combo_tb_language.findText(lang)
                if idx >= 0:
                    self.combo_tb_language.setCurrentIndex(idx)
            if hasattr(self, "combo_tb_reasoning_a"):
                self.combo_tb_reasoning_a.setCurrentText(cfg.get("testbench_reasoning_a", "None"))
            if hasattr(self, "combo_tb_reasoning_b"):
                self.combo_tb_reasoning_b.setCurrentText(cfg.get("testbench_reasoning_b", "Low"))
            if hasattr(self, "combo_tb_reasoning_c"):
                self.combo_tb_reasoning_c.setCurrentText(cfg.get("testbench_reasoning_c", "Low"))
            if hasattr(self, "check_tb_run_all_modes"):
                self.check_tb_run_all_modes.setChecked(bool(cfg.get("testbench_run_all_modes", False)))
            if hasattr(self, "check_tb_autotune"):
                self.check_tb_autotune.setChecked(bool(cfg.get("testbench_enable_autotune", False)))
            if hasattr(self, "spin_tb_autotune_cycles"):
                self.spin_tb_autotune_cycles.setValue(int(cfg.get("testbench_autotune_cycles", 3)))

            selected_cases = cfg.get("testbench_selected_cases", None)
            if hasattr(self, "list_tb_cases") and isinstance(selected_cases, list):
                self._set_selected_test_case_ids(selected_cases)

            geom = cfg.get("window_geometry")
            if geom and isinstance(geom, dict):
                self.setGeometry(geom.get("x", 100), geom.get("y", 100),
                                 geom.get("w", 1000), geom.get("h", 750))

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
                "thinking_mode": self.combo_thinking_mode.currentData() if hasattr(self, "combo_thinking_mode") else "suppress",
                "narrative_style": self.combo_narrative_style.currentData() if hasattr(self, "combo_narrative_style") else "literary",
                "train_on_responses": self.combo_train_on_responses.currentData() if hasattr(self, "combo_train_on_responses") else "on",
                "md_file": str(self.md_file) if self.md_file else "",
                "model_file": str(self.model_file) if self.model_file else "",
                "testbench_model_ref": self.txt_tb_model.text().strip() if hasattr(self, "txt_tb_model") else "",
                "testbench_rules_path": self.txt_tb_rules.text().strip() if hasattr(self, "txt_tb_rules") else "",
                "testbench_enable_fallback": self.check_tb_fallback.isChecked() if hasattr(self, "check_tb_fallback") else True,
                "testbench_min_first": self.spin_tb_first_pass.value() if hasattr(self, "spin_tb_first_pass") else 85.0,
                "testbench_min_final": self.spin_tb_final.value() if hasattr(self, "spin_tb_final") else 95.0,
                "testbench_max_fallback": self.spin_tb_fallback.value() if hasattr(self, "spin_tb_fallback") else 20.0,
                "testbench_n_ctx": self.spin_tb_n_ctx.value() if hasattr(self, "spin_tb_n_ctx") else 8192,
                "testbench_max_tokens": self.spin_tb_max_tokens.value() if hasattr(self, "spin_tb_max_tokens") else 2048,
                "testbench_temperature": self.spin_tb_temperature.value() if hasattr(self, "spin_tb_temperature") else 0.2,
                "testbench_case_count": self.spin_tb_case_count.value() if hasattr(self, "spin_tb_case_count") else max(1, len(self.test_bench_cases)),
                "testbench_enable_narrative_gate": self.check_tb_narrative_gate.isChecked() if hasattr(self, "check_tb_narrative_gate") else True,
                "testbench_min_narrative_score": self.spin_tb_narrative_min.value() if hasattr(self, "spin_tb_narrative_min") else 70.0,
                "testbench_max_narrative_hard_fail_rate": self.spin_tb_narrative_hard_fail.value() if hasattr(self, "spin_tb_narrative_hard_fail") else 5.0,
                "testbench_prompt_mode": self._get_test_bench_prompt_mode() if hasattr(self, "combo_tb_prompt_mode") else "isolated",
                "testbench_target_language": self.combo_tb_language.currentText() if hasattr(self, "combo_tb_language") else "Español",
                "testbench_reasoning_a": self.combo_tb_reasoning_a.currentText() if hasattr(self, "combo_tb_reasoning_a") else "None",
                "testbench_reasoning_b": self.combo_tb_reasoning_b.currentText() if hasattr(self, "combo_tb_reasoning_b") else "Low",
                "testbench_reasoning_c": self.combo_tb_reasoning_c.currentText() if hasattr(self, "combo_tb_reasoning_c") else "Low",
                "testbench_run_all_modes": self.check_tb_run_all_modes.isChecked() if hasattr(self, "check_tb_run_all_modes") else False,
                "testbench_enable_autotune": self.check_tb_autotune.isChecked() if hasattr(self, "check_tb_autotune") else False,
                "testbench_autotune_cycles": self.spin_tb_autotune_cycles.value() if hasattr(self, "spin_tb_autotune_cycles") else 3,
                "testbench_selected_cases": self._get_selected_test_case_ids() if hasattr(self, "list_tb_cases") else [],
                "window_geometry": {
                    "x": self.geometry().x(), "y": self.geometry().y(),
                    "w": self.geometry().width(), "h": self.geometry().height(),
                },
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
            "Qwen/Qwen3.5-2B",         # Modelo base
            
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
        self.spin_rank.setValue(16)
        row1.addWidget(self.spin_rank)
        row1.addWidget(QLabel(self.tr("lora_alpha")))
        self.spin_alpha = QSpinBox()
        self.spin_alpha.setRange(8, 512)
        self.spin_alpha.setValue(32)
        row1.addWidget(self.spin_alpha)
        params_layout.addLayout(row1)
        
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(self.tr("batch_size")))
        self.spin_batch = QSpinBox()
        self.spin_batch.setRange(1, 64)
        self.spin_batch.setValue(4)
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

        row_thinking = QHBoxLayout()
        row_thinking.addWidget(QLabel(self.tr("thinking_mode_label")))
        self.combo_thinking_mode = QComboBox()
        self.combo_thinking_mode.addItem(self.tr("thinking_mode_suppress"), "suppress")
        self.combo_thinking_mode.addItem(self.tr("thinking_mode_native"), "native")
        self.combo_thinking_mode.setToolTip(self.tr("thinking_mode_tooltip"))
        row_thinking.addWidget(self.combo_thinking_mode)
        row_thinking.addStretch()
        params_layout.addLayout(row_thinking)

        row_narrative = QHBoxLayout()
        row_narrative.addWidget(QLabel(self.tr("narrative_style_label")))
        self.combo_narrative_style = QComboBox()
        self.combo_narrative_style.addItem(self.tr("narrative_style_literary"), "literary")
        self.combo_narrative_style.addItem(self.tr("narrative_style_functional"), "functional")
        self.combo_narrative_style.setToolTip(self.tr("narrative_style_tooltip"))
        row_narrative.addWidget(self.combo_narrative_style)
        row_narrative.addStretch()
        params_layout.addLayout(row_narrative)

        row_toro = QHBoxLayout()
        row_toro.addWidget(QLabel(self.tr("train_on_responses_label")))
        self.combo_train_on_responses = QComboBox()
        self.combo_train_on_responses.addItem(self.tr("train_on_responses_on"), "on")
        self.combo_train_on_responses.addItem(self.tr("train_on_responses_off"), "off")
        self.combo_train_on_responses.setToolTip(self.tr("train_on_responses_tooltip"))
        row_toro.addWidget(self.combo_train_on_responses)
        row_toro.addStretch()
        params_layout.addLayout(row_toro)

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

        # Generate Dataset Button
        self.btn_generate_dataset = QPushButton(self.tr("generate_dataset_btn"))
        self.btn_generate_dataset.setMinimumHeight(35)
        self.btn_generate_dataset.setStyleSheet("font-weight: bold; background-color: #6f42c1; color: white; border-radius: 5px; padding: 5px;")
        self.btn_generate_dataset.clicked.connect(self.generate_dataset)
        layout.addWidget(self.btn_generate_dataset)
        
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
        self.spin_tb_max_tokens.setRange(64, 8192)
        self.spin_tb_max_tokens.setValue(2048)
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

        row_reasoning = QHBoxLayout()
        self.lbl_tb_reasoning_a = QLabel(self.tr("testbench_reasoning_a"))
        row_reasoning.addWidget(self.lbl_tb_reasoning_a)
        self.combo_tb_reasoning_a = QComboBox()
        self.combo_tb_reasoning_a.addItems(["None", "Low", "Medium", "High"])
        self.combo_tb_reasoning_a.setCurrentText("None")
        row_reasoning.addWidget(self.combo_tb_reasoning_a)

        self.lbl_tb_reasoning_b = QLabel(self.tr("testbench_reasoning_b"))
        row_reasoning.addWidget(self.lbl_tb_reasoning_b)
        self.combo_tb_reasoning_b = QComboBox()
        self.combo_tb_reasoning_b.addItems(["None", "Low", "Medium", "High"])
        self.combo_tb_reasoning_b.setCurrentText("Low")
        row_reasoning.addWidget(self.combo_tb_reasoning_b)

        self.lbl_tb_reasoning_c = QLabel(self.tr("testbench_reasoning_c"))
        row_reasoning.addWidget(self.lbl_tb_reasoning_c)
        self.combo_tb_reasoning_c = QComboBox()
        self.combo_tb_reasoning_c.addItems(["None", "Low", "Medium", "High"])
        self.combo_tb_reasoning_c.setCurrentText("Low")
        row_reasoning.addWidget(self.combo_tb_reasoning_c)

        runtime_layout.addLayout(row_reasoning)

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

        self.check_tb_autotune = QCheckBox(self.tr("testbench_enable_autotune"))
        self.check_tb_autotune.setChecked(False)
        row_rt_2.addWidget(self.check_tb_autotune)

        self.lbl_tb_autotune_cycles = QLabel(self.tr("testbench_autotune_cycles"))
        row_rt_2.addWidget(self.lbl_tb_autotune_cycles)
        self.spin_tb_autotune_cycles = QSpinBox()
        self.spin_tb_autotune_cycles.setRange(1, 10)
        self.spin_tb_autotune_cycles.setValue(3)
        self.spin_tb_autotune_cycles.setMinimumWidth(50)
        row_rt_2.addWidget(self.spin_tb_autotune_cycles)

        self.lbl_tb_language = QLabel("Idioma de los Prompts")
        row_rt_2.addWidget(self.lbl_tb_language)
        self.combo_tb_language = QComboBox()
        self.combo_tb_language.addItems([
            "Espaol", "Ingls", "Francs", "Alemn",
            "Italiano", "Portugus", "Ruso",
            "Japons", "Coreano", "Chino"
        ])
        row_rt_2.addWidget(self.combo_tb_language)

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
        self.btn_tb_history = QPushButton(self.tr("testbench_history_btn"))
        self.btn_tb_history.clicked.connect(self.show_model_history)
        actions_layout.addWidget(self.btn_tb_run)
        actions_layout.addWidget(self.btn_tb_stop)
        actions_layout.addWidget(self.btn_tb_export)
        actions_layout.addWidget(self.btn_tb_history)
        actions_layout.addStretch()
        self.group_tb_actions.setLayout(actions_layout)
        layout.addWidget(self.group_tb_actions)

        self.group_tb_queue = QGroupBox(self.tr("tb_queue_section"))
        tb_queue_layout = QVBoxLayout()
        tb_queue_buttons = QHBoxLayout()
        self.btn_tb_queue_add = QPushButton(self.tr("tb_queue_add_btn"))
        self.btn_tb_queue_add.clicked.connect(self.tb_queue_add_models)
        self.btn_tb_queue_remove = QPushButton(self.tr("tb_queue_remove_btn"))
        self.btn_tb_queue_remove.clicked.connect(self.tb_queue_remove_selected)
        self.btn_tb_queue_start = QPushButton(self.tr("tb_queue_start_btn"))
        self.btn_tb_queue_start.setStyleSheet("font-weight: bold; background-color: #007bff; color: white; border-radius: 4px; padding: 4px 12px;")
        self.btn_tb_queue_start.clicked.connect(self.tb_queue_start)
        tb_queue_buttons.addWidget(self.btn_tb_queue_add)
        tb_queue_buttons.addWidget(self.btn_tb_queue_remove)
        tb_queue_buttons.addWidget(self.btn_tb_queue_start)
        tb_queue_buttons.addStretch()
        tb_queue_layout.addLayout(tb_queue_buttons)
        self.list_tb_queue = QListWidget()
        self.list_tb_queue.setMaximumHeight(100)
        self.list_tb_queue.setStyleSheet("""
            QListWidget::item { color: white; padding: 3px; }
            QListWidget::item:selected { background-color: #555; }
        """)
        tb_queue_layout.addWidget(self.list_tb_queue)
        self.group_tb_queue.setLayout(tb_queue_layout)
        layout.addWidget(self.group_tb_queue)

        summary_layout = QHBoxLayout()
        self.lbl_tb_summary = QLabel(self.tr("testbench_summary_empty"))
        summary_layout.addWidget(self.lbl_tb_summary)
        self.btn_tb_comparative = QPushButton("Ver Comparativa A/B/C")
        self.btn_tb_comparative.hide()
        summary_layout.addWidget(self.btn_tb_comparative)
        summary_layout.addStretch()
        layout.addLayout(summary_layout)

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

    def _attach_testbench_meta(self, report: dict) -> dict:
        if not isinstance(report, dict):
            return report
        cfg = dict(getattr(self, "_last_test_bench_config", {}) or {})
        if cfg:
            report.setdefault("model_ref", cfg.get("model_ref", ""))
            report.setdefault("testbench_config", cfg)
        report.setdefault("exported_at", datetime.now().isoformat(timespec="seconds"))
        return report

    def show_model_history(self):
        reports_dir = OUTPUT_DIR / "test_bench_reports"
        if not reports_dir.exists():
            QMessageBox.information(
                self,
                self.tr("testbench_history_msg_title"),
                self.tr("testbench_history_no_reports"),
            )
            return

        all_entries = []
        for file in reports_dir.glob("test_bench_report_*.json"):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                runs = data.get("runs")
                if not isinstance(runs, list) or not runs:
                    continue

                timestamp = data.get("exported_at", data.get("timestamp", file.stem.split("report_")[-1]))
                model_ref = str(
                    data.get("model_ref")
                    or (data.get("testbench_config") or {}).get("model_ref")
                    or ""
                ).strip()
                
                if model_ref:
                    p_model = Path(model_ref)
                    model_label = p_model.name
                    
                    if hasattr(p_model, "parent") and p_model.parent.name and p_model.parent.name != ".":
                        model_label = f"{p_model.parent.name}/{p_model.name}"
                else:
                    model_label = self.tr("testbench_history_unknown_model")

                winners = data.get("winners") if isinstance(data.get("winners"), dict) else {}
                final_rates = []
                thinking_label = ""

                if data.get("autotune") and winners:
                    for mode in ["isolated", "compact", "full"]:
                        winner_level = winners.get(mode)
                        matched = [
                            r
                            for r in runs
                            if r.get("prompt_mode") == mode
                            and r.get("_autotune_reasoning_level") == winner_level
                        ]
                        if matched:
                            avg_rate = sum(float(r.get("final_rate", 0) or 0) for r in matched) / len(matched)
                            final_rates.append(avg_rate)
                    thinking_label = "A={a} B={b} C={c}".format(
                        a=winners.get("isolated", "?"),
                        b=winners.get("compact", "?"),
                        c=winners.get("full", "?"),
                    )
                elif bool(data.get("multi_mode")) and isinstance(data.get("summary_by_mode"), list):
                    for row in data.get("summary_by_mode") or []:
                        if isinstance(row, dict):
                            final_rates.append(float(row.get("final_rate", 0) or 0))
                    thinking_label = self.tr("testbench_history_thinking_fixed")
                else:
                    final_rates.append(float(data.get("final_rate", 0) or 0))
                    cfg = data.get("testbench_config") or {}
                    prompt_mode = str(cfg.get("prompt_mode", ""))
                    reasoning = str(cfg.get("testbench_reasoning_level", ""))
                    thinking_label = f"{prompt_mode}:{reasoning}" if prompt_mode and reasoning else self.tr("testbench_history_thinking_fixed")

                if final_rates:
                    avg_global = sum(final_rates) / len(final_rates)
                    all_entries.append({
                        "model_label": model_label,
                        "score": avg_global,
                        "date": timestamp,
                        "file": file.name,
                        "file_path": file,
                        "model_ref": model_ref,
                        "thinking": thinking_label,
                        "full_data": data,
                    })
            except Exception as e:
                print(f"Error leyendo {file.name}: {e}")

        if not all_entries:
            QMessageBox.information(
                self,
                self.tr("testbench_history_msg_title"),
                self.tr("testbench_history_no_valid"),
            )
            return

        all_entries.sort(key=lambda x: x["date"], reverse=True)
        best_score = max(e["score"] for e in all_entries)

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("testbench_history_title"))
        dialog.resize(750, 450)
        
        layout = QVBoxLayout()
        table = QTableWidget(len(all_entries), 7)
        table.setHorizontalHeaderLabels([
            self.tr("testbench_history_col_model"),
            self.tr("testbench_history_col_success"),
            self.tr("testbench_history_col_thinking"),
            self.tr("testbench_history_col_training"),
            self.tr("testbench_history_col_date"),
            self.tr("testbench_history_col_details"),
            "",
        ])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)

        def _resolve_model_path(model_ref: str) -> Path:
            raw = str(model_ref or "").strip()
            if not raw:
                return Path("")
            p = Path(raw)
            if p.is_absolute():
                return p
            return TRAINER_ROOT / p

        def _load_training_meta(model_ref: str) -> dict:
            model_path = _resolve_model_path(model_ref)
            if not str(model_path) or not model_path.exists():
                return {}
            meta_path = Path(str(model_path) + ".training.json")
            if not meta_path.exists():
                return {}
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                return meta if isinstance(meta, dict) else {}
            except Exception:
                return {}

        def _format_training_meta(meta: dict) -> str:
            if not meta:
                return self.tr("testbench_history_training_na")
            parts = []
            
            base_model = meta.get("base_model", "")
            if base_model:
                base_name = Path(base_model).name if ("/" in base_model or "\\" in base_model) else base_model
                parts.append(f"base={base_name}")
                
            parts.append(f"r={meta.get('rank', '?')}")
            parts.append(f"a={meta.get('alpha', '?')}")
            parts.append(f"bs={meta.get('requested_batch_size', meta.get('batch_size', '?'))}")
            parts.append(f"ga={meta.get('grad_accumulation_steps', '?')}")
            parts.append(f"eff={meta.get('effective_batch_size', '?')}")
            parts.append(f"lr={meta.get('learning_rate', '?')}")
            parts.append(f"ep={meta.get('epochs', '?')}")
            parts.append(f"seq={meta.get('max_seq_length', '?')}")
            if "fla_available" in meta:
                parts.append(f"FLA={1 if bool(meta.get('fla_available')) else 0}")
            return " | ".join(str(p) for p in parts if str(p).strip())

        def _delete_entry(row_index: int, file_path: Path):
            confirm = QMessageBox.question(
                dialog,
                self.tr("testbench_history_delete_title"),
                self.tr("testbench_history_delete_confirm").format(file=file_path.name),
            )
            if confirm != QMessageBox.Yes:
                return
            try:
                file_path.unlink(missing_ok=True)
            except Exception as ex:
                QMessageBox.warning(dialog, self.tr("dlg_error_title"), str(ex))
                return
            dialog.accept()
            self.show_model_history()

        for row_idx, entry in enumerate(all_entries):
            item_model = QTableWidgetItem(str(entry["model_label"]))
            item_score = QTableWidgetItem(f"{entry['score']:.2f}%")
            item_thinking = QTableWidgetItem(str(entry.get("thinking", "")))

            training_meta = _load_training_meta(entry.get("model_ref", ""))
            item_training = QTableWidgetItem(_format_training_meta(training_meta))
            item_date = QTableWidgetItem(f"{entry['date']}\n{entry['file']}")
            
            for item in (item_model, item_score, item_thinking, item_training, item_date):
                item.setTextAlignment(Qt.AlignCenter)
                item.setFlags(item.flags() ^ Qt.ItemIsEditable)
            
            is_best = abs(entry["score"] - best_score) < 0.001
            if is_best:
                item_model.setForeground(Qt.green)
                item_score.setForeground(Qt.green)
                font = item_model.font()
                font.setBold(True)
                item_model.setFont(font)
                item_score.setFont(font)

            table.setItem(row_idx, 0, item_model)
            table.setItem(row_idx, 1, item_score)
            table.setItem(row_idx, 2, item_thinking)
            table.setItem(row_idx, 3, item_training)
            table.setItem(row_idx, 4, item_date)

            btn_details = QPushButton(self.tr("testbench_history_btn_details"))
            data_to_pass = entry.get("full_data", {})
            model_to_pass = entry["model_label"]
            btn_details.clicked.connect(lambda checked, d=data_to_pass, m=model_to_pass: self._show_report_details_dialog(d, m))
            table.setCellWidget(row_idx, 5, btn_details)

            btn_delete = QPushButton(self.tr("testbench_history_btn_delete"))
            btn_delete.setStyleSheet("color: #ff4444;")
            fp = entry["file_path"]
            btn_delete.clicked.connect(lambda checked, r=row_idx, f=fp: _delete_entry(r, f))
            table.setCellWidget(row_idx, 6, btn_delete)
            
        layout.addWidget(table)
        
        btn_close = QPushButton(self.tr("testbench_history_close_btn"))
        btn_close.clicked.connect(dialog.accept)
        layout.addWidget(btn_close)
        
        dialog.setLayout(layout)
        dialog.exec()

    def _show_report_details_dialog(self, data: dict, model_name: str):
        mode_labels = {"isolated": "A) Isolated", "compact": "B) Compact", "full": "C) Full"}
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("testbench_details_title").format(model=model_name))
        dialog.resize(1000, 620)
        layout = QVBoxLayout()

        # --- Preprocesar datos agrupados ---
        data_by_key = {}
        runs = data.get("runs", [])
        if not runs and "cases" in data:
            runs = [data]

        for r in runs:
            mode = str(r.get("prompt_mode", "unknown"))
            reasoning = str(r.get("_autotune_reasoning_level", ""))
            keys_to_update = ["_global_"]
            if mode:
                keys_to_update.append(f"_mode_{mode}")
            if mode and reasoning:
                keys_to_update.append(f"{mode}|{reasoning}")

            for c in r.get("cases", []):
                suite_id = str(c.get("case_id", "unknown"))
                passed = bool(c.get("passed_final", False))
                for key in keys_to_update:
                    if key not in data_by_key:
                        data_by_key[key] = {"suites": {}, "rules": {}}
                    bucket = data_by_key[key]
                    if suite_id not in bucket["suites"]:
                        bucket["suites"][suite_id] = {"runs": 0, "pass": 0, "fail": 0}
                    bucket["suites"][suite_id]["runs"] += 1
                    if passed:
                        bucket["suites"][suite_id]["pass"] += 1
                    else:
                        bucket["suites"][suite_id]["fail"] += 1
                    if c.get("final_checks"):
                        for ci in c["final_checks"]:
                            if not ci.get("ok", True):
                                cn = ci.get("check", "unknown")
                                bucket["rules"][cn] = bucket["rules"].get(cn, 0) + 1

        def _pass_rate_for_key(key):
            b = data_by_key.get(key)
            if not b or not b["suites"]:
                return 0.0
            total_r = sum(s["runs"] for s in b["suites"].values())
            total_p = sum(s["pass"] for s in b["suites"].values())
            return (total_p / total_r * 100.0) if total_r > 0 else 0.0

        # --- Panel izquierdo: Árbol de navegación ---
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setMinimumWidth(230)
        tree.setMaximumWidth(300)

        global_rate = _pass_rate_for_key("_global_")
        root_item = QTreeWidgetItem(tree, [self.tr("testbench_details_filter_global") + f"  ({global_rate:.1f}%)"])
        root_item.setData(0, Qt.UserRole, "_global_")
        font_bold = root_item.font(0)
        font_bold.setBold(True)
        root_item.setFont(0, font_bold)

        winners = data.get("winners", {}) if isinstance(data.get("winners"), dict) else {}
        mode_order = ["isolated", "compact", "full"]
        modes_present = sorted(set(
            str(r.get("prompt_mode", "")) for r in runs if r.get("prompt_mode")
        ), key=lambda x: mode_order.index(x) if x in mode_order else 99)

        for mode in modes_present:
            mode_key = f"_mode_{mode}"
            mode_rate = _pass_rate_for_key(mode_key)
            mode_label = mode_labels.get(mode, mode.capitalize())
            mode_node = QTreeWidgetItem(root_item, [f"{mode_label}  ({mode_rate:.1f}%)"])
            mode_node.setData(0, Qt.UserRole, mode_key)
            mode_node.setFont(0, font_bold)

            levels = sorted(set(
                str(r.get("_autotune_reasoning_level", ""))
                for r in runs
                if r.get("prompt_mode") == mode and r.get("_autotune_reasoning_level")
            ), key=lambda x: ["None", "Low", "Medium", "High"].index(x) if x in ["None", "Low", "Medium", "High"] else 99)

            for level in levels:
                leaf_key = f"{mode}|{level}"
                leaf_rate = _pass_rate_for_key(leaf_key)
                winner_marker = ""
                if winners.get(mode) == level:
                    winner_marker = " \u2605"
                leaf_label = f"{level}  ({leaf_rate:.1f}%){winner_marker}"
                leaf_item = QTreeWidgetItem(mode_node, [leaf_label])
                leaf_item.setData(0, Qt.UserRole, leaf_key)
                if leaf_rate >= 90.0:
                    leaf_item.setForeground(0, Qt.green)
                elif leaf_rate < 70.0:
                    leaf_item.setForeground(0, Qt.red)

        tree.expandAll()

        # --- Panel derecho: Tablas de detalle ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        lbl_suites = QLabel(self.tr("testbench_details_lbl_suites"))
        font_section = lbl_suites.font()
        font_section.setBold(True)
        lbl_suites.setFont(font_section)

        table_suites = QTableWidget(0, 5)
        table_suites.setHorizontalHeaderLabels([
            self.tr("testbench_details_col_suite"),
            self.tr("testbench_details_col_runs"),
            self.tr("testbench_details_col_pass"),
            self.tr("testbench_details_col_fail"),
            self.tr("testbench_details_col_rate")
        ])
        table_suites.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 5):
            table_suites.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)

        lbl_rules = QLabel(self.tr("testbench_details_lbl_rules"))
        lbl_rules.setFont(font_section)

        table_rules = QTableWidget(0, 2)
        table_rules.setHorizontalHeaderLabels([
            self.tr("testbench_details_col_rule"),
            self.tr("testbench_details_col_fail_count")
        ])
        table_rules.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

        right_layout.addWidget(lbl_suites)
        right_layout.addWidget(table_suites, 3)
        right_layout.addWidget(lbl_rules)
        right_layout.addWidget(table_rules, 2)

        # --- Splitter ---
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(tree)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        def _on_tree_selection():
            items = tree.selectedItems()
            if not items:
                return
            key = items[0].data(0, Qt.UserRole)
            bucket = data_by_key.get(key, {"suites": {}, "rules": {}})

            suites = bucket["suites"]
            table_suites.setRowCount(0)
            if suites:
                sorted_suites = sorted(suites.items(), key=lambda x: x[1]["fail"], reverse=True)
                table_suites.setRowCount(len(sorted_suites))
                for i, (suite_name, m) in enumerate(sorted_suites):
                    r_c, p_c, f_c = m["runs"], m["pass"], m["fail"]
                    rate = (p_c / r_c * 100.0) if r_c > 0 else 0.0
                    items_row = [
                        QTableWidgetItem(suite_name),
                        QTableWidgetItem(str(r_c)),
                        QTableWidgetItem(str(p_c)),
                        QTableWidgetItem(str(f_c)),
                        QTableWidgetItem(f"{rate:.1f}%"),
                    ]
                    for it in items_row:
                        it.setFlags(it.flags() ^ Qt.ItemIsEditable)
                    for it in items_row[1:]:
                        it.setTextAlignment(Qt.AlignCenter)
                    if f_c > 0:
                        items_row[3].setForeground(Qt.red)
                    else:
                        items_row[2].setForeground(Qt.green)
                    if rate >= 90.0:
                        items_row[4].setForeground(Qt.green)
                    elif rate < 70.0:
                        items_row[4].setForeground(Qt.red)
                    for col, it in enumerate(items_row):
                        table_suites.setItem(i, col, it)

            rules = bucket["rules"]
            table_rules.setRowCount(0)
            if rules:
                sorted_rules = sorted(rules.items(), key=lambda x: x[1], reverse=True)
                table_rules.setRowCount(len(sorted_rules))
                for i, (rule_name, count) in enumerate(sorted_rules):
                    it_rule = QTableWidgetItem(rule_name)
                    it_count = QTableWidgetItem(str(count))
                    it_rule.setFlags(it_rule.flags() ^ Qt.ItemIsEditable)
                    it_count.setFlags(it_count.flags() ^ Qt.ItemIsEditable)
                    it_count.setTextAlignment(Qt.AlignCenter)
                    it_count.setForeground(Qt.red)
                    table_rules.setItem(i, 0, it_rule)
                    table_rules.setItem(i, 1, it_count)

        tree.itemSelectionChanged.connect(_on_tree_selection)
        root_item.setSelected(True)
        _on_tree_selection()

        btn_close = QPushButton(self.tr("testbench_history_close_btn"))
        btn_close.clicked.connect(dialog.accept)
        layout.addWidget(btn_close)

        dialog.setLayout(layout)
        dialog.exec()

    def _show_comparative_dialog(self, mode_rows):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("testbench_comparative_title"))
        dialog.resize(600, 300)
        
        layout = QVBoxLayout()
        table = QTableWidget(len(mode_rows), 5)
        table.setHorizontalHeaderLabels([
            self.tr("testbench_comparative_col_mode"),
            self.tr("testbench_comparative_col_verdict"),
            self.tr("testbench_comparative_col_first"),
            self.tr("testbench_comparative_col_final"),
            self.tr("testbench_comparative_col_fallback")
        ])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        
        for row_idx, row_data in enumerate(mode_rows):
            item_mode = QTableWidgetItem(row_data["label"])
            item_verdict = QTableWidgetItem(self.tr("testbench_comparative_pass") if row_data["verdict"] else self.tr("testbench_comparative_fail"))
            item_first = QTableWidgetItem(f"{row_data['first_pass_rate']:.2f}%")
            item_final = QTableWidgetItem(f"{row_data['final_rate']:.2f}%")
            item_fallback = QTableWidgetItem(f"{row_data['fallback_rate']:.2f}%")
            
            # Centrar texto
            for item in (item_mode, item_verdict, item_first, item_final, item_fallback):
                item.setTextAlignment(Qt.AlignCenter)
            
            # Colorear veredicto
            if row_data["verdict"]:
                item_verdict.setForeground(Qt.green)
            else:
                item_verdict.setForeground(Qt.red)
                
            table.setItem(row_idx, 0, item_mode)
            table.setItem(row_idx, 1, item_verdict)
            table.setItem(row_idx, 2, item_first)
            table.setItem(row_idx, 3, item_final)
            table.setItem(row_idx, 4, item_fallback)
            
        layout.addWidget(table)
        
        btn_close = QPushButton(self.tr("testbench_history_close_btn"))
        btn_close.clicked.connect(dialog.accept)
        layout.addWidget(btn_close)
        
        dialog.setLayout(layout)
        dialog.exec()

    def _show_autotune_details_dialog(self, winners):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("testbench_autotune_details_title"))
        dialog.resize(700, 400)
        
        layout = QVBoxLayout()
        
        mode_labels = {"isolated": "A (Isolated)", "compact": "B (Compact)", "full": "C (Full)"}
        reasoning_levels = ["None", "Low", "Medium", "High"]
        
        table = QTableWidget(len(reasoning_levels), 4)
        table.setHorizontalHeaderLabels([
            self.tr("testbench_autotune_details_col_level"),
            self.tr("testbench_autotune_details_col_mode_a"),
            self.tr("testbench_autotune_details_col_mode_b"),
            self.tr("testbench_autotune_details_col_mode_c")
        ])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        
        for row_idx, level in enumerate(reasoning_levels):
            item_level = QTableWidgetItem(level)
            item_level.setTextAlignment(Qt.AlignCenter)
            table.setItem(row_idx, 0, item_level)
            
            for col_idx, mode in enumerate(["isolated", "compact", "full"]):
                winner_level = winners.get(mode, {}).get("level", "None")
                score_val = winners.get(mode, {}).get("score", 0.0) if level == winner_level else 0.0
                
                if level == winner_level:
                    item_score = QTableWidgetItem(f"★ {score_val:.1f}")
                    item_score.setForeground(Qt.green)
                else:
                    item_score = QTableWidgetItem("-")
                
                item_score.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_idx, col_idx + 1, item_score)
        
        layout.addWidget(QLabel(self.tr("testbench_autotune_details_desc")))
        layout.addWidget(table)
        
        summary_text = f"A={winners.get('isolated', {}).get('level', 'None')}, B={winners.get('compact', {}).get('level', 'Low')}, C={winners.get('full', {}).get('level', 'Low')}"
        layout.addWidget(QLabel(self.tr("testbench_autotune_details_summary").format(summary=summary_text)))
        
        btn_close = QPushButton(self.tr("testbench_history_close_btn"))
        btn_close.clicked.connect(dialog.accept)
        layout.addWidget(btn_close)
        
        dialog.setLayout(layout)
        dialog.exec()

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

        prompt_mode = self._get_test_bench_prompt_mode()
        if prompt_mode == "isolated":
            reasoning = self.combo_tb_reasoning_a.currentText() if hasattr(self, "combo_tb_reasoning_a") else "None"
        elif prompt_mode == "rule_names":
            reasoning = self.combo_tb_reasoning_b.currentText() if hasattr(self, "combo_tb_reasoning_b") else "Low"
        elif prompt_mode == "full":
            reasoning = self.combo_tb_reasoning_c.currentText() if hasattr(self, "combo_tb_reasoning_c") else "Low"
        else:
            reasoning = "Low"

        return {
            "model_ref": self.txt_tb_model.text().strip(),
            "rules_md_path": rules_path,
            "session_dump_path": str(TRAINER_ROOT / "session_test.txt"),
            "n_ctx": self.spin_tb_n_ctx.value(),
            "max_tokens": self.spin_tb_max_tokens.value(),
            "temperature": float(self.spin_tb_temperature.value()),
            "engine": engine_map.get(engine_idx, "auto"),
            "prompt_mode": prompt_mode,
            "testbench_reasoning_level": reasoning,
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
            str(OUTPUT_DIR),
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
        self._last_test_bench_config = dict(config)
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
        if not self.is_tb_queue_running:
            self.txt_tb_output.clear()
        self.lbl_tb_summary.setText(self.tr("testbench_running"))

        run_autotune = bool(getattr(self, "check_tb_autotune", None) and self.check_tb_autotune.isChecked())
        run_all_modes = bool(getattr(self, "check_tb_run_all_modes", None) and self.check_tb_run_all_modes.isChecked())

        if run_autotune:
            self.test_bench_sequence_active = True
            self.test_bench_sequence_stop_requested = False
            self.test_bench_autotune_active = True
            self.test_bench_sequence_queue = self._generate_autotune_grid()
            self.test_bench_sequence_reports = []
            self.test_bench_sequence_base_config = dict(config)
            self.test_bench_sequence_case_ids = list(selected_case_ids)
            total_runs = len(self.test_bench_sequence_queue)
            self.log_test_bench(self.tr("testbench_autotune_starting_log").format(total=total_runs))
            self._run_next_test_bench_sequence()
        elif run_all_modes:
            self.test_bench_sequence_active = True
            self.test_bench_sequence_stop_requested = False
            self.test_bench_autotune_active = False
            self.test_bench_sequence_queue = [{"mode": m, "reasoning": None} for m in self._get_test_bench_sequence_modes()]
            self.test_bench_sequence_reports = []
            self.test_bench_sequence_base_config = dict(config)
            self.test_bench_sequence_case_ids = list(selected_case_ids)
            self.log_test_bench(self.tr("testbench_sequence_starting_log"))
            self._run_next_test_bench_sequence()
        else:
            self.test_bench_sequence_active = False
            self.test_bench_sequence_stop_requested = False
            self.test_bench_autotune_active = False
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
        self.test_bench_thread.finished.connect(self._on_test_bench_thread_done)
        self.test_bench_thread.start()

    def _on_test_bench_thread_done(self):
        thread = self.test_bench_thread
        if thread:
            success = getattr(thread, '_result_success', False)
            payload = getattr(thread, '_result_payload', None)
            self.on_test_bench_finished(success, payload)

    def _generate_autotune_grid(self):
        modes = self._get_test_bench_sequence_modes()
        reasoning_levels = ["None", "Low", "Medium", "High"]
        cycles = self.spin_tb_autotune_cycles.value() if hasattr(self, "spin_tb_autotune_cycles") else 3
        queue = []
        for mode in modes:
            for level in reasoning_levels:
                for _ in range(cycles):
                    queue.append({"mode": mode, "reasoning": level})
        return queue

    def _run_next_test_bench_sequence(self):
        if self.test_bench_sequence_stop_requested:
            self._finalize_test_bench_sequence()
            return

        if not self.test_bench_sequence_queue:
            self._finalize_test_bench_sequence()
            return

        item = self.test_bench_sequence_queue.pop(0)
        if isinstance(item, dict):
            mode = item.get("mode", "isolated")
            reasoning_override = item.get("reasoning", None)
        else:
            mode = str(item)
            reasoning_override = None

        cfg = dict(self.test_bench_sequence_base_config or {})
        cfg["prompt_mode"] = mode

        if reasoning_override is not None:
            cfg["testbench_reasoning_level"] = reasoning_override
        elif mode == "isolated":
            cfg["testbench_reasoning_level"] = cfg.get("testbench_reasoning_a", "None")
        elif mode == "compact":
            cfg["testbench_reasoning_level"] = cfg.get("testbench_reasoning_b", "Low")
        elif mode == "full":
            cfg["testbench_reasoning_level"] = cfg.get("testbench_reasoning_c", "Low")
        else:
            cfg["testbench_reasoning_level"] = "Low"

        self._current_sequence_item = {"mode": mode, "reasoning": cfg["testbench_reasoning_level"]}

        remaining = len(self.test_bench_sequence_queue)
        mode_label = self._get_prompt_mode_label(mode)

        if getattr(self, "test_bench_autotune_active", False):
            self.log_test_bench(f"[AUTO-TUNE] ({remaining} restantes) Modo: {mode_label} | Razonamiento: {cfg['testbench_reasoning_level']}")
        else:
            self.log_test_bench(self.tr("testbench_sequence_mode_log").format(mode=mode_label))

        self.log_test_bench(f"[config] prompt_mode={mode}")
        self.log_test_bench(f"[config] reasoning_level={cfg['testbench_reasoning_level']}")
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
        was_autotune = getattr(self, "test_bench_autotune_active", False)

        self.test_bench_sequence_active = False
        self.test_bench_sequence_stop_requested = False
        self.test_bench_sequence_queue = []
        self.test_bench_sequence_base_config = {}
        self.test_bench_sequence_case_ids = []
        self.test_bench_autotune_active = False

        if not reports:
            if stopped:
                self.lbl_tb_summary.setText(self.tr("testbench_sequence_aborted"))
                self.log_test_bench(self.tr("testbench_sequence_aborted_log"))
            self.test_bench_report = None
            self.update_test_bench_status()
            return

        if was_autotune and not stopped:
            try:
                self._analyze_autotune_results(reports)
            except Exception as e:
                self.log_test_bench(f"[AUTO-TUNE][ERROR] Fallo al analizar resultados: {e}")
                self.lbl_tb_summary.setText(f"Error en análisis Auto-Tune: {e}")
                self.test_bench_report = {
                    "autotune": True,
                    "error": str(e),
                    "runs": reports,
                }
            self.test_bench_sequence_reports = []
            self.update_test_bench_status()
            if self._tb_queue_on_model_finished():
                return
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

        self.lbl_tb_summary.setText("Secuencia finalizada. (Ver detalles)")
        
        try:
            self.btn_tb_comparative.clicked.disconnect()
        except RuntimeError:
            pass
            
        self.btn_tb_comparative.clicked.connect(lambda: self._show_comparative_dialog(mode_rows))
        self.btn_tb_comparative.show()

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

        if self._tb_queue_on_model_finished():
            return

    def _analyze_autotune_results(self, reports):
        self.log_test_bench("=" * 60)
        self.log_test_bench("[AUTO-TUNE] Analizando resultados del grid search...")
        self.log_test_bench("=" * 60)

        scores = {}
        for report in reports:
            mode = str(report.get("prompt_mode", "isolated"))
            reasoning = str(report.get("_autotune_reasoning_level", "None"))
            key = (mode, reasoning)

            final_rate = float(report.get("final_rate", 0.0) or 0.0)
            narrative_avg = 0.0
            diagnostics = report.get("diagnostics", {}) or {}
            if isinstance(diagnostics, dict):
                narrative_avg = float(diagnostics.get("final_narrative_avg_score", 0.0) or 0.0)

            score = (final_rate * 10.0) + narrative_avg

            if key not in scores:
                scores[key] = []
            scores[key].append({
                "final_rate": final_rate,
                "narrative_avg": narrative_avg,
                "score": score,
            })

        mode_labels = {
            "isolated": "A",
            "compact": "B",
            "full": "C",
        }
        # Prioridad de niveles para desempate (de menor a mayor coste de tokens)
        reasoning_priority = ["None", "Low", "Medium", "High"]
        
        winners = {}

        for mode in ["isolated", "compact", "full"]:
            self.log_test_bench(f"\n[AUTO-TUNE] --- Fase {mode_labels.get(mode, mode)}: {self._get_prompt_mode_label(mode)} ---")
            best_level = "None"
            best_avg_score = -1.0
            all_candidates = []

            for level in ["None", "Low", "Medium", "High"]:
                key = (mode, level)
                entries = scores.get(key, [])
                if not entries:
                    self.log_test_bench(f"  {level}: Sin datos")
                    continue

                avg_score = sum(e["score"] for e in entries) / len(entries)
                avg_final = sum(e["final_rate"] for e in entries) / len(entries)
                avg_narr = sum(e["narrative_avg"] for e in entries) / len(entries)

                self.log_test_bench(
                    f"  {level}: Score={avg_score:.2f} | Pass Rate={avg_final:.1f}% | Narrativa={avg_narr:.1f} ({len(entries)} ciclos)"
                )

                all_candidates.append({"level": level, "score": avg_score})

            # Seleccionar el mejor con desempate por eficiencia de tokens
            if all_candidates:
                top_score = max(c["score"] for c in all_candidates)
                # Candidatos dentro del umbral de tolerancia respecto al mejor score
                within_threshold = [c for c in all_candidates if (top_score - c["score"]) < 15.0]
                # De los candidatos viables, elegir el de menor coste de tokens
                within_threshold.sort(key=lambda x: reasoning_priority.index(x["level"]))
                chosen = within_threshold[0]
                best_level = chosen["level"]
                best_avg_score = chosen["score"]

                # Log si la eficiencia descartó un candidato con mayor score
                absolute_best = max(all_candidates, key=lambda x: x["score"])
                if absolute_best["level"] != best_level:
                    self.log_test_bench(
                        f"  [EFICIENCIA] {absolute_best['level']} ({absolute_best['score']:.2f}) vs {best_level} ({best_avg_score:.2f})"
                        f" — diferencia {abs(absolute_best['score'] - best_avg_score):.2f} < 15.0 → se elige {best_level} (menor coste de tokens)."
                    )

            winners[mode] = {"level": best_level, "score": best_avg_score}
            self.log_test_bench(
                self.tr("testbench_autotune_winner").format(
                    mode=f"{mode_labels.get(mode, mode)}) {self._get_prompt_mode_label(mode)}",
                    winner=best_level,
                    score=best_avg_score,
                )
            )

        self.log_test_bench("\n" + "=" * 60)
        self._apply_autotune_winners(winners)

        self.test_bench_report = {
            "autotune": True,
            "winners": {m: w["level"] for m, w in winners.items()},
            "scores_detail": {
                f"{m}|{l}": [e["score"] for e in entries]
                for (m, l), entries in scores.items()
            },
            "runs": reports,
        }

    def _apply_autotune_winners(self, winners):
        w_a = winners.get("isolated", {}).get("level", "None")
        w_b = winners.get("compact", {}).get("level", "Low")
        w_c = winners.get("full", {}).get("level", "Low")

        if hasattr(self, "combo_tb_reasoning_a"):
            self.combo_tb_reasoning_a.setCurrentText(w_a)
        if hasattr(self, "combo_tb_reasoning_b"):
            self.combo_tb_reasoning_b.setCurrentText(w_b)
        if hasattr(self, "combo_tb_reasoning_c"):
            self.combo_tb_reasoning_c.setCurrentText(w_c)

        summary = self.tr("testbench_autotune_summary").format(a=w_a, b=w_b, c=w_c)
        self.lbl_tb_summary.setText(summary)
        self.log_test_bench(f"[AUTO-TUNE] {summary}")
        self.log_test_bench("[AUTO-TUNE] Niveles de razonamiento aplicados a los selectores de la interfaz.")
        
        try:
            self.btn_tb_comparative.clicked.disconnect()
        except RuntimeError:
            pass
        
        self.btn_tb_comparative.clicked.connect(lambda: self._show_autotune_details_dialog(winners))
        self.btn_tb_comparative.show()

    def stop_test_bench(self):
        if self.is_tb_queue_running:
            self.is_tb_queue_running = False
            self._tb_queue_awaiting_finish = False
            for job in self.tb_model_queue:
                if job["status"] == "processing":
                    job["status"] = "error"
            self._tb_queue_refresh()
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
        try:
            self._on_test_bench_finished_impl(success, payload)
        except Exception as e:
            self.log_test_bench(f"[ERROR CRÍTICO] on_test_bench_finished: {e}")
            self.lbl_tb_summary.setText(f"Error interno: {e}")
            self.test_bench_sequence_active = False
            self.test_bench_sequence_stop_requested = False
            if self.test_bench_thread:
                self.test_bench_thread.deleteLater()
                self.test_bench_thread = None
            self.update_test_bench_status()

    def _on_test_bench_finished_impl(self, success, payload):
        if self.test_bench_thread:
            self.test_bench_thread.deleteLater()
            self.test_bench_thread = None

        if self.test_bench_sequence_active:
            if success and isinstance(payload, dict):
                seq_item = getattr(self, "_current_sequence_item", None)
                if seq_item and isinstance(seq_item, dict):
                    payload["_autotune_reasoning_level"] = seq_item.get("reasoning", "None")
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
                QTimer.singleShot(0, self._finalize_test_bench_sequence)
            else:
                QTimer.singleShot(0, self._run_next_test_bench_sequence)
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

        if self._tb_queue_on_model_finished():
            return

    def export_test_bench_report(self):
        if not isinstance(self.test_bench_report, dict):
            return

        self._attach_testbench_meta(self.test_bench_report)
        
        try:
            suite_metrics = {}
            runs = self.test_bench_report.get("runs", [])
            if not runs and "cases" in self.test_bench_report:
                runs = [self.test_bench_report]
                
            for run in runs:
                mode = str(run.get("prompt_mode", "isolated"))
                reasoning = str(run.get("_autotune_reasoning_level", "None"))
                key_mode = f"{mode}_{reasoning}"
                
                for case_result in run.get("cases", []):
                    case_id = case_result.get("case_id", "unknown")
                    if case_id not in suite_metrics:
                        suite_metrics[case_id] = {
                            "total_runs": 0,
                            "successes": 0,
                            "failures": 0,
                            "by_mode": {}
                        }
                    
                    if key_mode not in suite_metrics[case_id]["by_mode"]:
                        suite_metrics[case_id]["by_mode"][key_mode] = {
                            "runs": 0,
                            "successes": 0,
                            "failures": 0
                        }
                    
                    passed = bool(case_result.get("passed_final", False))
                    
                    suite_metrics[case_id]["total_runs"] += 1
                    suite_metrics[case_id]["by_mode"][key_mode]["runs"] += 1
                    
                    if passed:
                        suite_metrics[case_id]["successes"] += 1
                        suite_metrics[case_id]["by_mode"][key_mode]["successes"] += 1
                    else:
                        suite_metrics[case_id]["failures"] += 1
                        suite_metrics[case_id]["by_mode"][key_mode]["failures"] += 1
            
            if suite_metrics:
                self.test_bench_report["suite_metrics"] = suite_metrics
        except Exception as e:
            self.log_test_bench(f"Error generando metricas por suite: {e}")

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

    # --- TEST BENCH MODEL QUEUE ---
    def tb_queue_add_models(self):
        fnames, _ = QFileDialog.getOpenFileNames(
            self,
            self.tr("tb_queue_select_title"),
            str(OUTPUT_DIR),
            "GGUF Files (*.gguf);;All Files (*)",
        )
        for fname in fnames:
            fname = fname.strip()
            if not fname:
                continue
            already = any(j["model_path"] == fname for j in self.tb_model_queue)
            if already:
                continue
            self.tb_model_queue.append({"model_path": fname, "status": "pending"})
            self.log_test_bench(self.tr("tb_queue_added").format(model=Path(fname).name))
        self._tb_queue_refresh()

    def tb_queue_remove_selected(self):
        row = self.list_tb_queue.currentRow()
        if row < 0:
            return
        if self.is_tb_queue_running and row == self.current_tb_queue_index:
            return
        self.tb_model_queue.pop(row)
        if self.is_tb_queue_running and row < self.current_tb_queue_index:
            self.current_tb_queue_index -= 1
        self._tb_queue_refresh()

    def _tb_queue_refresh(self):
        self.list_tb_queue.clear()
        for job in self.tb_model_queue:
            status_key = f"status_{job['status']}"
            status_text = self.tr(status_key)
            model_name = Path(job["model_path"]).name
            text = self.tr("tb_queue_item_format").format(status=status_text, model=model_name)
            item = QListWidgetItem(text)
            if job["status"] == "processing":
                item.setBackground(Qt.darkCyan)
            elif job["status"] == "done":
                item.setBackground(Qt.darkGreen)
            elif job["status"] == "error":
                item.setBackground(Qt.darkRed)
            self.list_tb_queue.addItem(item)
        self.update_test_bench_status()

    def tb_queue_start(self):
        pending = [j for j in self.tb_model_queue if j["status"] == "pending"]
        if not pending:
            self.log_test_bench(self.tr("tb_queue_empty"))
            return

        selected_case_ids = self._get_selected_test_case_ids()
        if not selected_case_ids:
            QMessageBox.warning(self, self.tr("testbench_error_title"), self.tr("testbench_error_cases_missing"))
            return

        self.is_tb_queue_running = True
        self.current_tb_queue_index = -1
        for i, job in enumerate(self.tb_model_queue):
            if job["status"] == "pending":
                self.current_tb_queue_index = i
                break
        self.update_test_bench_status()
        self._tb_queue_process_next()

    def _tb_queue_process_next(self):
        if self.current_tb_queue_index < 0 or self.current_tb_queue_index >= len(self.tb_model_queue):
            self._tb_queue_finish()
            return

        job = self.tb_model_queue[self.current_tb_queue_index]
        if job["status"] != "pending":
            self.current_tb_queue_index += 1
            self._tb_queue_process_next()
            return

        job["status"] = "processing"
        self._tb_queue_refresh()

        total = len(self.tb_model_queue)
        model_name = Path(job["model_path"]).name
        self.log_test_bench("=" * 60)
        self.log_test_bench(self.tr("tb_queue_processing").format(
            current=self.current_tb_queue_index + 1, total=total, model=model_name
        ))

        self.txt_tb_model.setText(job["model_path"])
        self._tb_queue_awaiting_finish = True
        self.start_test_bench()

    def _tb_queue_on_model_finished(self):
        if not self.is_tb_queue_running or not getattr(self, "_tb_queue_awaiting_finish", False):
            return False
        self._tb_queue_awaiting_finish = False

        job = self.tb_model_queue[self.current_tb_queue_index]
        model_name = Path(job["model_path"]).name

        if isinstance(self.test_bench_report, dict) and (self.test_bench_report.get("runs") or self.test_bench_report.get("cases")):
            job["status"] = "done"
            self._attach_testbench_meta(self.test_bench_report)
            try:
                export_dir = OUTPUT_DIR / "test_bench_reports"
                export_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                target = export_dir / f"test_bench_report_{ts}.json"
                with open(target, "w", encoding="utf-8") as f:
                    json.dump(self.test_bench_report, f, ensure_ascii=False, indent=2)
                self.log_test_bench(self.tr("tb_queue_model_done").format(model=model_name))
            except Exception as e:
                self.log_test_bench(f"[COLA] Error exportando reporte: {e}")
        else:
            job["status"] = "error"
            self.log_test_bench(self.tr("tb_queue_model_error").format(model=model_name, error="sin reporte"))

        self._tb_queue_refresh()
        self.current_tb_queue_index += 1
        QTimer.singleShot(500, self._tb_queue_process_next)
        return True

    def _tb_queue_finish(self):
        ok_count = sum(1 for j in self.tb_model_queue if j["status"] == "done")
        fail_count = sum(1 for j in self.tb_model_queue if j["status"] == "error")
        total = len(self.tb_model_queue)
        self.is_tb_queue_running = False
        self.current_tb_queue_index = -1
        self.log_test_bench("=" * 60)
        self.log_test_bench(self.tr("tb_queue_finished").format(ok=ok_count, fail=fail_count, total=total))
        self.update_test_bench_status()

    def update_test_bench_status(self):
        active = self.test_bench_thread is not None and self.test_bench_thread.isRunning()

        # Si no hay test_bench_report y no estamos activos, ocultar el boton comparativo
        if not active and not self.test_bench_report:
            if hasattr(self, 'btn_tb_comparative'):
                self.btn_tb_comparative.hide()
        has_model = bool(getattr(self, "txt_tb_model", None) and self.txt_tb_model.text().strip())

        if hasattr(self, "btn_tb_export"):
            self.btn_tb_export.setEnabled((not active) and isinstance(self.test_bench_report, dict))

        if hasattr(self, "txt_tb_model"):
            self.txt_tb_model.setEnabled(not active)
        if hasattr(self, "btn_tb_model_file"):
            self.btn_tb_model_file.setEnabled(not active)
        if hasattr(self, "txt_tb_rules"):
            self.txt_tb_rules.setEnabled(not active)
        if hasattr(self, "btn_tb_rules"):
            self.btn_tb_rules.setEnabled(not active)
        if hasattr(self, "list_tb_cases"):
            self.list_tb_cases.setEnabled(not active)
        if hasattr(self, "spin_tb_n_ctx"):
            self.spin_tb_n_ctx.setEnabled(not active)
        if hasattr(self, "spin_tb_max_tokens"):
            self.spin_tb_max_tokens.setEnabled(not active)
        if hasattr(self, "spin_tb_temperature"):
            self.spin_tb_temperature.setEnabled(not active)
        if hasattr(self, "spin_tb_case_count"):
            self.spin_tb_case_count.setEnabled(not active)
        if hasattr(self, "check_tb_narrative_gate"):
            self.check_tb_narrative_gate.setEnabled(not active)
        if hasattr(self, "spin_tb_narrative_min"):
            self.spin_tb_narrative_min.setEnabled(not active)
        if hasattr(self, "spin_tb_narrative_hard_fail"):
            self.spin_tb_narrative_hard_fail.setEnabled(not active)
        if hasattr(self, "check_tb_fallback"):
            self.check_tb_fallback.setEnabled(not active)
        if hasattr(self, "combo_tb_prompt_mode"):
            self.combo_tb_prompt_mode.setEnabled(not active)
        if hasattr(self, "check_tb_run_all_modes"):
            self.check_tb_run_all_modes.setEnabled(not active)
        if hasattr(self, "spin_tb_first_pass"):
            self.spin_tb_first_pass.setEnabled(not active)
        if hasattr(self, "spin_tb_final"):
            self.spin_tb_final.setEnabled(not active)
        if hasattr(self, "spin_tb_fallback"):
            self.spin_tb_fallback.setEnabled(not active)
        if hasattr(self, "check_tb_autotune"):
            self.check_tb_autotune.setEnabled(not active)
        if hasattr(self, "spin_tb_autotune_cycles"):
            self.spin_tb_autotune_cycles.setEnabled(not active)
        if hasattr(self, "combo_tb_reasoning_a"):
            self.combo_tb_reasoning_a.setEnabled(not active)
        if hasattr(self, "combo_tb_reasoning_b"):
            self.combo_tb_reasoning_b.setEnabled(not active)
        if hasattr(self, "combo_tb_reasoning_c"):
            self.combo_tb_reasoning_c.setEnabled(not active)

        queue_busy = active or self.is_tb_queue_running
        if hasattr(self, "btn_tb_queue_add"):
            self.btn_tb_queue_add.setEnabled(not queue_busy)
        if hasattr(self, "btn_tb_queue_remove"):
            self.btn_tb_queue_remove.setEnabled(not queue_busy)
        if hasattr(self, "btn_tb_queue_start"):
            self.btn_tb_queue_start.setEnabled(not queue_busy and bool(self.tb_model_queue))
        if hasattr(self, "btn_tb_run"):
            self.btn_tb_run.setEnabled((not queue_busy) and has_model)
        if hasattr(self, "btn_tb_stop"):
            self.btn_tb_stop.setEnabled(active or self.is_tb_queue_running)

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
        
        # Micro: < 4B (0.5b, 1.5b, 1b, 2b, 3b)
        if re.search(r'\b(0\.5b|1\.5b|1b|2b|3b|mini)\b', name):
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
        rank, alpha, batch, lr, epochs = 16, 32, 4, 0.0002, 3
        
        if size_cat == "micro":
            # 1.5B-3B: Rank 16, Alpha 32 (2x), Batch 8, LR 2e-4, Ep 3
            # Effective batch sin FLA: 8*4=32 (estable). Rank 16 validado con 2B.
            rank, alpha, batch, lr, epochs = 16, 32, 8, 0.0002, 3
        elif size_cat == "small":
            # 4B-8B: Rank 16, Alpha 32 (2x), Batch 4, LR 2e-4, Ep 3
            # Rank 32 causa degradacion empirica en Qwen3-8B. Effective batch 4*4=16.
            rank, alpha, batch, lr, epochs = 16, 32, 4, 0.0002, 3
        elif size_cat == "medium":
            # 9B-14B: Rank 16, Alpha 32 (2x), Batch 4, LR 1.5e-4, Ep 2
            # Rank 64 destruye capacidades en modelos Qwen3 (empirico Reddit/paper).
            # Effective batch sin FLA: 4*4=16 (optimo segun Unsloth).
            rank, alpha, batch, lr, epochs = 16, 32, 4, 0.00015, 2
        elif size_cat == "large":
            # >14B: Rank 8, Alpha 16 (2x), Batch 4, LR 1e-4, Ep 2
            # Modelos grandes tienen mas capacidad interna, menos adaptacion LoRA necesaria.
            # Effective batch sin FLA: 4*4=16. Rank 8 fue optimo incluso para 8B (Reddit).
            rank, alpha, batch, lr, epochs = 8, 16, 4, 0.0001, 2
            
        # Estilo narrativo: siempre funcional. El SFT enseña estructura, no estilo.
        # Los modelos ya tienen creatividad latente del pre-training; inyectarles prosa los sesga.
        narrative_style = "functional"

        # Train on responses only: OFF para micro (<4B), ON para 4B+.
        # Evidencia: Exp1 (Mar 2026) - modelos pequeños necesitan loss completo.
        train_on_responses = "off" if size_cat == "micro" else "on"

        # Think-Suppression: siempre suprimir para RolemIAster (JSON puro).
        thinking_mode = "suppress"

        self.spin_rank.setValue(rank)
        self.spin_alpha.setValue(alpha)
        self.spin_batch.setValue(batch)
        self.spin_lr.setValue(lr)
        self.spin_epochs.setValue(epochs)

        if hasattr(self, "combo_narrative_style"):
            idx = self.combo_narrative_style.findData(narrative_style)
            if idx >= 0:
                self.combo_narrative_style.setCurrentIndex(idx)

        if hasattr(self, "combo_train_on_responses"):
            idx = self.combo_train_on_responses.findData(train_on_responses)
            if idx >= 0:
                self.combo_train_on_responses.setCurrentIndex(idx)

        if hasattr(self, "combo_thinking_mode"):
            idx = self.combo_thinking_mode.findData(thinking_mode)
            if idx >= 0:
                self.combo_thinking_mode.setCurrentIndex(idx)

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

        thinking_mode = "suppress"
        if hasattr(self, "combo_thinking_mode"):
            thinking_mode = self.combo_thinking_mode.currentData() or "suppress"

        narrative_style = "functional"
        if hasattr(self, "combo_narrative_style"):
            narrative_style = self.combo_narrative_style.currentData() or "functional"

        train_on_responses = "on"
        if hasattr(self, "combo_train_on_responses"):
            train_on_responses = self.combo_train_on_responses.currentData() or "on"

        return {
            "rank": self.spin_rank.value(),
            "alpha": self.spin_alpha.value(),
            "batch_size": self.spin_batch.value(),
            "epochs": self.spin_epochs.value(),
            "learning_rate": self.spin_lr.value(),
            "engine": engine_map.get(engine_idx, "auto"),
            "lang": getattr(self, "current_lang", "en"),
            "thinking_mode": thinking_mode,
            "narrative_style": narrative_style,
            "train_on_responses": train_on_responses,
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

            p = job["params"]
            thinking_label = self.tr("thinking_mode_suppress") if p.get("thinking_mode") == "suppress" else self.tr("thinking_mode_native")
            narrative_label = self.tr("narrative_style_functional") if p.get("narrative_style") == "functional" else self.tr("narrative_style_literary")
            toro_label = self.tr("train_on_responses_on") if p.get("train_on_responses") == "on" else self.tr("train_on_responses_off")
            tooltip = (
                f"Modelo: {job['model'].name}\n"
                f"Reglas: {job['md'].name}\n"
                f"─────────────────────\n"
                f"LoRA Rank: {p.get('rank')}  |  Alpha: {p.get('alpha')}\n"
                f"Batch Size: {p.get('batch_size')}  |  Epochs: {p.get('epochs')}\n"
                f"Learning Rate: {p.get('learning_rate')}\n"
                f"─────────────────────\n"
                f"Razonamiento: {thinking_label}\n"
                f"Estilo Narrativo: {narrative_label}\n"
                f"Loss: {toro_label}"
            )
            item.setToolTip(tooltip)

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

    def generate_dataset(self):
        if not self.md_file:
            QMessageBox.warning(self, self.tr("dlg_error_title"), self.tr("dlg_no_md_file"))
            return

        output_path = OUTPUT_DIR / "training_dataset.jsonl"
        self.log(self.tr("log_generating_dataset"))
        self.btn_generate_dataset.setEnabled(False)

        try:
            from core.preparar_dataset import generate_robust_dataset
            success = generate_robust_dataset(str(self.md_file), str(output_path))
            if success:
                self.log(self.tr("log_dataset_generated").format(path=str(output_path)))
                QMessageBox.information(self, self.tr("dlg_success_title"), self.tr("dlg_dataset_generated"))
            else:
                self.log(self.tr("log_dataset_error"))
                QMessageBox.critical(self, self.tr("dlg_error_title"), self.tr("dlg_dataset_failed"))
        except Exception as e:
            self.log(f"[ERROR] {e}")
            QMessageBox.critical(self, self.tr("dlg_error_title"), str(e))
        finally:
            self.btn_generate_dataset.setEnabled(True)

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

