from unittest.mock import patch
from src import main as cli


def run(argv):
    return cli.main(argv)


@patch("src.api.get_random_joke", return_value="Hello Chuck")
def test_cli_random(mock_func, capsys):
    code = run(["random"])
    captured = capsys.readouterr()
    assert code == 0
    assert "Hello Chuck" in captured.out


@patch("src.api.get_categories", return_value=["animal", "career"]) 
def test_cli_categories(mock_func, capsys):
    code = run(["categories"])
    out = capsys.readouterr().out
    assert code == 0
    assert "animal" in out and "career" in out


@patch("src.api.search_jokes", return_value=["a", "b"]) 
def test_cli_search(mock_func, capsys):
    code = run(["search", "code", "--limit", "2"]) 
    out = capsys.readouterr().out
    assert code == 0
    assert "1. a" in out and "2. b" in out

