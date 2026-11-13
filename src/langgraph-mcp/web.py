import asyncio
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore

from app import agent, rag_agent, combined_agent

BUSY = BoundedSemaphore(1)

HTML = r'''<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LangGraph · MCP · RAG · OGX</title>
<style>
body{font:16px system-ui,sans-serif;background:#f3f5f8;color:#172132;margin:0}
main{max-width:900px;margin:40px auto;padding:0 20px}
section,form{background:white;border:1px solid #dce2e9;border-radius:12px;padding:22px;margin:20px 0}
label{display:block;font-weight:600;margin-bottom:8px}
select,textarea{box-sizing:border-box;width:100%;padding:12px;font:inherit;border:1px solid #9caabd;border-radius:6px}
textarea{min-height:100px}
button{padding:12px 20px;margin-top:12px;background:#193d70;color:white;border:0;border-radius:6px;cursor:pointer}
button:disabled{opacity:.5;cursor:wait}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:14px}
#answer{white-space:pre-wrap;line-height:1.6}
#status{margin-left:12px}summary{cursor:pointer}
.source{border-left:3px solid #193d70;padding:4px 16px;margin:18px 0}
.source p{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}
.muted{font-size:14px;color:#526174}
[hidden]{display:none!important}
</style>
</head>
<body><main>
<h1>LangGraph ile cluster'a ve dokümana sor</h1>
<p>Her soru bağımsız işlenir. Yapmak istediğin sorguyu seç.</p>
<form id="form">
<label for="mode">Sorgu türü</label>
<select id="mode">
<option value="pods">Pod sorgula · MCP</option>
<option value="rag">Dokümana sor · RAG</option>
<option value="combined">Birlikte sor · MCP + RAG</option>
</select>
<p id="description">maas-demo podlarının güncel durumunu MCP üzerinden sorgular.</p>
<label for="prompt">Sorun</label>
<textarea id="prompt" maxlength="2000" required>maas-demo podlarının durumunu Türkçe ve kısaca özetle.</textarea>
<button id="send">Gönder</button><span id="status" role="status" aria-live="polite"></span>
</form>
<section id="result" hidden>
<div id="pod-section"><h2>MCP pod özeti</h2><pre id="summary"></pre></div>
<h2 id="answer-title">Model yorumu</h2><div id="answer"></div>
<p id="usage" class="muted"></p>
<div id="sources-section" hidden>
<h2>Bulunan kaynaklar</h2>
<p class="muted">Modelin kullandığı metin parçaları aşağıdadır. Arama skoru doğruluk yüzdesi değildir; yanıtı kaynakla karşılaştır.</p>
<div id="sources"></div>
<p id="context-note" class="muted" hidden>Bağlam sınırı nedeniyle bulunan metinlerin bir bölümü kullanıldı.</p>
</div>
<p id="pod-context-note" class="muted" hidden>Model yalnızca sayısal pod özetini gördü; tam pod verisi araç sonuçlarında bulunur.</p>
<details id="trace-section"><summary>MCP araç çağrıları ve sonuçları</summary><pre id="trace"></pre></details>
</section>
<script>
const form=document.getElementById('form');
const button=document.getElementById('send');
const status=document.getElementById('status');
const mode=document.getElementById('mode');
const prompt=document.getElementById('prompt');
const defaults={pods:'maas-demo podlarının durumunu Türkçe ve kısaca özetle.',rag:'Podları hangi namespace içinde sorgulayabiliyoruz?',combined:'Dokümana göre pod sorguları hangi servis üzerinden yapılıyor ve maas-demo podlarının şu anki durumu nasıl?'};
mode.addEventListener('change',()=>{
  if(!prompt.value.trim()||Object.values(defaults).includes(prompt.value))prompt.value=defaults[mode.value];
  document.getElementById('description').textContent=mode.value==='combined'
    ? 'Doküman bilgisini ve maas-demo canlı pod durumunu tek yanıtta birleştirir.'
    : mode.value==='rag'
    ? 'demo-docs içindeki dokümanları arar; bulunan metinleri kullanarak cevap üretir.'
    : 'maas-demo podlarının güncel durumunu MCP üzerinden sorgular.';
  document.getElementById('result').hidden=true;
  status.textContent='';
});
form.addEventListener('submit',async event=>{
  event.preventDefault();
  button.disabled=mode.disabled=prompt.disabled=true;
  status.textContent='Yanıt hazırlanıyor…';
  document.getElementById('result').hidden=true;
  try{
    const response=await fetch('/api/chat',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({prompt:prompt.value,mode:mode.value})
    });
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'İstek başarısız.');
    const rag=data.mode==='rag';
    const combined=data.mode==='combined';
    document.getElementById('pod-section').hidden=rag;
    document.getElementById('trace-section').hidden=rag;
    document.getElementById('sources-section').hidden=!(rag||combined);
    document.getElementById('answer-title').textContent=combined?'Doküman ve canlı pod yorumu':rag?'Dokümana dayalı yanıt':'Model yorumu';
    document.getElementById('answer').textContent=data.answer||'Model görünür bir yanıt üretmedi.';
    const fields=[
      ['total_pods','Toplam pod'],['running_pods','Çalışan pod'],
      ['running_ready_pods','Çalışan ve tüm containerları hazır pod'],
      ['succeeded_pods','Tamamlanan pod'],['pending_pods','Pending pod'],
      ['failed_pods','Failed pod'],['other_phase_pods','Diğer phase'],
      ['pods_with_waiting_containers','Bekleyen container içeren pod'],
      ['pods_with_restarts','Geçmiş restart kaydı olan pod'],['total_restarts','Toplam geçmiş restart']
    ];
    document.getElementById('summary').textContent=data.pod_summary
      ? fields.map(([key,label])=>label+': '+data.pod_summary[key]).join('\n'):'MCP özeti alınamadı.';
    document.getElementById('trace').textContent=JSON.stringify(data.tool_calls||[],null,2);
    const sources=document.getElementById('sources');
    sources.replaceChildren();
    for(const [index,source] of (data.sources||[]).entries()){
      const box=document.createElement('div');box.className='source';
      const title=document.createElement('h3');title.textContent='['+(index+1)+'] '+source.filename;
      const text=document.createElement('p');text.textContent=source.text;
      box.append(title,text);
      if(typeof source.score==='number'){
        const score=document.createElement('small');
        score.textContent='Arama sıralama skoru: '+source.score.toFixed(3);box.append(score);
      }
      sources.append(box);
    }
    if((rag||combined)&&!sources.children.length)sources.textContent='Arama sonucu boş döndü.';
    document.getElementById('context-note').hidden=!data.context_truncated;
    document.getElementById('pod-context-note').hidden=!data.pod_context_truncated;
    document.getElementById('usage').textContent=
      'Model çağrısı: '+data.model_calls+' · Toplam token: '+data.total_tokens+' · Bitiş: '+data.finish_reason;
    document.getElementById('result').hidden=false;
    status.textContent=data.finish_reason==='length'?'Yanıt token sınırında kesildi.':'Tamamlandı';
  }catch(error){status.textContent=error.message;}
  finally{button.disabled=mode.disabled=prompt.disabled=false;}
});
</script>
</main></body></html>
'''


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type="application/json; charset=utf-8"):
        data = (
            body.encode()
            if isinstance(body, str)
            else json.dumps(body, ensure_ascii=False).encode()
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self.send(200, HTML, "text/html; charset=utf-8")
        elif self.path == "/health":
            self.send(200, {"status": "ok"})
        else:
            self.send(404, {"error": "Bulunamadı."})

    def do_POST(self):
        if self.path != "/api/chat":
            self.send(404, {"error": "Bulunamadı."})
            return
        if not BUSY.acquire(blocking=False):
            self.send(429, {"error": "Başka bir soru işleniyor; biraz sonra tekrar deneyin."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384:
                raise ValueError("Geçersiz istek boyutu.")
            payload = json.loads(self.rfile.read(size))
            prompt = payload.get("prompt") if isinstance(payload, dict) else None
            if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
                raise ValueError("Soru 1–2000 karakter olmalı.")

            mode = payload.get("mode", "pods")
            if mode not in ("pods", "rag", "combined"):
                raise ValueError("Sorgu türü pods, rag veya combined olmalı.")
            result = asyncio.run(
                {"pods": agent, "rag": rag_agent, "combined": combined_agent}[mode](prompt.strip())
            )
            result["mode"] = mode
            answer = result["answer"]
            if isinstance(answer, str):
                answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S)
                result["answer"] = answer.split("<think>", 1)[0].strip()
            self.send(200, result)
        except ValueError as error:
            self.send(400, {"error": str(error)})
        except Exception as error:
            print("Agent isteği başarısız:", type(error).__name__, flush=True)
            self.send(502, {"error": "Agent isteği tamamlanamadı; pod loglarını kontrol edin."})
        finally:
            BUSY.release()


if __name__ == "__main__":
    print("LangGraph MCP/RAG web listening on :8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
