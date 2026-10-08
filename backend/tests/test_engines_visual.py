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
    # rosto/cabelo novos nao contam como bloco: na foto sintetica sobra pouca pele modificada (pode nao haver medida)
    assert "straight_edges" in out.measures and "mask_modified" in out.intermediates


async def test_face_refine_without_real_gain_is_not_kept():
    faces = {"foto": 0.1, "identity": 0.65, "face_refine": 0.66, "tattoo": 0.66, "integrated": 0.66, "final": 0.66}
    eng, _, _ = engine(faces)
    out = await eng.run(req(advanced={"max_retries": 0}))
    ident = next(p for p in out.telemetry.passes if p["pass"] == "identity")
    fr = next(p for p in out.telemetry.passes if p["pass"] == "face_refine")
    assert ident["identity"] == 0.65
    assert fr["accepted"] is False and "ganho de identidade pequeno" in fr["reason"]


async def test_sunglasses_are_generated_over_and_pasted_back():
    """Varanda (reteste): o buraco dos oculos partia o rosto. Agora a mascara cobre os oculos e os
    pixels ORIGINAIS deles voltam por cima depois de cada passe."""
    from app.core.persona_replacement.contracts import RawSegments
    from tests.test_persona_transfer import wide_tattoo_photo

    class GlassesSeg:
        async def segment(self, image, sheet):
            _, person, hair, clothes = wide_tattoo_photo()
            return RawSegments(person, hair, [(64.0, 46.0, 96.0, 56.0)], clothes=clothes)

    faces = {"foto": 0.1, "identity": 0.8, "face_refine": 0.8, "tattoo": 0.8, "integrated": 0.8, "final": 0.8}
    eng, ad, store = engine(faces)
    store.images["foto.png"][48:55, 66:94] = (15, 15, 20)  # lentes escuras
    eng.segmenter = GlassesSeg()
    out = await eng.run(req(advanced={"max_retries": 0}))
    ident = ad.calls[0]
    assert ident.stage == "identity" and ident.mask[51, 80] > 0.5  # oculos DENTRO da mascara de geracao
    assert (out.pixels[50:53, 70:90] == (15, 15, 20)).all()  # e de volta por cima no final


def test_thin_ink_lines_vanish_but_hand_shading_stays():
    """Teste de 2026-10-07: a entrada lisa virou a mao fechada numa luva. O fechamento tira o TRACO fino e
    mantem o sombreado das juntas; a tinta cheia vai para o push-pull."""
    from app.core.engines.skin import structure_preserving_fill

    img = scene(80, 80).astype(np.int16)
    img[:, 40:] -= 25  # sombra larga (lado da mao): tem que ficar
    for y in range(10, 70, 12):
        img[y:y + 2, 10:70] = (60, 55, 60)  # tracos finos de tatuagem
    img[50:66, 12:28] = (40, 40, 45)  # tinta cheia
    img = np.clip(img, 0, 255).astype(np.uint8)
    zone = np.zeros((80, 80), np.float32)
    zone[6:70, 8:72] = 1
    known = np.ones((80, 80), np.float32) - zone
    out, solid = structure_preserving_fill(img, zone, known, line_radius=3)
    lum = out.astype(np.float32) @ np.array([0.299, 0.587, 0.114])
    assert lum[34:36, 15:35].mean() > 120  # traco fino sumiu
    assert lum[20:30, 10:35].mean() - lum[20:30, 45:70].mean() > 12  # sombra larga continua
    assert solid[56:60, 16:24].all() and lum[56:60, 16:24].mean() > 120  # tinta cheia preenchida


def test_isolated_dots_are_not_markings():
    from app.core.engines.skin import drop_small_blobs

    m = np.zeros((60, 60), np.float32)
    m[10:30, 10:30] = 1  # tatuagem
    m[45:47, 45:47] = 1  # pintinha
    out = drop_small_blobs(m, 3)
    assert out[10:30, 10:30].all() and out[45:47, 45:47].sum() == 0


def test_retry_targets_the_worst_warning_not_the_first_in_the_list():
    from app.core.engines.replacement import _by_severity

    rep = validate_v2({"identity": 0.73, "person_found": True, "background": 0.0031, "tattoo_residual": 0.114,
                       "persona_instances": 1, "original_sim": 0.02})
    order = _by_severity(rep, rep.warnings())
    assert order.index("tattoo") < order.index("background")


