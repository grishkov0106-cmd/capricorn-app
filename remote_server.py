"""Сервер для телефона: команды идут прямо в очередь Капри, ответы возвращаются на телефон.
Запускается из main.py (трей → «Телефон»). Защита — токен из config.json.
Веб-панель: http://IP-ПК:5000/ — открывается в браузере телефона, билд APK не нужен."""
import logging
import secrets
import socket
import threading
import time
import uuid

from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

log = logging.getLogger("capri")

app = Flask(__name__)
_srv = None
_capri = None
_token = ""
_results = {}          # id команды -> (время, текст ответа)
_lock = threading.Lock()
RESULT_TTL = 300       # сколько храним ответ, если телефон его не забрал


def local_ip():
    """IP этого ПК в локальной сети (пакеты никуда не отправляются)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _auth_ok():
    token = request.headers.get("X-Token") or request.args.get("token") or ""
    return bool(_token) and secrets.compare_digest(token, _token)


@app.route("/")
def panel():
    return Response(PANEL_HTML, content_type="text/html; charset=utf-8")


@app.route("/ping")
def ping():
    return jsonify({"ok": True, "app": "capri"})


@app.route("/auth")
def auth():
    if not _auth_ok():
        return jsonify({"ok": False}), 401
    return jsonify({"ok": True})


@app.route("/command", methods=["POST"])
def command():
    if not _auth_ok():
        return jsonify({"success": False, "error": "Неверный токен"}), 401
    data = request.get_json(silent=True) or {}
    text = (data.get("command") or "").strip()
    if not text:
        return jsonify({"success": False, "error": "Пустая команда"})
    cmd_id = uuid.uuid4().hex[:12]
    log.info("Команда с телефона: %s", text)
    _capri.text_command(text, cmd_id=cmd_id)
    return jsonify({"success": True, "id": cmd_id})


@app.route("/result")
def result():
    if not _auth_ok():
        return jsonify({"error": "Неверный токен"}), 401
    cmd_id = request.args.get("id", "")
    with _lock:
        now = time.time()
        for k in [k for k, (ts, _) in _results.items() if now - ts > RESULT_TTL]:
            _results.pop(k, None)
        item = _results.pop(cmd_id, None)
    if item:
        return jsonify({"ready": True, "text": item[1]})
    return jsonify({"ready": False})


def publish_result(cmd_id, text):
    """Вызывается из main.py, когда Капри сформировала ответ на команду с телефона."""
    if cmd_id:
        with _lock:
            _results[cmd_id] = (time.time(), text)


def start(capri, token, port=5000):
    global _srv, _capri, _token
    if _srv:
        return True                      # уже запущен
    _capri, _token = capri, token
    try:
        _srv = make_server("0.0.0.0", port, app, threaded=True)
    except OSError:
        log.exception("Порт %s занят — сервер для телефона не запущен", port)
        return False
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # не засоряем capri.log каждым запросом
    threading.Thread(target=_srv.serve_forever, daemon=True).start()
    log.info("Сервер для телефона: http://%s:%s", local_ip(), port)
    return True


def stop():
    global _srv
    if _srv:
        threading.Thread(target=_srv.shutdown, daemon=True).start()
        _srv = None
        log.info("Сервер для телефона остановлен")


PANEL_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#04090b">
<title>КАПРИ // REMOTE</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Ccircle cx='32' cy='32' r='28' fill='%23ff2e63'/%3E%3Cellipse cx='32' cy='32' rx='16' ry='9' fill='%23061814' stroke='%232bff9e' stroke-width='4'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Russo+One&family=JetBrains+Mono:wght@400;600&family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#04090b; --bg2:#071114; --surface:#0a171c; --surface2:#0e1e24;
  --line:#1b3a3a; --line2:#265049;
  --pink:#ff2e63; --purple:#a05cff; --green:#2bff9e; --cyan:#2bd9ff; --amber:#ffb02e; --red:#ff4545;
  --text:#d9f7ec; --muted:#6f9a8d; --dim:#41615a;
  --mono:'JetBrains Mono',ui-monospace,Consolas,monospace;
  --disp:'Russo One','Arial Black',sans-serif;
  --body:'Inter',system-ui,'Segoe UI',sans-serif;
}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  background:var(--bg); color:var(--text); font-family:var(--body);
  min-height:100vh; overflow-x:hidden; -webkit-tap-highlight-color:transparent;
}
::selection{background:var(--pink);color:#fff}

/* ---------- фон: сетка, аврора, сканлайны, зерно ---------- */
body::before{
  content:""; position:fixed; inset:0; z-index:0; pointer-events:none;
  background-image:
    linear-gradient(rgba(43,217,255,.05) 1px,transparent 1px),
    linear-gradient(90deg,rgba(43,217,255,.05) 1px,transparent 1px);
  background-size:44px 44px;
  mask-image:radial-gradient(ellipse 90% 70% at 50% 20%,#000 30%,transparent 100%);
}
.aurora{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden}
.aurora i{position:absolute;border-radius:50%;filter:blur(90px);opacity:.28;mix-blend-mode:screen}
.aurora i:nth-child(1){width:55vw;height:55vw;left:-15vw;top:-20vh;background:var(--pink);animation:drift1 26s ease-in-out infinite alternate}
.aurora i:nth-child(2){width:45vw;height:45vw;right:-12vw;top:30vh;background:var(--purple);animation:drift2 32s ease-in-out infinite alternate}
.aurora i:nth-child(3){width:35vw;height:35vw;left:20vw;bottom:-18vh;background:#0affc2;opacity:.14;animation:drift1 38s ease-in-out infinite alternate-reverse}
@keyframes drift1{to{transform:translate(9vw,7vh) scale(1.15)}}
@keyframes drift2{to{transform:translate(-8vw,-6vh) scale(.9)}}
.scanlines{
  position:fixed;inset:0;z-index:3;pointer-events:none;opacity:.5;
  background:repeating-linear-gradient(0deg,rgba(0,0,0,.22) 0 1px,transparent 1px 3px);
  animation:scan 9s linear infinite;
}
@keyframes scan{to{background-position:0 60px}}
.grain{
  position:fixed;inset:0;z-index:3;pointer-events:none;opacity:.05;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)'/%3E%3C/svg%3E");
}
.vignette{position:fixed;inset:0;z-index:2;pointer-events:none;background:radial-gradient(ellipse 120% 100% at 50% 40%,transparent 55%,rgba(0,0,0,.55) 100%)}

/* ---------- шапка ---------- */
header{
  position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:12px;
  padding:12px 16px;border-bottom:1px solid var(--line);
  background:rgba(4,9,11,.78);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  animation:rise .5s cubic-bezier(.2,.8,.2,1) both;
}
.logo{width:38px;height:38px;flex:none;filter:drop-shadow(0 0 8px rgba(255,46,99,.6))}
.title-wrap{line-height:1.05;min-width:0}
.glitch{
  font-family:var(--disp);font-size:22px;letter-spacing:3px;color:#fff;position:relative;
  text-shadow:0 0 14px rgba(255,46,99,.7);
}
.glitch::before,.glitch::after{
  content:attr(data-text);position:absolute;inset:0;overflow:hidden;
}
.glitch::before{color:var(--cyan);animation:gl1 4.2s infinite steps(1)}
.glitch::after{color:var(--pink);animation:gl2 3.7s infinite steps(1)}
@keyframes gl1{
  0%,93%,100%{clip-path:inset(0 0 100% 0);transform:none}
  94%{clip-path:inset(10% 0 55% 0);transform:translate(-3px,1px)}
  96%{clip-path:inset(60% 0 8% 0);transform:translate(3px,-1px)}
  98%{clip-path:inset(30% 0 40% 0);transform:translate(-2px,0)}
}
@keyframes gl2{
  0%,90%,100%{clip-path:inset(0 0 100% 0);transform:none}
  91%{clip-path:inset(55% 0 20% 0);transform:translate(3px,1px)}
  93%{clip-path:inset(5% 0 70% 0);transform:translate(-3px,-1px)}
  95%{clip-path:inset(40% 0 35% 0);transform:translate(2px,0)}
}
.subtitle{font-family:var(--mono);font-size:10px;letter-spacing:2.5px;color:var(--muted);margin-top:3px}
.spacer{flex:1}
.status{
  display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:10px;letter-spacing:1px;
  padding:7px 11px;border:1px solid var(--line);border-radius:99px;color:var(--muted);
  background:var(--surface);white-space:nowrap;transition:.3s;
}
.status .led{width:8px;height:8px;border-radius:50%;background:var(--red);box-shadow:0 0 8px var(--red);flex:none}
.status.on{color:var(--green);border-color:rgba(43,255,158,.4)}
.status.on .led{background:var(--green);box-shadow:0 0 10px var(--green);animation:pulse 1.8s ease-in-out infinite}
@keyframes pulse{50%{opacity:.45}}
.icon-btn{
  width:38px;height:38px;flex:none;border:1px solid var(--line);border-radius:10px;cursor:pointer;
  background:var(--surface);color:var(--text);font-size:16px;transition:.2s;
}
.icon-btn:hover{border-color:var(--pink);box-shadow:0 0 12px rgba(255,46,99,.35)}
.icon-btn.off{opacity:.5;border-style:dashed}

/* ---------- компоновка ---------- */
main{position:relative;z-index:5;max-width:640px;margin:0 auto;padding:18px 16px 40px;display:flex;flex-direction:column;gap:16px}
.card{
  position:relative;border:1px solid var(--line);border-radius:14px;padding:16px;
  background:linear-gradient(180deg,var(--surface),var(--bg2));
  box-shadow:0 10px 40px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.03);
  animation:rise .6s cubic-bezier(.2,.8,.2,1) both;
}
.card:nth-of-type(1){animation-delay:.08s}
.card:nth-of-type(2){animation-delay:.16s}
.card:nth-of-type(3){animation-delay:.24s}
.card::before{
  content:"";position:absolute;top:-1px;left:14px;right:14px;height:2px;border-radius:2px;
  background:linear-gradient(90deg,var(--pink),var(--purple),var(--green));opacity:.7;
}
@keyframes rise{from{opacity:0;transform:translateY(18px)}to{opacity:1;transform:none}}
.card-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
.card-tag{font-family:var(--mono);font-size:10px;letter-spacing:2px;color:var(--cyan)}
.card-state{font-family:var(--mono);font-size:10px;letter-spacing:1px;color:var(--amber)}
.card-state.ok{color:var(--green)}
.card-state.bad{color:var(--red)}
.hint{font-size:12px;color:var(--muted);line-height:1.55;margin-bottom:12px}
.hint b{color:var(--text);font-family:var(--mono);font-weight:600}
.kbd-hint{font-family:var(--mono);font-size:10px;color:var(--dim);letter-spacing:1px}

/* ---------- канал связи ---------- */
.token-row{display:flex;gap:10px}
.token-wrap{position:relative;flex:1;min-width:0}
input{
  width:100%;background:#050f12;border:1px solid var(--line);border-radius:10px;
  color:var(--text);font-family:var(--mono);font-size:14px;padding:13px 42px 13px 14px;
  outline:none;transition:.2s;caret-color:var(--pink);
}
input:focus{border-color:var(--pink);box-shadow:0 0 0 3px rgba(255,46,99,.15),0 0 18px rgba(255,46,99,.2)}
input::placeholder{color:var(--dim)}
.token-wrap .icon-btn{position:absolute;right:5px;top:50%;transform:translateY(-50%);width:32px;height:32px;border:none;background:transparent;font-size:14px}
.btn{
  font-family:var(--disp);font-size:12px;letter-spacing:1.5px;cursor:pointer;border:none;
  padding:13px 18px;color:#fff;transition:.2s;white-space:nowrap;
}
.btn-ghost{
  background:var(--surface2);border:1px solid var(--line2);border-radius:10px;color:var(--green);
}
.btn-ghost:hover{border-color:var(--green);box-shadow:0 0 14px rgba(43,255,158,.25)}
.btn-primary{
  background:linear-gradient(120deg,var(--pink),var(--purple));
  clip-path:polygon(10px 0,100% 0,100% calc(100% - 10px),calc(100% - 10px) 100%,0 100%,0 10px);
  box-shadow:0 0 20px rgba(255,46,99,.35);
}
.btn-primary:hover{filter:brightness(1.2);box-shadow:0 0 30px rgba(255,46,99,.55)}
.btn-primary:active{transform:scale(.96)}
.btn:disabled{opacity:.55;cursor:wait;filter:grayscale(.4)}
.link-msg{font-family:var(--mono);font-size:11px;margin-top:10px;min-height:15px;color:var(--muted)}
.link-card.linked{border-color:rgba(43,255,158,.45);box-shadow:0 0 24px rgba(43,255,158,.12),0 10px 40px rgba(0,0,0,.45)}
.link-card.linked::before{background:linear-gradient(90deg,var(--green),var(--cyan));opacity:1}

/* ---------- командная строка ---------- */
.cmd-row{display:flex;align-items:center;gap:10px}
.prompt{font-family:var(--mono);font-size:20px;color:var(--pink);text-shadow:0 0 10px rgba(255,46,99,.8);flex:none}
.cmd-row input{flex:1;padding-right:14px}
.chips{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin-top:14px}
.chip{
  font-family:var(--body);font-size:12px;font-weight:600;color:var(--text);cursor:pointer;
  background:var(--surface2);border:1px solid var(--line);padding:10px 12px;text-align:left;
  clip-path:polygon(7px 0,100% 0,100% calc(100% - 7px),calc(100% - 7px) 100%,0 100%,0 7px);
  transition:.18s;
}
.chip:hover{border-color:var(--purple);background:#14222b;transform:translateY(-2px);box-shadow:0 6px 16px rgba(160,92,255,.25)}
.chip:active{transform:scale(.96)}
body.busy .chip{opacity:.4;pointer-events:none}

/* ---------- терминал ---------- */
.mini-btn{
  font-family:var(--mono);font-size:10px;letter-spacing:1.5px;color:var(--muted);cursor:pointer;
  background:transparent;border:1px solid var(--line);border-radius:7px;padding:5px 10px;transition:.2s;
}
.mini-btn:hover{color:var(--red);border-color:var(--red)}
.terminal{
  max-height:46vh;overflow-y:auto;display:flex;flex-direction:column;gap:12px;
  font-family:var(--mono);padding-right:6px;
  scrollbar-width:thin;scrollbar-color:var(--purple) #071114;
}
.terminal::-webkit-scrollbar{width:8px}
.terminal::-webkit-scrollbar-track{background:#071114;border-radius:4px}
.terminal::-webkit-scrollbar-thumb{background:linear-gradient(var(--pink),var(--purple));border-radius:4px}
.terminal::-webkit-scrollbar-thumb:hover{background:var(--pink)}
.boot{color:var(--dim);font-size:12px;line-height:1.9;padding:8px 2px}
.boot .b1{color:var(--cyan);letter-spacing:2px}
.boot .cursor{display:inline-block;width:9px;height:15px;background:var(--green);vertical-align:-2px;animation:blink 1s steps(1) infinite;box-shadow:0 0 8px var(--green)}
@keyframes blink{50%{opacity:0}}
.entry{animation:rise .35s ease both}
.entry-head{display:flex;align-items:baseline;gap:8px;font-size:13px}
.entry-head .prompt{font-size:15px}
.entry-cmd{color:#fff;font-weight:600;word-break:break-word}
.entry-head time{margin-left:auto;color:var(--dim);font-size:10px;flex:none;padding-left:8px}
.entry-res{
  display:flex;gap:9px;align-items:flex-start;margin:7px 0 0 20px;padding:10px 12px;
  border-radius:8px;font-size:12.5px;line-height:1.6;border-left:2px solid var(--line2);
  background:rgba(255,255,255,.02);
}
.entry-res .res-text{white-space:pre-wrap;word-break:break-word}
.entry-res.thinking{border-left-color:var(--amber);color:var(--amber)}
.entry-res.thinking .res-text{animation:pulse 1.4s ease-in-out infinite;letter-spacing:1px}
.entry-res.ok{border-left-color:var(--green);background:rgba(43,255,158,.06);color:var(--text)}
.entry-res.ok .res-ico{color:var(--green)}
.entry-res.err{border-left-color:var(--red);background:rgba(255,69,69,.07);color:#ffb3b3}
.entry-res.err .res-ico{color:var(--red)}
.res-ico{flex:none;font-weight:600}
.spin{
  width:13px;height:13px;flex:none;margin-top:2px;border-radius:50%;
  border:2px solid rgba(255,176,46,.25);border-top-color:var(--amber);animation:rot .8s linear infinite;
}
@keyframes rot{to{transform:rotate(360deg)}}

/* ---------- прочее ---------- */
footer{position:relative;z-index:5;text-align:center;font-family:var(--mono);font-size:10px;letter-spacing:1px;color:var(--dim);padding:0 16px 28px;line-height:1.8}
.toast{
  position:fixed;top:70px;left:50%;transform:translate(-50%,-16px);z-index:50;
  background:var(--surface2);border:1px solid var(--pink);color:var(--text);
  font-size:12.5px;padding:11px 18px;border-radius:10px;opacity:0;pointer-events:none;
  transition:.3s;box-shadow:0 8px 30px rgba(0,0,0,.6),0 0 18px rgba(255,46,99,.3);max-width:88vw;text-align:center;
}
.toast.show{opacity:1;transform:translate(-50%,0)}
.shake{animation:shake .4s ease}
@keyframes shake{20%,60%{transform:translateX(-7px)}40%,80%{transform:translateX(7px)}}
:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
@media (max-width:420px){
  .glitch{font-size:18px}
  .subtitle{font-size:9px}
  .status{font-size:9px;padding:6px 9px}
  .chips{grid-template-columns:1fr 1fr}
  .btn{padding:13px 14px;font-size:11px}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01s !important;animation-iteration-count:1 !important;transition-duration:.01s !important}
}
</style>
</head>
<body>
<div class="aurora"><i></i><i></i><i></i></div>
<div class="vignette"></div>
<div class="scanlines"></div>
<div class="grain"></div>

<header>
  <svg class="logo" viewBox="0 0 64 64" aria-hidden="true">
    <circle cx="32" cy="32" r="28" fill="#ff2e63" stroke="#a05cff" stroke-width="4"/>
    <ellipse cx="32" cy="32" rx="18" ry="10" fill="#061814" stroke="#2bff9e" stroke-width="4"/>
    <circle cx="32" cy="32" r="5" fill="#2bff9e"/>
  </svg>
  <div class="title-wrap">
    <h1 class="glitch" data-text="КАПРИ">КАПРИ</h1>
    <div class="subtitle">REMOTE LINK // v2.0</div>
  </div>
  <div class="spacer"></div>
  <div class="status" id="statusPill"><span class="led"></span><span id="statusText">ПОИСК…</span></div>
  <button class="icon-btn" id="soundBtn" aria-pressed="true" title="Звук интерфейса">🔊</button>
</header>

<main>
  <section class="card link-card" id="linkCard">
    <div class="card-head">
      <span class="card-tag">01 // КАНАЛ СВЯЗИ</span>
      <span class="card-state" id="linkState">НЕ ПОДКЛЮЧЕНО</span>
    </div>
    <p class="hint">Сервер: <b id="hostLabel"></b> · Токен возьми на ПК: трей Капри → Телефон → «IP и токен для телефона…»</p>
    <div class="token-row">
      <div class="token-wrap">
        <input type="password" id="tokenInput" placeholder="Вставь токен" autocomplete="off" spellcheck="false">
        <button class="icon-btn" id="eyeBtn" title="Показать токен">👁</button>
      </div>
      <button class="btn btn-ghost" id="connectBtn">ПОДКЛЮЧИТЬ</button>
    </div>
    <p class="link-msg" id="linkMsg"></p>
  </section>

  <section class="card">
    <div class="card-head">
      <span class="card-tag">02 // КОМАНДНАЯ СТРОКА</span>
      <span class="kbd-hint">ENTER ⏎</span>
    </div>
    <div class="cmd-row">
      <span class="prompt">›</span>
      <input id="cmdInput" placeholder="например: который час" autocomplete="off" spellcheck="false">
      <button class="btn btn-primary" id="sendBtn"><span id="sendLabel">ОТПРАВИТЬ</span></button>
    </div>
    <div class="chips" id="chips">
      <button class="chip" data-cmd="который час">🕐 Который час</button>
      <button class="chip" data-cmd="какая погода">🌦 Погода</button>
      <button class="chip" data-cmd="пауза">⏸ Пауза</button>
      <button class="chip" data-cmd="продолжи">▶ Продолжи</button>
      <button class="chip" data-cmd="громкость 40">🔊 Громкость 40</button>
      <button class="chip" data-cmd="сделай скриншот">📸 Скриншот</button>
      <button class="chip" data-cmd="что грузит компьютер">🧠 Что грузит ПК</button>
      <button class="chip" data-cmd="проверь компьютер">🩺 Диагностика</button>
      <button class="chip" data-cmd="сверни всё">🗔 Сверни всё</button>
      <button class="chip" data-cmd="заблокируй компьютер">🔒 Блокировка</button>
    </div>
  </section>

  <section class="card terminal-card">
    <div class="card-head">
      <span class="card-tag">03 // ЖУРНАЛ ОПЕРАЦИЙ</span>
      <button class="mini-btn" id="clearBtn">ОЧИСТИТЬ</button>
    </div>
    <div class="terminal" id="terminal"></div>
  </section>
</main>

<footer>ПК И ТЕЛЕФОН — В ОДНОЙ WI-FI СЕТИ · ПОРТ 5000<br>ДОБАВЬ СТРАНИЦУ НА ГЛАВНЫЙ ЭКРАН — БУДЕТ КАК ПРИЛОЖЕНИЕ</footer>
<div class="toast" id="toast"></div>

<script>
(function(){
'use strict';
var $ = function(s){ return document.querySelector(s); };
var statusPill=$('#statusPill'), statusText=$('#statusText'), soundBtn=$('#soundBtn'),
    linkCard=$('#linkCard'), linkState=$('#linkState'), hostLabel=$('#hostLabel'),
    tokenInput=$('#tokenInput'), eyeBtn=$('#eyeBtn'), connectBtn=$('#connectBtn'), linkMsg=$('#linkMsg'),
    cmdInput=$('#cmdInput'), sendBtn=$('#sendBtn'), sendLabel=$('#sendLabel'),
    terminal=$('#terminal'), clearBtn=$('#clearBtn'), toastEl=$('#toast');

var state = {
  token: localStorage.getItem('capri_token') || '',
  sound: localStorage.getItem('capri_sound') !== '0',
  online: false, linked: false, busy: false
};
var logArr = [];
try { logArr = JSON.parse(localStorage.getItem('capri_log') || '[]'); } catch(e){ logArr = []; }

/* ---------- звук ---------- */
var AC = null;
function tone(f, t0, d, type, g){
  var o = AC.createOscillator(), gn = AC.createGain();
  o.type = type; o.frequency.value = f;
  gn.gain.setValueAtTime(0.0001, t0);
  gn.gain.exponentialRampToValueAtTime(g, t0 + 0.012);
  gn.gain.exponentialRampToValueAtTime(0.0001, t0 + d);
  o.connect(gn); gn.connect(AC.destination);
  o.start(t0); o.stop(t0 + d + 0.03);
}
function sfx(kind){
  if(!state.sound) return;
  try{
    AC = AC || new (window.AudioContext || window.webkitAudioContext)();
    if(AC.state === 'suspended') AC.resume();
    var t = AC.currentTime + 0.01;
    if(kind === 'send'){ tone(620,t,0.07,'square',0.03); tone(930,t+0.07,0.09,'square',0.03); }
    else if(kind === 'ok'){ tone(523,t,0.10,'sine',0.05); tone(784,t+0.09,0.15,'sine',0.05); }
    else if(kind === 'err'){ tone(180,t,0.16,'sawtooth',0.05); tone(120,t+0.10,0.18,'sawtooth',0.04); }
    else { tone(440,t,0.05,'square',0.02); }
  }catch(e){}
}
function updateSoundBtn(){
  soundBtn.textContent = state.sound ? '🔊' : '🔇';
  soundBtn.setAttribute('aria-pressed', String(state.sound));
  soundBtn.classList.toggle('off', !state.sound);
}
soundBtn.onclick = function(){
  state.sound = !state.sound;
  localStorage.setItem('capri_sound', state.sound ? '1' : '0');
  updateSoundBtn();
  if(state.sound) sfx('tap');
};

/* ---------- утилиты ---------- */
function now(){ return new Date().toTimeString().slice(0,8); }
var toastTimer = null;
function toast(msg){
  toastEl.textContent = msg; toastEl.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function(){ toastEl.classList.remove('show'); }, 2800);
}
function shake(el){ el.classList.remove('shake'); void el.offsetWidth; el.classList.add('shake'); }
function scrollTerm(){ terminal.scrollTop = terminal.scrollHeight; }
function saveLog(){ localStorage.setItem('capri_log', JSON.stringify(logArr.slice(-40))); }

/* ---------- статус связи ---------- */
function setStatus(ok, ms){
  state.online = ok;
  statusPill.classList.toggle('on', ok);
  statusText.textContent = ok ? ('НА СВЯЗИ' + (ms != null ? ' · ' + ms + 'мс' : '')) : 'НЕТ СВЯЗИ';
}
function ping(){
  var t = performance.now();
  fetch('/ping', {cache:'no-store'})
    .then(function(r){ setStatus(r.ok, Math.round(performance.now() - t)); })
    .catch(function(){ setStatus(false); });
}

/* ---------- авторизация ---------- */
function setLinked(ok, msg){
  state.linked = ok;
  linkCard.classList.toggle('linked', ok);
  linkState.textContent = msg;
  linkState.className = 'card-state ' + (ok ? 'ok' : 'bad');
}
function checkAuth(){
  if(!state.token){ setLinked(false, 'ТОКЕН НЕ ЗАДАН'); return Promise.resolve(false); }
  return fetch('/auth', {headers:{'X-Token':state.token}, cache:'no-store'})
    .then(function(r){
      if(r.ok){ setLinked(true, 'КАНАЛ ЗАЩИЩЁН'); return true; }
      setLinked(false, 'ТОКЕН ОТКЛОНЁН'); return false;
    })
    .catch(function(){ setLinked(false, 'СЕРВЕР НЕДОСТУПЕН'); return false; });
}
connectBtn.onclick = function(){
  var tok = tokenInput.value.trim();
  if(!tok){ linkMsg.textContent = '// вставь токен из трея Капри'; shake(linkCard); return; }
  state.token = tok;
  localStorage.setItem('capri_token', tok);
  connectBtn.disabled = true; connectBtn.textContent = 'ПРОВЕРКА…';
  checkAuth().then(function(ok){
    connectBtn.disabled = false; connectBtn.textContent = 'ПОДКЛЮЧИТЬ';
    if(ok){ linkMsg.textContent = '// токен принят, канал открыт'; sfx('ok'); toast('Канал защищён. Можно командовать.'); }
    else { linkMsg.textContent = '// токен не подошёл — скопируй заново из трея'; sfx('err'); shake(linkCard); }
  });
};
eyeBtn.onclick = function(){
  var show = tokenInput.type === 'password';
  tokenInput.type = show ? 'text' : 'password';
  eyeBtn.textContent = show ? '🙈' : '👁';
};

/* ---------- журнал ---------- */
function renderEmpty(){
  terminal.innerHTML = '';
  var b = document.createElement('div');
  b.className = 'boot';
  b.innerHTML = '<div class="b1">CAPRI OS — REMOTE TERMINAL</div>' +
    '<div>Канал открыт. Введи команду или нажми быструю кнопку.</div>' +
    '<div>ОЖИДАНИЕ КОМАНДЫ <span class="cursor"></span></div>';
  terminal.appendChild(b);
}
function makeEntry(cmd, ts){
  var el = document.createElement('div'); el.className = 'entry';
  var head = document.createElement('div'); head.className = 'entry-head';
  var p = document.createElement('span'); p.className = 'prompt'; p.textContent = '›';
  var c = document.createElement('span'); c.className = 'entry-cmd'; c.textContent = cmd;
  var t = document.createElement('time'); t.textContent = ts || now();
  head.appendChild(p); head.appendChild(c); head.appendChild(t);
  var res = document.createElement('div'); res.className = 'entry-res thinking';
  res.innerHTML = '<span class="spin"></span><span class="res-text">КАПРИ ДУМАЕТ</span><span>…</span>';
  el.appendChild(head); el.appendChild(res);
  return {el:el, res:res, cmd:cmd};
}
function fillRes(e, status, text){
  e.res.className = 'entry-res ' + status;
  e.res.innerHTML = '';
  var icon = document.createElement('span'); icon.className = 'res-ico';
  icon.textContent = status === 'ok' ? '✓' : '✗';
  var txt = document.createElement('span'); txt.className = 'res-text'; txt.textContent = text;
  e.res.appendChild(icon); e.res.appendChild(txt);
}
function complete(e, status, text){
  fillRes(e, status, text);
  logArr.push({c:e.cmd, r:text, s:status, t:now()});
  if(logArr.length > 40) logArr.shift();
  saveLog(); scrollTerm();
}
function restore(){
  terminal.innerHTML = '';
  if(!logArr.length){ renderEmpty(); return; }
  logArr.forEach(function(it){
    var e = makeEntry(it.c, it.t);
    fillRes(e, it.s, it.r);
    terminal.appendChild(e.el);
  });
  scrollTerm();
}
clearBtn.onclick = function(){ logArr = []; saveLog(); restore(); sfx('tap'); };

/* ---------- отправка команды ---------- */
function setBusy(b){
  state.busy = b;
  sendBtn.disabled = b;
  sendLabel.textContent = b ? 'ВЫПОЛНЯЮ…' : 'ОТПРАВИТЬ';
  document.body.classList.toggle('busy', b);
}
function pollResult(id, e, tries){
  if(tries > 90){
    complete(e, 'err', 'Капри не ответила за 90 секунд.');
    setBusy(false); sfx('err'); return;
  }
  setTimeout(function(){
    fetch('/result?id=' + encodeURIComponent(id), {headers:{'X-Token':state.token}, cache:'no-store'})
      .then(function(r){
        if(r.status === 401){
          complete(e, 'err', 'Токен отклонён — подключись заново.');
          setLinked(false, 'ТОКЕН ОТКЛОНЁН'); setBusy(false); sfx('err'); return;
        }
        return r.json();
      })
      .then(function(d){
        if(!d) return;
        if(d.ready){ complete(e, 'ok', d.text); setBusy(false); sfx('ok'); return; }
        pollResult(id, e, tries + 1);
      })
      .catch(function(){ pollResult(id, e, tries + 1); });
  }, 1000);
}
function send(raw){
  var cmd = (raw || '').trim();
  if(!cmd) return;
  if(state.busy){ toast('Капри уже выполняет команду — дождись ответа.'); return; }
  if(!state.linked){
    toast('Сначала подключись: вставь токен в «Канал связи».');
    shake(linkCard); tokenInput.focus(); return;
  }
  cmdInput.value = '';
  setBusy(true); sfx('send');
  if(navigator.vibrate) navigator.vibrate(12);
  var boot = terminal.querySelector('.boot');
  if(boot) terminal.innerHTML = '';
  var e = makeEntry(cmd);
  terminal.appendChild(e.el); scrollTerm();
  fetch('/command', {
    method:'POST',
    headers:{'Content-Type':'application/json', 'X-Token':state.token},
    body:JSON.stringify({command:cmd})
  })
    .then(function(r){
      if(r.status === 401){
        complete(e, 'err', 'Токен отклонён — подключись заново.');
        setLinked(false, 'ТОКЕН ОТКЛОНЁН'); setBusy(false); sfx('err'); return;
      }
      return r.json().then(function(d){
        if(!d.success){ complete(e, 'err', d.error || 'Сервер вернул ошибку.'); setBusy(false); sfx('err'); return; }
        pollResult(d.id, e, 0);
      });
    })
    .catch(function(){
      complete(e, 'err', 'Нет связи с ПК. Проверь Wi-Fi и запущен ли сервер.');
      setBusy(false); sfx('err');
    });
}
sendBtn.onclick = function(){ send(cmdInput.value); };
cmdInput.addEventListener('keydown', function(ev){
  if(ev.key === 'Enter'){ ev.preventDefault(); send(cmdInput.value); }
});
Array.prototype.forEach.call(document.querySelectorAll('.chip'), function(b){
  b.onclick = function(){ sfx('tap'); send(b.getAttribute('data-cmd')); };
});

/* ---------- старт ---------- */
hostLabel.textContent = location.host;
tokenInput.value = state.token;
updateSoundBtn();
restore();
ping();
setInterval(ping, 8000);
if(state.token) checkAuth();
document.addEventListener('visibilitychange', function(){ if(!document.hidden) ping(); });
})();
</script>
</body>
</html>
"""