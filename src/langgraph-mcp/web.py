import asyncio
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore

from app import agent

BUSY = BoundedSemaphore(1)

HTML = r'''<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LangGraph · MCP · OGX</title>
<style>
body{font:16px system-ui,sans-serif;background:#f3f5f8;color:#172132;margin:0}
main{max-width:900px;margin:40px auto;padding:0 20px}
section,form{background:white;border:1px solid #dce2e9;border-radius:12px;padding:22px;margin:20px 0}
textarea{box-sizing:border-box;width:100%;min-height:100px;padding:12px;font:inherit}
button{padding:12px 20px;margin-top:12px;background:#193d70;color:white;border:0;border-radius:6px;cursor:pointer}
button:disabled{opacity:.5;cursor:wait}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:14px}
#answer{white-space:pre-wrap;line-height:1.6}
#status{margin-left:12px}summary{cursor:pointer}
</style>
</head>
<body><main>
<h1>LangGraph ile cluster'a sor</h1>
<p>maas-demo podlarını MCP üzerinden sorgula. Her soru bağımsız işlenir.</p>
<form id="form">
<label for="prompt">Sorun</label>
<textarea id="prompt" maxlength="2000" required>maas-demo podlarının durumunu Türkçe ve kısaca özetle.</textarea>
<button id="send">Gönder</button><span id="status" role="status"></span>
</form>
<section id="result" hidden>
<h2>MCP pod özeti</h2><pre id="summary"></pre>
<h2>Model yorumu</h2><div id="answer"></div>
<p id="usage"></p>
<details><summary>MCP araç çağrıları ve sonuçları</summary><pre id="trace"></pre></details>
</section>
<script>
const form=document.getElementById('form');
const button=document.getElementById('send');
const status=document.getElementById('status');
form.addEventListener('submit',async event=>{
  event.preventDefault();
  button.disabled=true;
  status.textContent='Yanıt hazırlanıyor…';
  document.getElementById('result').hidden=true;
  try{
    const response=await fetch('/api/chat',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({prompt:document.getElementById('prompt').value})
    });
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'İstek başarısız.');
    document.getElementById('answer').textContent=data.answer||'Model görünür bir yanıt üretmedi.';
    const fields=[
      ['total_pods','Toplam pod'],
      ['running_pods','Çalışan pod'],
      ['running_ready_pods','Çalışan ve tüm containerları hazır pod'],
      ['succeeded_pods','Tamamlanan pod'],
      ['pending_pods','Pending pod'],
      ['failed_pods','Failed pod'],
      ['other_phase_pods','Diğer phase'],
      ['pods_with_waiting_containers','Bekleyen container içeren pod'],
      ['pods_with_restarts','Geçmiş restart kaydı olan pod'],
      ['total_restarts','Toplam geçmiş restart']
    ];
    document.getElementById('summary').textContent=data.pod_summary
      ? fields.map(([key,label])=>label+': '+data.pod_summary[key]).join('\n')
      : 'MCP özeti alınamadı.';
    document.getElementById('trace').textContent=JSON.stringify(data.tool_calls,null,2);
    document.getElementById('usage').textContent=
      'Model çağrısı: '+data.model_calls+' · Toplam token: '+data.total_tokens+
      ' · Bitiş: '+data.finish_reason;
    document.getElementById('result').hidden=false;
    status.textContent=data.finish_reason==='length'
      ? 'Yanıt token sınırında kesildi.' : 'Tamamlandı';
  }catch(error){
    status.textContent=error.message;
  }finally{
    button.disabled=false;
  }
});
</script>
</main></body></html>'''


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

            result = asyncio.run(agent(prompt.strip()))
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
    print("LangGraph MCP web listening on :8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