def test_tone_match_fixes_small_patches_but_leaves_a_whole_arm_alone():
    """Mancha pequena no meio da pele: a cor vai para a da pele em volta. Zona enorme (sem pele limpa perto):
    fica como o modelo fez (no teste real o braco inteiro ficou avermelhado quando era corrigido)."""
    from app.core.engines.skin import tone_match

    base = np.full((80, 80, 3), (200, 160, 140), np.uint8)
    gen = base.copy()
    gen[30:40, 30:40] = (194, 154, 134)  # remendo um pouco mais escuro que a pele em volta
    small = np.zeros((80, 80), np.float32)
    small[28:42, 28:42] = 1
    known = 1 - small
    out = tone_match(gen, base, small, known, radius=3)
    assert np.abs(out[33:37, 33:37].astype(int) - base[33:37, 33:37].astype(int)).mean() < 3
    far = gen.copy()
    far[30:40, 30:40] = (170, 130, 110)  # diferenca grande: so ate o limite (nunca "repinta")
    moved = tone_match(far, base, small, known, radius=3).astype(int)[35, 35] - far[35, 35].astype(int)
    assert (moved <= 8).all() and (moved >= 6).all()
    big = np.ones((80, 80), np.float32)  # tudo e zona: nao ha com o que comparar
    assert (tone_match(gen, base, big, np.zeros((80, 80), np.float32), radius=3) == gen).all()


def test_markings_grow_into_ink_touching_the_top_but_not_into_the_black_top():
    """Teste de 2026-10-07: o pedaco da tatuagem encostado no top ficava fora da mascara."""
    from app.core.engines.markings import complete_markings

    img = np.full((60, 60, 3), (200, 150, 125), np.uint8)  # pele saturada
    img[:, :20] = (30, 33, 44)  # top preto (cinza azulado)
    img[20:40, 20:34] = (107, 96, 88)  # tinta cinza-quente encostada no top
    seeds = np.zeros((60, 60), np.float32)
    seeds[24:36, 30:34] = 1  # o detector so pegou a parte longe do top
    ref = np.full((60, 60, 3), (200, 150, 125), np.uint8)
    out = complete_markings(img, seeds, ref, np.ones((60, 60), np.float32))
    assert out[22:38, 21:30].all()  # completou ate o top
    assert out[:, :20].sum() == 0  # o top nao entra
    assert out[45:55, 40:55].sum() == 0  # pele limpa nao entra


def test_hair_against_background_is_not_a_seam():
    """Varanda: cabelo loiro -> escuro contra o ceu e mudanca legitima, nao emenda."""
    orig = np.full((80, 80, 3), (170, 190, 220), np.uint8)  # ceu
    orig[20:60, 20:60] = (220, 200, 150)  # cabelo loiro
    final = orig.copy()
    final[20:60, 20:60] = (60, 45, 35)  # cabelo da Luna
    reg = square(80, 80, 20, 60, 20, 60)
    person = reg.copy()
    bg = (person < 0.5).astype(np.float32)
    from app.core.persona_replacement.segmentation import dilate
    assert seam_excess(orig, final, reg) > 10
    assert seam_excess(orig, final, reg, ignore=dilate(bg, 3)) is None or seam_excess(orig, final, reg, ignore=dilate(bg, 3)) < 1


def test_light_jewelry_is_filled_not_kept():
    """Varanda: a pulseira clara virou um bloco branco - joia removida vai inteira para o preenchimento."""
    from app.core.engines.skin import structure_preserving_fill

    img = np.full((60, 60, 3), (190, 140, 115), np.uint8)
    img[25:35, 10:50] = (235, 235, 240)  # pulseira prateada
    zone = np.zeros((60, 60), np.float32)
    zone[23:37, 8:52] = 1
    out, solid = structure_preserving_fill(img, zone, 1 - zone, line_radius=3, force_solid=zone)
    assert np.abs(out[28:32, 15:45].astype(int) - np.array([190, 140, 115])).mean() < 12


def test_grain_is_never_heavy():
    from app.core.engines.integration import integrate

    rng = np.random.default_rng(0)
    orig = np.clip(150 + rng.normal(0, 12, (80, 80, 3)), 0, 255).astype(np.uint8)  # pele muito granulada ao sol
    cur = orig.copy()
    cur[20:60, 20:60] = 150  # regiao gerada lisa
    reg = square(80, 80, 20, 60, 20, 60)
    _, rel = integrate(orig, cur, reg, body_skin=1 - reg, seed=1)
    assert rel.get("grao_adicionado", 0) <= 2.5


