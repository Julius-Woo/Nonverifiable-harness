#!/usr/bin/env python3
"""Probe Azure OpenAI / AI Foundry endpoints listed in a key file.

Usage: python3 scripts/azure_probe.py <keyfile> [--out logs/azure_probe.json]
Key file lines: "<key>\t<endpoint-url>". Keys are NEVER printed; hosts and model names are.
"""
import json, sys, time, re, argparse, concurrent.futures as cf, urllib.request, urllib.error
from urllib.parse import urlparse

CANDIDATES = ["gpt-5.5","gpt-5.4","gpt-5.4-mini","gpt-5.4-nano","gpt-5.3-codex","gpt-5.2","gpt-5.1","gpt-5","gpt-5-mini","gpt-5-nano",
              "gpt-5-chat","gpt-4.1","gpt-4.1-mini","gpt-4.1-nano","gpt-4o","gpt-4o-mini","o4-mini","o3","o3-mini",
              "DeepSeek-V4","DeepSeek-V3.2","DeepSeek-V3.1","DeepSeek-R1","Llama-4-Maverick-17B-128E-Instruct-FP8","Meta-Llama-3.3-70B-Instruct",
              "grok-4","grok-4-fast-reasoning","grok-3","Mistral-Large-3","mistral-medium-2505","claude-opus-4-1","claude-sonnet-4-5","claude-haiku-4-5",
              "Phi-4","Phi-4-reasoning","Kimi-K2","MAI-DS-R1","gpt-oss-120b","gpt-oss-20b","model-router"]

def req(url, key, method="GET", body=None, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("api-key", key); r.add_header("Authorization", "Bearer "+key)
    r.add_header("Content-Type", "application/json")
    t=time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "null"), time.time()-t
    except urllib.error.HTTPError as e:
        try: msg=json.loads(e.read().decode())
        except Exception: msg={"raw":"(unreadable)"}
        return e.code, msg, time.time()-t
    except Exception as e:
        return -1, {"error": type(e).__name__+": "+str(e)[:120]}, time.time()-t

def base_of(url):
    u=urlparse(url); return f"{u.scheme}://{u.netloc}"

def list_models(base, key):
    found=set(); notes=[]
    for path in ["/openai/v1/models", "/openai/deployments?api-version=2023-03-15-preview", "/openai/models?api-version=2024-10-21", "/models/info?api-version=2024-05-01-preview"]:
        st, js, dt = req(base+path, key)
        if st==200 and isinstance(js, dict):
            items = js.get("data") or js.get("value") or ([js] if "model_name" in js else [])
            for it in items or []:
                name = it.get("id") or it.get("model_name") or it.get("name")
                if isinstance(it.get("model"), str) and path.startswith("/openai/deployments"):
                    name = it.get("id"); notes.append(f"deployment {it.get('id')} -> model {it.get('model')}")
                if name: found.add(name)
            notes.append(f"{path.split('?')[0]}: 200, {len(items or [])} items")
        else:
            notes.append(f"{path.split('?')[0]}: {st} {str(js)[:80]}")
    return found, notes

def chat_probe(base, key, model):
    # 1) OpenAI-compatible v1 chat
    st, js, dt = req(base+"/openai/v1/chat/completions", key, "POST",
                     {"model": model, "messages":[{"role":"user","content":"Reply with exactly the word OK."}], "max_completion_tokens": 64})
    if st==400 and "max_completion_tokens" in json.dumps(js):
        st, js, dt = req(base+"/openai/v1/chat/completions", key, "POST",
                         {"model": model, "messages":[{"role":"user","content":"Reply with exactly the word OK."}], "max_tokens": 64})
    if st==200:
        txt = (js.get("choices") or [{}])[0].get("message",{}).get("content")
        return {"api":"v1/chat","status":200,"text":(txt or "")[:20],"usage":js.get("usage"),"latency_s":round(dt,2),"served_model":js.get("model")}
    # 2) Responses API
    st2, js2, dt2 = req(base+"/openai/v1/responses", key, "POST", {"model": model, "input":"Reply with exactly the word OK.", "max_output_tokens": 64})
    if st2==200:
        txt=""
        for o in js2.get("output",[]):
            for c in o.get("content",[]) or []:
                if c.get("type")=="output_text": txt+=c.get("text","")
        return {"api":"v1/responses","status":200,"text":txt[:20],"usage":js2.get("usage"),"latency_s":round(dt2,2),"served_model":js2.get("model")}
    def emsg(j):
        e = j.get("error") if isinstance(j, dict) else None
        if isinstance(e, dict): return str(e.get("message") or e.get("code") or e)[:100]
        return str(e if e is not None else j)[:100]
    return {"api":"none","status":st,"status_responses":st2,"error":emsg(js),"error_responses":emsg(js2)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("keyfile"); ap.add_argument("--out", default="logs/azure_probe.json"); ap.add_argument("--all-candidates", action="store_true")
    a=ap.parse_args()
    pairs=[]
    for line in open(a.keyfile, encoding="utf-8"):
        m=re.match(r"^\s*(\S{20,})\s+(https?://\S+)", line)
        if m: pairs.append((m.group(1), m.group(2)))
    print(f"{len(pairs)} key/endpoint pairs")
    results=[]
    def work(i, key, url):
        base=base_of(url); found, notes = list_models(base, key)
        names = sorted(found) if found else []
        todo = list(dict.fromkeys(names + (CANDIDATES if (a.all_candidates or not found) else [])))
        probes={}
        with cf.ThreadPoolExecutor(8) as ex:
            for name, res in zip(todo, ex.map(lambda n: chat_probe(base, key, n), todo)):
                probes[name]=res
        return {"idx":i,"host":urlparse(base).netloc,"listed":names,"list_notes":notes,"probes":probes}
    with cf.ThreadPoolExecutor(len(pairs) or 1) as ex:
        results=list(ex.map(lambda p: work(p[0],p[1][0],p[1][1]), enumerate(pairs)))
    json.dump(results, open(a.out,"w"), indent=1)
    for r in results:
        ok=[(n,p) for n,p in r["probes"].items() if p.get("status")==200]
        print(f"\n[{r['idx']}] {r['host']}  listed={len(r['listed'])}  working={len(ok)}")
        for n in r["list_notes"]: print("    note:", n)
        for n,p in ok: print(f"    OK  {n:40s} via {p['api']:12s} {p['latency_s']:>6}s served={p.get('served_model')}")
        bad=[(n,p) for n,p in r["probes"].items() if p.get("status")!=200]
        for n,p in bad[:6]: print(f"    --  {n:40s} {p.get('status')}/{p.get('status_responses')} {p.get('error','')[:70]}")
        if len(bad)>6: print(f"    -- ... {len(bad)-6} more failures")
if __name__=="__main__": main()
