"""V2: defeitos VISIVEIS (emenda, blocos) medidos e barrados - correcoes do smoke test de 2026-10-07.

No smoke test a validacao deu WARN numa imagem com mancha clara de borda dura no colo, cabelo recortado
e quadrados no braco. Estes testes garantem que (1) a medida pega esses defeitos, (2) a integracao nao
mexe mais na borda cabelo x fundo, (3) uma etapa que cria blocos e desfeita e (4) "nao medido" nao passa.
"""
import numpy as np

from app.core.engines.adapter import AdapterResult
from app.core.engines.integration import integrate
from app.core.engines.validation import REJECT, WARN, check_original_residual, seam_excess, straight_edges, validate_v2
from tests.test_engines_replacement import FakeAdapter, engine, req


def scene(h=120, w=120):
    """Pele com gradiente suave e textura leve (nao e um plano liso)."""
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([170 + xx * 0.2, 125 + yy * 0.15, 105 + 0 * xx], -1)
    return np.clip(base + rng.normal(0, 2, (h, w, 3)), 0, 255).astype(np.uint8)


def square(h=120, w=120, y0=30, y1=90, x0=30, x1=90):
    m = np.zeros((h, w), np.float32)
    m[y0:y1, x0:x1] = 1
    return m


def test_hard_edged_patch_is_a_seam_and_a_smooth_change_is_not():
    orig, reg = scene(), square()
    patch = orig.astype(np.int16).copy()
    patch[reg > 0] += 30  # mancha clara de borda dura (colo do smoke test)
    hard = np.clip(patch, 0, 255).astype(np.uint8)
    yy, xx = np.mgrid[0:120, 0:120]
    ramp = np.clip(1 - np.maximum(np.abs(yy - 60), np.abs(xx - 60)) / 30.0, 0, 1)  # 0 na borda, 1 no centro
    soft = np.clip(orig + (30 * ramp)[..., None], 0, 255).astype(np.uint8)
    assert seam_excess(orig, hard, reg) > 10
    assert seam_excess(orig, soft, reg) < 3
    assert seam_excess(orig, orig, reg) == 0


def test_square_blocks_inside_the_region_are_counted():
    orig, reg = scene(), square()
    blocky = orig.copy()
    for y in range(36, 84, 16):
        for x in range(36, 84, 16):
            blocky[y:y + 9, x:x + 9] = (120, 120, 125)  # quadrados cinza (braco do smoke test)
    assert straight_edges(orig, blocky, reg) > 12
    assert straight_edges(orig, orig, reg) == 0


def test_validation_rejects_seams_and_reports_unmeasured_original_face():
    rep = validate_v2({"identity": 0.8, "person_found": True, "seam_excess": 11.2, "straight_edges": 13.3, "persona_instances": 1})
    assert rep.checks["seams"].status == REJECT and rep.status == REJECT
    res = check_original_residual(None, 0.0, None, {"warn": 0.3, "reject": 0.4}, {"pass": 0.03, "reject": 0.15},
                                  {"pass": 0.08, "reject": 0.3})
    assert res.status == WARN and "NAO medida" in res.reason


def test_integration_never_shifts_hair_against_the_background():
    """Borda cabelo x parede: o degrau pele x pele nao pode entrar ali (cabelo clareado no smoke test)."""
    h, w = 120, 120
    orig = np.full((h, w, 3), 235, np.uint8)  # parede clara
    orig[60:, :] = (200, 160, 140)  # pele original do colo (mais clara)
    cur = orig.copy()
    region = np.zeros((h, w), np.float32)
    region[10:80, 30:90] = 1
    cur[10:60, 30:90] = (60, 40, 30)  # cabelo gerado (encosta na parede)
    cur[60:80, 30:90] = (150, 105, 85)  # pele gerada mais escura (encosta na pele original)
    body = np.zeros((h, w), np.float32)
    body[60:, :] = 1
    body[region > 0] = 0
    out, rel = integrate(orig, cur, region, body_skin=body, seed=1)
    assert rel["degrau_limitado"] is True  # degrau de ~50: limitado, nao "consertado" por inteiro
    assert np.abs(out[10:55, 30:90].astype(int) - cur[10:55, 30:90].astype(int)).max() <= 8  # cabelo: so grao
    assert (out[70:80, 35:85].astype(int) - cur[70:80, 35:85].astype(int)).mean() > 2  # pele perto da pele: clareou
    assert (out[region < 0.5] == cur[region < 0.5]).all()


async def test_stage_that_creates_blocks_is_rolled_back():
    class BlockyTattoo(FakeAdapter):
        async def inpaint(self, r):
            res = await super().inpaint(r)
            if r.stage != "tattoo_cleanup":
                return res
            px = self.store.images[res.image].copy()
            ys, xs = np.nonzero(r.mask > 0.5)
            for y in range(ys.min(), ys.max(), 6):
                for x in range(xs.min(), xs.max(), 6):
                    px[y:y + 4, x:x + 4] = (90, 90, 95)
            key = await self.store.save(px, "blocky")
            return AdapterResult(key, res.seconds, res.gpu, res.parameters)

    faces = {"foto": 0.1, "identity": 0.8, "face_refine": 0.8, "tattoo": 0.8, "integrated": 0.8, "final": 0.8}
    eng, _, store = engine(faces)
    eng.adapter = BlockyTattoo(store)
    out = await eng.run(req(advanced={"max_retries": 0}, keep_intermediates=True))
    tat = next(p for p in out.telemetry.passes if p["pass"] == "tattoo_cleanup")
    assert tat["accepted"] is False and "defeito visivel" in tat["reason"]
    assert out.measures["straight_edges"] is not None and "mask_modified" in out.intermediates


async def test_face_refine_without_real_gain_is_not_kept():
    faces = {"foto": 0.1, "identity": 0.65, "face_refine": 0.66, "tattoo": 0.66, "integrated": 0.66, "final": 0.66}
    eng, _, _ = engine(faces)
    out = await eng.run(req(advanced={"max_retries": 0}))
    ident = next(p for p in out.telemetry.passes if p["pass"] == "identity")
    fr = next(p for p in out.telemetry.passes if p["pass"] == "face_refine")
    assert ident["identity"] == 0.65
    assert fr["accepted"] is False and "ganho de identidade pequeno" in fr["reason"]
