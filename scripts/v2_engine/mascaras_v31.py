"""SO MASCARAS (nada e gerado). Usa o MESMO codigo da engine V3.1 (_scene com a config persona_replacement_v3_1.json)
para mostrar o que a V3.1 vai usar: pessoa/fundo, roupa (aceita pelo material), pele validada, cabelo, maos/bracos,
acessorios (camadas) e regioes ambiguas, com coberturas, vazamentos entre classes e areas sem classificacao.

  python mascaras_v31.py plano.json saida_mascaras
"""
import asyncio
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("V2_ROOT", "/workspace/v2test"))
sys.path.insert(0, str(ROOT / "pylib"))
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from app.clients.comfyui_client import ComfyUIClient  # noqa: E402
from app.core.engines.replacement import ReplacementEngine, ReplacementRequest  # noqa: E402
from app.core.engines.replacement_v3 import PersonaReplacementV3  # noqa: E402
from app.core.engines.scene_analysis import semantic_masks  # noqa: E402
from app.core.engines.v31_integration import ambiguity, validated_skin  # noqa: E402
from app.core.persona.sheet import PersonaSheetRepository  # noqa: E402
from app.core.persona_replacement.segmentation import dilate, skin_pixels  # noqa: E402
from app.providers.base import ReferenceImage  # noqa: E402
from app.providers.comfyui.replacement import ComfyImageStore, ComfySegmenter  # noqa: E402
from app.providers.comfyui.session import ComfySession  # noqa: E402
from app.validation_backends.comfyui import ComfyImageAnalyzer  # noqa: E402
from app.validation_backends.reference import ComfyReferenceReader  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402

COLORS = {"pessoa": (40, 90, 255), "roupa": (255, 150, 0), "pele": (255, 90, 120), "cabelo": (170, 0, 220),
          "maos_bracos": (0, 220, 200), "acessorios": (255, 255, 0), "ambigua": (255, 0, 0)}


def tint(px, m, col, a=0.55):
    out = px.astype(np.float32).copy()
    w = (m > 0.5)[..., None] * a
    return (out * (1 - w) + np.array(col, np.float32) * w).clip(0, 255).astype(np.uint8)


def frac(a, b):
    return round(float(((a > 0.5) & (b > 0.5)).sum()) / max(1.0, float((b > 0.5).sum())), 4)


