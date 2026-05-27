from __future__ import annotations

import pytest

from agentlog import __version__
from agentlog.cli import SUBCOMMANDS, main


def test_no_args_prints_help_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "agentlog" in out
    for name in SUBCOMMANDS:
        assert name in out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize("cmd", [c for c in SUBCOMMANDS if c not in {"init", "uninstall", "tail", "ls"}])
def test_subcommands_registered_but_not_implemented(
    cmd: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main([cmd])
    err = capsys.readouterr().err
    assert rc == 2
    assert "not yet implemented" in err
