import os

from local_private_ollama_mcp.config import load_dotenv


def test_dotenv_loads_but_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nexport OLLAMA_MODEL=from-file\nNUM_CTX=4096 # inline\nDOC_ROOT=$HOME/docs\n"
        "OLLAMA_API_KEY='a$b'\nOUTPUT_DIR=\"/x y\"\n",
        encoding="utf-8",
    )
    for k in ("OLLAMA_MODEL", "DOC_ROOT", "OLLAMA_API_KEY", "OUTPUT_DIR"):
        monkeypatch.setenv(k, "")  # registers cleanup so values set by load_dotenv do not leak
        monkeypatch.delenv(k)
    monkeypatch.setenv("HOME", "/home/u")
    monkeypatch.setenv("NUM_CTX", "9999")  # explicit setting wins
    assert load_dotenv(env)
    assert os.environ["OLLAMA_MODEL"] == "from-file"
    assert os.environ["NUM_CTX"] == "9999"
    assert os.environ["DOC_ROOT"] == "/home/u/docs"
    assert os.environ["OLLAMA_API_KEY"] == "a$b"
    assert os.environ["OUTPUT_DIR"] == "/x y"


def test_dotenv_missing_file(tmp_path):
    assert not load_dotenv(tmp_path / "nope.env")
