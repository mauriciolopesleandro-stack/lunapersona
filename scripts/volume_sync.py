"""Mantem os dois network volumes do estudio iguais (US-MO-2 e EU-RO-1).

Roda DENTRO do pod, que tem o proprio volume montado em /workspace e fala
com o outro volume ("peer") pela API S3 da RunPod - o outro volume nao
precisa ter pod ligado. Chamado por scripts/pod_autostart.sh no boot, a
cada poucos minutos, e pelo backend antes de desligar por inatividade.

  python scripts/volume_sync.py small   # config + personas (segundos)
  python scripts/volume_sync.py big     # modelos (so transfere o que mudou)
  python scripts/volume_sync.py all
  python scripts/volume_sync.py small --prefer-remote   # forca o outro lado a vencer

Regra de sincronizacao (por arquivo, dos dois lados):
  - so de um lado e nunca sincronizado antes -> copia para o outro;
  - so de um lado mas ja sincronizado antes  -> foi apagado do outro lado,
    apaga deste tambem (com trava: muitas remocoes de uma vez sao puladas);
  - dos dois lados e diferente -> vence o que mudou (ou o mais novo, se
    mudou dos dois lados).
O "sincronizado antes" fica em /workspace/.luna-sync/state-<peer>.json.

Variaveis de ambiente (passadas pela Vercel ao criar o pod, ver
frontend/api/_runpod.ts): RUNPOD_S3_ACCESS_KEY, RUNPOD_S3_SECRET_KEY,
LUNA_PEER_VOLUME_ID, LUNA_PEER_DATACENTER. Sem elas, sai sem fazer nada.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path

WORKSPACE = Path(os.environ.get("LUNA_WORKSPACE", "/workspace"))
STATE_DIR = WORKSPACE / ".luna-sync"
LOCK_FILE = Path("/tmp/luna-volume-sync.lock")

# Caminhos relativos a /workspace. Ficam de fora de proposito: ambientes
# Python (cada volume monta o seu), o codigo (vem do git) e as imagens
# geradas (so valem no pod que as gerou).
GROUPS = {
    "small": [
        "lunapersona/.env",
        "lunapersona/personas",
        "runpod-slim/comfyui_args.txt",
    ],
    "big": [
        "ollama_models",
        "lunapersona/.ollama-models",
        "runpod-slim/ComfyUI/models",
        "runpod-slim/ComfyUI/user/default/workflows",
    ],
}
SKIP_NAMES = {".DS_Store", "__pycache__", ".ipynb_checkpoints"}
SKIP_SUFFIXES = (".part", ".tmp", ".partial")
# Diferenca de relogio tolerada entre o mtime local e o LastModified do S3.
MTIME_SLACK = 2.0
MAX_DELETE_FRACTION = 0.1
MAX_DELETE_FLOOR = 3


def log(msg: str) -> None:
    print(f"[volume_sync {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def s3_client(datacenter: str):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=f"https://s3api-{datacenter.lower()}.runpod.io",
        region_name=datacenter,
        aws_access_key_id=os.environ["RUNPOD_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["RUNPOD_S3_SECRET_KEY"],
        config=Config(retries={"max_attempts": 5, "mode": "standard"}, s3={"addressing_style": "path"}),
    )


def skipped(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in SKIP_NAMES for p in parts) or rel.endswith(SKIP_SUFFIXES)


def list_local(root: str) -> dict[str, tuple[int, float]]:
    base = WORKSPACE / root
    out: dict[str, tuple[int, float]] = {}
    if base.is_file():
        st = base.stat()
        out[root] = (st.st_size, st.st_mtime)
        return out
    if not base.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES]
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(WORKSPACE).as_posix()
            if skipped(rel) or path.is_symlink():
                continue
            st = path.stat()
            out[rel] = (st.st_size, st.st_mtime)
    return out


def list_remote(s3, bucket: str, root: str) -> dict[str, tuple[int, float]]:
    out: dict[str, tuple[int, float]] = {}
    paginator = s3.get_paginator("list_objects_v2")
    # Prefixo com "/" para "personas" nao pegar "personas-old" (e o proprio
    # arquivo, no caso de caminhos que sao arquivo unico, como o .env).
    for prefix in (root, root + "/"):
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/") or skipped(key):
                    continue
                if key == root or key.startswith(root + "/"):
                    out[key] = (obj["Size"], obj["LastModified"].timestamp())
        if root in out:  # era um arquivo unico
            break
    return out


def same(a, b) -> bool:
    return a is not None and b is not None and a[0] == b[0] and abs(a[1] - b[1]) < MTIME_SLACK


def transfer_config():
    from boto3.s3.transfer import TransferConfig

    # A API S3 da RunPod exige multipart acima de 500 MB.
    return TransferConfig(multipart_threshold=64 * 1024**2, multipart_chunksize=64 * 1024**2, max_concurrency=8)


def upload(s3, bucket: str, rel: str) -> tuple[int, float]:
    local = WORKSPACE / rel
    s3.upload_file(str(local), bucket, rel, Config=transfer_config())
    head = s3.head_object(Bucket=bucket, Key=rel)
    return head["ContentLength"], head["LastModified"].timestamp()


def download(s3, bucket: str, rel: str, remote: tuple[int, float]) -> tuple[int, float]:
    local = WORKSPACE / rel
    local.parent.mkdir(parents=True, exist_ok=True)
    tmp = local.with_name(local.name + ".part")
    s3.download_file(bucket, rel, str(tmp), Config=transfer_config())
    os.replace(tmp, local)
    # Mesmo mtime do lado remoto: na proxima rodada os dois lados batem.
    os.utime(local, (remote[1], remote[1]))
    st = local.stat()
    return st.st_size, st.st_mtime


def sync_root(s3, bucket: str, root: str, state: dict, prefer_remote: bool) -> dict[str, int]:
    local = list_local(root)
    remote = list_remote(s3, bucket, root)
    prev = {k: v for k, v in state.items() if k == root or k.startswith(root + "/")}
    counts = {"up": 0, "down": 0, "del_local": 0, "del_remote": 0, "skip_del": 0, "errors": 0}

    deletions: list[tuple[str, str]] = []
    for rel in sorted(set(local) | set(remote) | set(prev)):
        L, R, S = local.get(rel), remote.get(rel), prev.get(rel)
        sl = tuple(S["l"]) if S else None
        sr = tuple(S["r"]) if S else None
        local_changed = L is not None and not same(L, sl)
        remote_changed = R is not None and not same(R, sr)

        try:
            if L and R:
                if not local_changed and not remote_changed:
                    continue
                if same(L, R) or (L[0] == R[0] and S is None):
                    state[rel] = {"l": list(L), "r": list(R)}
                    continue
                if S is None:
                    # Primeiro encontro dos dois lados: data nao prova nada (um
                    # git clone da mtime "agora" a tudo) - vence o original.
                    local_wins = not prefer_remote
                else:
                    local_wins = local_changed and (not remote_changed or L[1] >= R[1])
                if local_wins:
                    r_new = upload(s3, bucket, rel)
                    state[rel] = {"l": list(L), "r": list(r_new)}
                    counts["up"] += 1
                else:
                    l_new = download(s3, bucket, rel, R)
                    state[rel] = {"l": list(l_new), "r": list(R)}
                    counts["down"] += 1
            elif L and not R:
                if S and not local_changed:
                    deletions.append(("local", rel))
                else:
                    r_new = upload(s3, bucket, rel)
                    state[rel] = {"l": list(L), "r": list(r_new)}
                    counts["up"] += 1
            elif R and not L:
                if S and not remote_changed:
                    deletions.append(("remote", rel))
                else:
                    l_new = download(s3, bucket, rel, R)
                    state[rel] = {"l": list(l_new), "r": list(R)}
                    counts["down"] += 1
            else:
                state.pop(rel, None)
        except Exception as exc:  # um arquivo com erro nao para o resto
            log(f"ERRO em {rel}: {exc}")
            counts["errors"] += 1

    total = max(len(local), len(remote))
    limit = max(MAX_DELETE_FLOOR, int(total * MAX_DELETE_FRACTION))
    if len(deletions) > limit:
        log(f"{root}: {len(deletions)} remocoes pendentes (limite {limit}) - puladas por seguranca.")
        counts["skip_del"] = len(deletions)
    else:
        for side, rel in deletions:
            try:
                if side == "local":
                    (WORKSPACE / rel).unlink(missing_ok=True)
                    counts["del_local"] += 1
                else:
                    s3.delete_object(Bucket=bucket, Key=rel)
                    counts["del_remote"] += 1
                state.pop(rel, None)
            except Exception as exc:
                log(f"ERRO ao remover {rel} ({side}): {exc}")
                counts["errors"] += 1
    return counts


SYNC_ENV_KEYS = ("RUNPOD_S3_ACCESS_KEY", "RUNPOD_S3_SECRET_KEY", "LUNA_PEER_VOLUME_ID", "LUNA_PEER_DATACENTER")
OPTIONAL_ENV_KEYS = ("LUNA_SELF_ORIGINAL",)


def load_container_env() -> None:
    """As variaveis do pod ficam no ambiente do processo 1 do container;
    rodando por SSH (ou filho de um processo que as perdeu), le de la."""
    if all(os.environ.get(k) for k in SYNC_ENV_KEYS):
        return
    try:
        raw = Path("/proc/1/environ").read_bytes()
    except OSError:
        return
    for item in raw.split(b"\0"):
        key, _, value = item.decode("utf-8", "replace").partition("=")
        if key in SYNC_ENV_KEYS + OPTIONAL_ENV_KEYS and not os.environ.get(key):
            os.environ[key] = value


def main() -> int:
    load_container_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    # Arquivo que nunca foi sincronizado e esta diferente nos dois lados: o
    # volume original (LUNA_SELF_ORIGINAL=1, o que ja tinha tudo) vence; a
    # copia perde (o que ela tem pode ter vindo so de um git clone recente).
    prefer_remote = "--prefer-remote" in sys.argv[1:] or os.environ.get("LUNA_SELF_ORIGINAL") != "1"
    group = args[0] if args else "small"
    roots = GROUPS["small"] + GROUPS["big"] if group == "all" else GROUPS.get(group)
    if roots is None:
        log(f"Grupo desconhecido: {group} (use small, big ou all)")
        return 2

    missing = [k for k in SYNC_ENV_KEYS if not os.environ.get(k)]
    if missing:
        log(f"Sincronizacao desligada (faltam: {', '.join(missing)}).")
        return 0

    peer_bucket = os.environ["LUNA_PEER_VOLUME_ID"]
    s3 = s3_client(os.environ["LUNA_PEER_DATACENTER"])

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / f"state-{peer_bucket}.json"
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # uma sincronizacao por vez
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        started = time.monotonic()
        problems = 0
        for root in roots:
            counts = sync_root(s3, peer_bucket, root, state, prefer_remote)
            state_file.write_text(json.dumps(state))
            problems += counts["errors"] + counts["skip_del"]
            changed = {k: v for k, v in counts.items() if v}
            if changed:
                log(f"{root}: {changed}")
        log(
            f"{group}: {'ok' if not problems else f'{problems} problema(s)'} em {time.monotonic() - started:.0f}s "
            f"(peer {peer_bucket} @ {os.environ['LUNA_PEER_DATACENTER']})"
        )
        if group == "all" and not problems:
            mark_ready(s3, peer_bucket)
    return 0 if not problems else 1


# A Vercel so sobe o estudio no volume-copia depois que este marcador existe
# nele (ver volumeIsReady em frontend/api/_runpod.ts): sem ele, o estudio
# subiria sem modelos, personas nem .env.
READY_MARKER = ".luna-sync/ready.json"


def mark_ready(s3, peer_bucket: str) -> None:
    body = json.dumps({"synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "peer": peer_bucket})
    local = WORKSPACE / READY_MARKER
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(body)
    s3.put_object(Bucket=peer_bucket, Key=READY_MARKER, Body=body.encode())
    log("Volumes iguais - marcador de pronto gravado nos dois lados.")


if __name__ == "__main__":
    sys.exit(main())
