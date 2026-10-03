import importlib.util
from pathlib import Path


def test_lambda_handler_imports() -> None:
    handler_file = Path(__file__).resolve().parent.parent / "app" / "lambda_handler.py"
    spec = importlib.util.spec_from_file_location("research_app_lambda_handler", handler_file)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert getattr(mod, "handler", None) is not None
