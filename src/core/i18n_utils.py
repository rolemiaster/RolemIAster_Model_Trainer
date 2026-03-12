import json
from pathlib import Path

_i18n_cache = {}

def get_translator(lang="en", i18n_dir=None):
    """
    Returns a translation function `tr(key, **kwargs)` for the desired language.
    If the language file is not found, tries English as fallback.
    If the key is not in JSON, returns the key itself.
    """
    if i18n_dir is None:
        # Assuming src/core/i18n_utils.py -> src/i18n
        i18n_dir = Path(__file__).parent.parent / "i18n"
        
    def _load_lang(l):
        if l in _i18n_cache:
            return _i18n_cache[l]
        p = i18n_dir / f"{l}.json"
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _i18n_cache[l] = json.load(f)
                    return _i18n_cache[l]
            except Exception:
                pass
        return {}

    lang_dict = _load_lang(lang)
    fallback_dict = _load_lang("en")

    def tr(key, **kwargs):
        val = lang_dict.get(key, fallback_dict.get(key, key))
        if kwargs:
            try:
                return val.format(**kwargs)
            except KeyError:
                return val # Si fallan los kwargs, devolvemos sin formatear
        return val

    return tr