def test_arm_edge_against_wood_is_not_a_tattoo():
    """Foto da porta (2026-10-07): sem tatuagem, mas a borda do braco contra a madeira virou 'tinta'."""
    from app.core.engines.markings import keep_inked_regions

    img = np.full((60, 60, 3), (205, 160, 135), np.uint8)  # pele
    img[:, :12] = (150, 85, 50)  # madeira marrom saturada colada no braco
    img[30:40, 35:45] = (95, 90, 88)  # tatuagem cinza de verdade
    ref = np.full((60, 60, 3), (205, 160, 135), np.uint8)
    marks = np.zeros((60, 60), np.float32)
    marks[:, 8:16] = 1  # falso positivo na borda
    marks[28:42, 33:47] = 1  # tatuagem (com margem)
    kept = keep_inked_regions(marks, img, ref)
    assert kept[:, 8:16].sum() == 0 and kept[30:40, 35:45].all()


def test_new_face_features_are_not_blocks():
    """Espelho (2026-10-07): olhos/boca/fios novos contaram como 'blocos' (15,7) numa troca boa."""
    orig, reg = scene(), square()
    final = orig.copy()
    final[40:42, 35:55] = (40, 30, 30)  # "sobrancelha" nova, reta
    final[60:62, 38:52] = (150, 60, 70)  # "boca" nova
    face = square(120, 120, 34, 66, 32, 58)
    assert straight_edges(orig, final, reg) > 0
    assert straight_edges(orig, final, reg, ignore=face) == 0


async def test_slippers_labeled_bracelet_and_double_labeled_box_are_not_removed():
    from app.core.persona_replacement.contracts import RawSegments
    from tests.test_persona_transfer import wide_tattoo_photo

    class Seg2:
        async def segment(self, image, sheet):
            _, person, hair, clothes = wide_tattoo_photo()
            return RawSegments(person, hair, [(60.0, 220.0, 70.0, 238.0), (100.0, 100.0, 110.0, 120.0), (100.0, 100.0, 110.0, 120.0)],
                               clothes=clothes, protect_labels=["bracelet", "earrings", "watch"])

    faces = {"foto": 0.1, "identity": 0.8, "face_refine": 0.8, "tattoo": 0.8, "integrated": 0.8, "final": 0.8}
    eng, ad, store = engine(faces)
    store.images["foto.png"][220:238, 60:70] = (120, 30, 40)  # "chinelo" (no pe, longe dos pulsos do esqueleto)
    eng.segmenter = Seg2()
    out = await eng.run(req(advanced={"max_retries": 0}))
    pols = [(b["label"], b["policy"]) for b in out.telemetry.attributes["boxes"]]
    assert ("bracelet", "IGNORE") in pols  # fora da pele
    assert ("earrings", "PRESERVE") in pols and ("watch", "PRESERVE") in pols  # mesma caixa: manter vence


async def test_face_lock_with_the_master_is_kept_only_when_identity_rises():
    """2026-10-07: tres trocas pareciam tres mulheres. O Face Lock (cabeca da master, igual a geracao V1) entra
    depois do rosto; so a regiao da identidade volta para a foto e so fica se a identidade subir."""
    from app.providers.base import ProviderImage, StageOutput

    class FakeLock:
        def __init__(self, store):
            self.store, self.calls = store, []

        async def lock_face(self, image, master, seed):
            self.calls.append((image.locator, master.reference_id))
            px = self.store.images[image.locator].copy()
            px = np.clip(px.astype(int) + 4, 0, 255).astype(np.uint8)  # muda a imagem INTEIRA, de leve (so a identidade volta)
            key = await self.store.save(px, "face_lock_raw")
            return StageOutput(image=ProviderImage("comfyui", key, "", px.shape[1], px.shape[0]), stage="face_lock",
                               adapter="fake", seconds=20.0, seed=seed, effective_parameters={"workflow": "qwen-bfs-head-swap"})

    for lock_sim, kept in ((0.9, True), (0.5, False)):
        faces = {"foto": 0.1, "identity": 0.7, "face_refine": 0.7, "face_lock": lock_sim, "tattoo": 0.8,
                 "integrated": 0.8, "final": 0.8}
        eng, ad, store = engine(faces)
        eng.face_lock = FakeLock(store)
        out = await eng.run(req(advanced={"max_retries": 0}, keep_intermediates=True))
        fl = next(p for p in out.telemetry.passes if p["pass"] == "face_lock")
        assert fl["accepted"] is kept and eng.face_lock.calls[0][1] == "m"
        orig = store.images["foto.png"]
        assert (out.pixels[0:4, 0:4] == orig[0:4, 0:4]).all()  # fora da identidade: a foto
