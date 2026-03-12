import os
from huggingface_hub import snapshot_download
from pathlib import Path

class ModelDownloader:
    def __init__(self, log_func=print, tr_func=lambda k, **kw: k):
        self.log = log_func
        self.tr = tr_func
        self.stop_requested = False

    def download_model(self, repo_id, output_dir):
        """
        Descarga un modelo desde HuggingFace a un directorio local.
        """
        self.log(self.tr("log_download_start").format(repo_id=repo_id))
        
        try:
            # Crear nombre de carpeta limpio
            folder_name = repo_id.replace("/", "_")
            target_path = Path(output_dir) / folder_name
            
            if target_path.exists() and any(target_path.iterdir()):
                self.log(self.tr("log_download_dir_exists").format(target_path=target_path))
                self.log(self.tr("log_download_verifying"))
            
            # Descargar (snapshot_download maneja resume y caché)
            local_dir = snapshot_download(
                repo_id=repo_id,
                local_dir=str(target_path),
                local_dir_use_symlinks=False, # Descargar archivos reales, no enlaces
                resume_download=True
            )
            
            self.log(self.tr("log_download_complete").format(local_dir=local_dir))
            return str(local_dir)
            
        except Exception as e:
            import traceback
            self.log(self.tr("log_download_error_core").format(e=str(e)))
            print(f"[ERROR] Traceback completo de la descarga:\n{traceback.format_exc()}")
            return None

    def stop(self):
        self.stop_requested = True
