"""SO MASCARAS (nada e gerado): leitura + segmentacao das fotos do plano, para conferir antes do teste da V3.1 se a
roupa (top claro, calca bege) e aceita como roupa, e quais acessorios/objetos sao detectados.

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
from PIL import Image  # noqa: E402

from app.clients.comfyui_client import ComfyUIClient  # noqa: E402
from app.core.engines.v31_integration import ambiguity, trusted_clothes  # noqa: E402
from app.core.persona.sheet import PersonaSheetRepository  # noqa: E402
from app.providers.base import ProviderImage, ReferenceImage  # noqa: E402
from app.providers.comfyui.replacement import ComfyImageStore, ComfySegmenter  # noqa: E402
from app.providers.comfyui.session import ComfySession  # noqa: E402
from app.validation_backends.reference import ComfyReferenceReader  # noqa: E402
from app.workflow_manager.manager import WorkflowManager  # noqa: E402


async def main() -> int:
    plano = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.mkdir(exist_ok=True)
    cfg = json.loads((ROOT / "config" / "persona_replacement_v3_1.json").read_text())
    protect = cfg["segmentation"]["protect"]
    client = ComfyUIClient(base_url="http://127.0.0.1:8188", connect_timeout=90.0, generation_timeout=600.0)
    session = ComfySession(client, WorkflowManager(ROOT / "workflows"))
    store = ComfyImageStore(client)
    reader, seg = ComfyReferenceReader(client), ComfySegmenter(session, store)
    sheet = PersonaSheetRepository(ROOT / "personas_run").get("luna")
    m = sheet.master("master_face")
    master = ReferenceImage(m.reference_id, m.file, sheet.read_master("master_face"), m.sha256)
    rep = {}
    for ex in plano["execucoes"]:
        img = Image.open(ROOT / ex["foto"]).convert("RGB")
        img.thumbnail((1600, 1600), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        loc = await client.upload_image(f"v2in_{Path(ex['foto']).stem[:30]}.png", buf.getvalue())
        w, h = img.size
        sh = await reader.read(ProviderImage("comfyui", loc, "", w, h), master)
        raw = await seg.segment(loc, sh, protect=protect)
        px = np.asarray(img)
        tc, info = trusted_clothes(raw.clothes, raw.person, float(cfg["segmentation"].get("clothes_min_frac", 0.04)))
        over = px.astype(np.float32).copy()
        for msk, col in ((raw.person, (0, 0, 255)), (raw.clothes, (255, 160, 0)), (raw.hair, (255, 0, 200))):
            if msk is not None:
                a = (msk > 0.5)[..., None] * 0.45
                over = over * (1 - a) + np.array(col, np.float32) * a
        o = Image.fromarray(over.clip(0, 255).astype(np.uint8))
        from PIL import ImageDraw
        dr = ImageDraw.Draw(o)
        for b, lab in zip(raw.protect_boxes, list(raw.protect_labels or []) + [""] * len(raw.protect_boxes)):
            dr.rectangle(b, outline=(255, 255, 0), width=3)
            dr.text((b[0] + 3, b[1] + 3), lab, fill=(255, 255, 0))
        o.save(out / f"{ex['id']}__mascaras.png")
        rep[ex["id"]] = {"caption": sh.caption, "clothes": info, "ambiguity": ambiguity(px, raw.clothes, raw.person),
                         "boxes": [[lab, [round(v) for v in b]] for b, lab in zip(raw.protect_boxes, raw.protect_labels or [])]}
        print("MASCARAS", ex["id"], json.dumps(rep[ex["id"]]["clothes"]), flush=True)
    (out / "mascaras.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
