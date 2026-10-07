"""Roda a matriz do benchmark num ComfyUI (o do pod, por tunel SSH; ou o falso, no ensaio).

  - agrupado por modelo (todas as execucoes de uma variante antes da proxima: nada de trocar 20 GB a
    cada imagem); cada variante so comeca quando os arquivos DELA chegaram (marcador no pod), sem
    esperar as outras;
  - baixa cada saida assim que fica pronta (sincroniza continuamente) -> saidas/<id>.png;
  - mede o tempo de cada execucao (historico do ComfyUI) e registra quedas;
  - execucao que ja tem saida e pulada (se cair no meio, rodar de novo so faz o que faltou).

  ensaio:  python rodar_sessao.py --bench C:\\Users\\mauri\\lv\\bench --url http://127.0.0.1:8191
  pod:     python rodar_sessao.py --bench ... --url https://ID-8189.proxy.runpod.net --status https://ID-8188.proxy.runpod.net
           (no fim, ou em qualquer erro/Ctrl+C, manda /fim: o pod desliga sozinho)
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


UA = {"User-Agent": "Mozilla/5.0 luna-bench"}  # o proxy da RunPod (Cloudflare) recusa o user-agent padrao do Python (403)


def http(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_json(base, path, timeout=30):
    return json.loads(http(base + path, timeout=timeout))


def post_json(base, path, obj):
    try:
        return json.loads(http(base + path, json.dumps(obj).encode(), {"Content-Type": "application/json"}))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read() or b"{}")


def upload(base, arquivo):
    lim = uuid.uuid4().hex
    dados = Path(arquivo).read_bytes()
    corpo = (f'--{lim}\r\nContent-Disposition: form-data; name="image"; filename="{Path(arquivo).name}"\r\n'
             f"Content-Type: image/png\r\n\r\n").encode() + dados + b"\r\n"
    corpo += f'--{lim}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n--{lim}--\r\n'.encode()
    r = json.loads(http(base + "/upload/image", corpo, {"Content-Type": f"multipart/form-data; boundary={lim}"}, 180))
    return r["name"]


def vivo(base):
    try:
        get_json(base, "/system_stats", timeout=8)
        return True
    except Exception:
        return False


def status(url):
    try:
        return get_json(url, "/status", timeout=20)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--status", help="painel do pod (marcadores de download, logs, /fim); vazio = ensaio")
    ap.add_argument("--variantes", default="", help="subconjunto, ex. B,G (padrao: todas, na ordem do plano)")
    ap.add_argument("--espera-variante-min", type=float, default=40, help="tempo maximo esperando os arquivos de uma variante")
    ap.add_argument("--teto-min", type=float, default=70, help="tempo maximo desta rodada")
    a = ap.parse_args()
    b = Path(a.bench)
    base = a.url.rstrip("/")
    plano = json.load(open(b / "plano.json", encoding="utf-8"))
    execs = json.load(open(b / "execucoes.json", encoding="utf-8"))
    (b / "saidas").mkdir(exist_ok=True)
    tempos_p = b / "tempos.json"
    tempos = json.load(open(tempos_p, encoding="utf-8")) if tempos_p.exists() else {}
    ordem = [v for v in (a.variantes.split(",") if a.variantes else plano["ordem"]) if v]
    inicio = time.time()
    enviados = set()

    def salvar():
        json.dump(tempos, open(tempos_p, "w", encoding="utf-8"), indent=1)

    try:
        rodar(a, b, base, execs, ordem, tempos, salvar, inicio, enviados)
    finally:
        if a.status:
            st = a.status.rstrip("/")
            for nome in ("recursos.csv", "quedas.log", "sessao.log", "comfy.log"):
                try:
                    (b / f"pod_{nome}").write_bytes(http(f"{st}/log/{nome}", timeout=60))
                except Exception:
                    pass
            try:
                http(f"{st}/fim", b"{}", {"Content-Type": "application/json"}, 30)
                print("pedi o desligamento do pod (/fim)")
            except Exception as exc:
                print(f"NAO consegui pedir /fim ({exc}) - o pod desliga pelo teto")
        print(f"rodada: {(time.time() - inicio) / 60:.1f} min")


def rodar(a, b, base, execs, ordem, tempos, salvar, inicio, enviados):
    for v in ordem:
        pend = [e for e in execs if e["variante"] == v and not (b / "saidas" / f"{e['id']}.png").exists()]
        if not pend:
            continue
        # espera os arquivos DESTA variante (o pod escreve /root/bench/pronto_<V> quando baixou)
        t_esp = time.time()
        while a.status:
            st = status(a.status.rstrip("/")) or {}
            if v in st.get("prontos", []):
                break
            falha = st.get("falhas", {}).get(v) or st.get("falhas", {}).get("DISCO")
            if falha:
                print(f"[{v}] download falhou: {falha} - variante eliminada")
                for e in pend:
                    tempos[e["id"]] = {"queda": f"download falhou: {falha}"}
                salvar()
                pend = []
                break
            if time.time() - t_esp > a.espera_variante_min * 60 or time.time() - inicio > a.teto_min * 60:
                print(f"[{v}] arquivos nao chegaram a tempo - pulada")
                pend = []
                break
            time.sleep(10)
        if not pend:
            continue
        print(f"[{v}] {len(pend)} execucoes (esperou {time.time() - t_esp:.0f}s pelos arquivos)")
        for arq in sorted({f for e in pend for f in e["arquivos"]}):
            if arq not in enviados:
                upload(base, b / "entradas" / arq)
                enviados.add(arq)
        filas = {}
        for e in pend:
            r = post_json(base, "/prompt", {"prompt": e["grafo"], "client_id": "bench"})
            if r.get("node_errors") or "prompt_id" not in r:
                tempos[e["id"]] = {"queda": f"workflow recusado: {json.dumps(r)[:300]}"}
                continue
            filas[r["prompt_id"]] = e
        t_var = time.time()
        while filas:
            if time.time() - inicio > a.teto_min * 60:
                print("teto de tempo da rodada - parando")
                break
            if not vivo(base):
                print(f"[{v}] ComfyUI nao responde (queda?) - esperando voltar")
                t_q = time.time()
                while not vivo(base) and time.time() - t_q < 300:
                    time.sleep(10)
                for pid, e in list(filas.items()):
                    tempos.setdefault(e["id"], {})["queda"] = "ComfyUI caiu durante a execucao"
                if not vivo(base):
                    break
                # reenvia o que estava na fila
                for pid, e in list(filas.items()):
                    del filas[pid]
                    r = post_json(base, "/prompt", {"prompt": e["grafo"], "client_id": "bench"})
                    if "prompt_id" in r:
                        filas[r["prompt_id"]] = e
                continue
            for pid, e in list(filas.items()):
                try:
                    h = get_json(base, f"/history/{pid}")
                except Exception:
                    continue
                if pid not in h:
                    continue
                st = h[pid].get("status", {})
                msgs = {m[0]: m[1] for m in st.get("messages", []) if isinstance(m, list) and len(m) == 2}
                t = None
                if "execution_start" in msgs and ("execution_success" in msgs or "execution_error" in msgs):
                    fim = msgs.get("execution_success", msgs.get("execution_error"))
                    t = round((fim["timestamp"] - msgs["execution_start"]["timestamp"]) / 1000, 1)
                if st.get("status_str") == "error":
                    tempos[e["id"]] = {"queda": f"erro no ComfyUI: {json.dumps(msgs.get('execution_error', {}))[:300]}", "segundos": t}
                    del filas[pid]
                    continue
                imgs = [i for o in h[pid].get("outputs", {}).values() for i in o.get("images", [])]
                if not imgs:
                    if st.get("completed"):
                        tempos[e["id"]] = {"queda": "terminou sem imagem", "segundos": t}
                        del filas[pid]
                    continue
                q = urllib.parse.urlencode({"filename": imgs[0]["filename"], "subfolder": imgs[0].get("subfolder", ""),
                                            "type": imgs[0].get("type", "output")})
                (b / "saidas" / f"{e['id']}.png").write_bytes(http(f"{base}/view?{q}", timeout=180))
                tempos[e["id"]] = {k: val for k, val in {**tempos.get(e["id"], {}), "segundos": t}.items()}
                print(f"  {e['id']} pronta ({t}s)")
                del filas[pid]
                salvar()
            time.sleep(2)
        print(f"[{v}] terminou em {time.time() - t_var:.0f}s")
        salvar()


if __name__ == "__main__":
    main()