async def main() -> int:
    plano = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.mkdir(exist_ok=True)
    cfg = ReplacementEngine.load_config(ROOT / "config" / "persona_replacement_v3_1.json")
    client = ComfyUIClient(base_url="http://127.0.0.1:8188", connect_timeout=90.0, generation_timeout=600.0)
    session = ComfySession(client, WorkflowManager(ROOT / "workflows"))
    store = ComfyImageStore(client)
    eng = PersonaReplacementV3(reader=ComfyReferenceReader(client), segmenter=ComfySegmenter(session, store),
                               analyzer=ComfyImageAnalyzer(client), store=store, adapter=None, config=cfg)
    sheet = PersonaSheetRepository(ROOT / "personas_run").get("luna")
    m0 = sheet.master("master_face")
    master = ReferenceImage(m0.reference_id, m0.file, sheet.read_master("master_face"), m0.sha256)
    rep = {}
    for ex in plano["execucoes"]:
        img = Image.open(ROOT / ex["foto"]).convert("RGB")
        img.thumbnail((1600, 1600), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        loc = await client.upload_image(f"v2in_{Path(ex['foto']).stem[:30]}.png", buf.getvalue())
        req = ReplacementRequest(image=loc, persona_id="luna", master=master, persona_sheet=sheet.data,
                                 replacement_version="v3.1")
        attrs = req.attributes()
        plan = eng._configured(req.plan(attrs), req)
        sc = await eng._scene(loc, master, plan, attrs)
        h, w = sc.original.shape[:2]
        sc.hands = await eng._source_hands(loc, w, h, master)
        px = sc.original
        mk = sc.masks
        person = (mk.person > 0.5).astype(np.float32)
        clothes = (mk.clothing > 0.5).astype(np.float32) if sc.clothes_ok else np.zeros_like(person)
        hair = (mk.hair > 0.5).astype(np.float32)
        acc = np.zeros_like(person) if sc.accessory is None else (sc.accessory > 0.5).astype(np.float32)
        skin = validated_skin(person, clothes if sc.clothes_ok else None, hair, acc)
        sem = semantic_masks(sc, eng._hand_mask(sc))
        hands = sem.get("hand_mask", np.zeros_like(person))
        arms = sem.get("arm_mask", np.zeros_like(person))
        limbs = np.maximum(hands, arms) * person
        color_skin = (skin_pixels(px) > 0.5).astype(np.float32)
        fabric_like_skin = clothes * color_skin  # tecido com cor de pele: NAO recebe correcao de pele
        unclassified = person * (1 - np.clip(skin + clothes + hair + acc, 0, 1))  # bordas/transicoes sem classe
        amb = np.maximum(fabric_like_skin, unclassified)
        # numeros
        stats = {
            "roupa_aceita": sc.clothes_ok, "roupa_confianca": sc.notes.get("clothes_trust"),
            "cabelo": sc.notes.get("hair"),
            "cobertura_da_pessoa": {k: frac(v, person) for k, v in (("roupa", clothes), ("pele", skin), ("cabelo", hair),
                                                                     ("acessorios", acc), ("sem_classe", unclassified))},
            "vazamentos": {"pele_em_roupa_px": int(((skin > 0.5) & (clothes > 0.5)).sum()),
                           "pele_em_cabelo_px": int(((skin > 0.5) & (hair > 0.5)).sum()),
                           "pele_em_acessorio_px": int(((skin > 0.5) & (acc > 0.5)).sum()),
                           "pele_fora_da_pessoa_px": int(((skin > 0.5) & (person < 0.5)).sum()),
                           "roupa_em_cabelo_px": int(((clothes > 0.5) & (hair > 0.5)).sum()),
                           "bracos_cobertos_por_cabelo": frac(hair, arms) if arms.any() else None,
                           "bracos_dentro_da_pessoa": frac(person, arms) if arms.any() else None},
            "ambiguas": {"tecido_cor_de_pele_px": int(fabric_like_skin.sum()),
                         "tecido_cor_de_pele_frac_da_roupa": frac(color_skin, clothes) if clothes.any() else None,
                         "sem_classe_px": int(unclassified.sum()), "acao": "excluidas da correcao de pele"},
            "acessorios": [{"label": b["label"], "policy": b["policy"], "box": b["box"], "note": b.get("note", "")}
                           for b in sc.box_policy],
            "camadas": [x.to_dict() for x in sc.layers],
            "legenda": getattr(sc.sheet, "caption", ""),
            "dwpose": [[round(float(v), 1) for v in k] for k in (sc.base_pose or [])],  # para conferir bracos/maos
        }
        # imagens por classe + sobreposicao
        panels = [("original", px), ("pessoa (azul) x fundo", tint(px, person, COLORS["pessoa"])),
                  ("roupa", tint(px, clothes, COLORS["roupa"])), ("pele validada", tint(px, skin, COLORS["pele"])),
                  ("cabelo", tint(px, hair, COLORS["cabelo"])), ("maos e bracos", tint(px, limbs, COLORS["maos_bracos"])),
                  ("acessorios", tint(px, acc, COLORS["acessorios"], 0.8)),
                  ("ambiguas (vermelho)", tint(px, amb, COLORS["ambigua"], 0.75))]
        allm = px
        for nm, mm in (("pele", skin), ("roupa", clothes), ("cabelo", hair), ("acessorios", acc), ("ambigua", amb)):
            allm = tint(allm, mm, COLORS[nm], 0.45)
        panels.append(("todas", allm))
        H = 560
        tiles = [(t, Image.fromarray(p).resize((int(w * H / h), H))) for t, p in panels]
        grid = Image.new("RGB", (sum(t.width + 6 for _, t in tiles), H + 26), "white")
        dr = ImageDraw.Draw(grid)
        x = 0
        for t, im in tiles:
            grid.paste(im, (x, 26))
            dr.text((x + 4, 6), t, fill="black")
            x += im.width + 6
        # caixas dos acessorios no painel 'acessorios'
        sx = H / h
        ax = sum(tl.width + 6 for _, tl in tiles[:6])
        for b in sc.box_policy:
            x1, y1, x2, y2 = (v * sx for v in b["box"])
            dr.rectangle([ax + x1, 26 + y1, ax + x2, 26 + y2], outline=(255, 0, 0) if b["policy"] != "PRESERVE" else (0, 160, 0),
                         width=2)
            dr.text((ax + x1 + 2, 26 + y1 + 2), f"{b['label']}:{b['policy'][:4]}", fill=(0, 0, 0))
        grid.save(out / f"{ex['id']}__mascaras.jpg", quality=90)
        for t, p in panels:
            Image.fromarray(p).save(out / f"{ex['id']}__{t.split(' ')[0]}.png")
        rep[ex["id"]] = stats
        print("MASCARAS", ex["id"], json.dumps({k: stats[k] for k in ("roupa_aceita", "cobertura_da_pessoa", "vazamentos")}),
              flush=True)
    (out / "mascaras.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
