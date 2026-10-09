"""Modelos da troca fora do volume: baixa uma vez, linka no ComfyUI, relinka link quebrado, usa arquivo real."""
import os

import pytest

from app.providers.comfyui import model_provision as mp


@pytest.mark.skipif(os.name == "nt", reason="symlink no Windows pede privilegio de administrador; o pod e Linux")
async def test_downloads_links_and_reuses(tmp_path, monkeypatch):
    models, cache = tmp_path / "models", tmp_path / "cache"
    (models / "checkpoints").mkdir(parents=True)
    calls = []

    async def fake(url, target):
        calls.append(url)
        target.write_bytes(b"x" * (2 << 20))

    monkeypatch.setattr(mp, "_download", fake)
    cfg = {"comfyui_models_dir": str(models), "cache_dir": str(cache),
           "files": [{"dest": "checkpoints/a.safetensors", "url": "u1"}, {"dest": "instantid/b.bin", "url": "u2"}]}
    assert sorted(await mp.ensure_models(cfg)) == ["checkpoints/a.safetensors", "instantid/b.bin"]
    assert os.path.islink(models / "checkpoints" / "a.safetensors") and (models / "instantid" / "b.bin").exists()
    assert await mp.ensure_models(cfg) == [] and len(calls) == 2  # ja pronto: nada baixa
    (cache / "a.safetensors").unlink()  # pod religado: disco temporario zerado, link quebrado
    assert await mp.ensure_models(cfg) == ["checkpoints/a.safetensors"]
    real = models / "checkpoints" / "c.safetensors"
    real.write_bytes(b"r")
    cfg["files"].append({"dest": "checkpoints/c.safetensors", "url": "u3"})
    await mp.ensure_models(cfg)
    assert "u3" not in calls and not os.path.islink(real)  # arquivo real do volume: nao toca


async def test_failure_is_an_error_and_no_config_is_noop(tmp_path, monkeypatch):
    assert await mp.ensure_models(None) == []
    assert await mp.ensure_models({"comfyui_models_dir": str(tmp_path / "nao_existe"), "files": [{"dest": "x", "url": "u"}]}) == []

    async def broken(url, target):
        raise mp.ProvisionError("HTTP 404")

    monkeypatch.setattr(mp, "_download", broken)
    (tmp_path / "m").mkdir()
    with pytest.raises(mp.ProvisionError):
        await mp.ensure_models({"comfyui_models_dir": str(tmp_path / "m"), "cache_dir": str(tmp_path / "c"),
                                "files": [{"dest": "checkpoints/a.safetensors", "url": "u"}]})
