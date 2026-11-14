"""Планировщик Капри: напоминания, таймеры, отложенное выключение ПК.
Команды проверяются раньше остальных (main.py → _process).
Данные — в scheduler.json рядом с программой, переживают перезапуск.
Нейросеть не нужна — работает на любом железе."""
import datetime
import json
import logging
import os
import re
import subprocess
import threading
import time

log = logging.getLogger("capri")
NO_WINDOW = 0x08000000

NUM = {"ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
       "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
       "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
       "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
       "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
       "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100}
ORD = {"первое": 1, "первый": 1, "второе": 2, "второй": 2, "третье": 3, "третий": 3,
       "четвертое": 4, "четвертый": 4, "пятое": 5, "пятый": 5}


def norm(s):
    s = s.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s:.\-]", " ", s)).strip()


def plural(n, one, few, many):
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def fmt_left(sec):
    """Секунды → «1 час 2 минуты» для озвучки."""
    sec = max(0, int(round(sec)))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    parts = []
    if h:
        parts.append(f"{h} " + plural(h, "час", "часа", "часов"))
    if m:
        parts.append(f"{m} " + plural(m, "минута", "минуты", "минут"))
    if s and not h:
        parts.append(f"{s} " + plural(s, "секунда", "секунды", "секунд"))
    return " ".join(parts) or "меньше секунды"


def parse_duration(t):
    """«20 минут», «полтора часа», «1 час 30 минут», «двадцать пять минут» → секунды или None."""
    t = norm(t).replace("полчаса", "30 минут").replace("полминуты", "30 секунд")
    t = re.sub(r"полтора часа?", "90 минут", t)
    total, acc, found = 0, 0, False
    for w in t.split():
        if w.isdigit():
            acc += int(w)
        elif w in NUM:
            acc += NUM[w]
        elif w.startswith("секунд") or w == "сек":
            total += acc or 1
            acc, found = 0, True
        elif w.startswith("минут") or w == "мин":
            total += (acc or 1) * 60
            acc, found = 0, True
        elif w.startswith("час"):
            total += (acc or 1) * 3600
            acc, found = 0, True
        else:
            acc = 0
    return total if found and total > 0 else None


def duration_after(text, marker="через"):
    """Длительность сразу после слова-маркера + остаток фразы (текст напоминания).
    Берём только слова-числа и единицы времени, чтобы не съесть цифры из самого напоминания."""
    i = text.find(marker)
    if i < 0:
        return None, None
    words = text[i + len(marker):].strip().split()
    taken = []
    for w in words[:6]:
        if w.isdigit() or w in NUM or re.match(r"(секунд|минут|час)", w) or w in ("полчаса", "полминуты", "полтора"):
            taken.append(w)
        else:
            break
    if not taken:
        return None, None
    return parse_duration(" ".join(taken)), " ".join(words[len(taken):])


def clock_in(text):
    """«в 18:30», «в 9 утра», «в полдень», «завтра в 9» → (datetime, что вырезать из фразы) или (None, None)."""
    t = norm(text)
    tomorrow = bool(re.search(r"\bзавтра\b", t))
    m = re.search(r"\bв\s+(\d{1,2})[:.](\d{2})\b", t) or re.search(r"\bв\s+(\d{1,2})\s+(\d{2})\b", t)
    cut = []
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        cut.append(m.group(0))
    else:
        m = re.search(r"\bв\s+(\d{1,2}|[а-я]+)\s*(утра|вечера|дня|ночи)?\b", t)
        h = mi = None
        if m:
            raw = m.group(1)
            h = int(raw) if raw.isdigit() else NUM.get(raw)
            part = m.group(2)
            if h is not None:
                if part in ("вечера", "дня") and h < 12:
                    h += 12
                if part == "ночи" and h == 12:
                    h = 0
                mi = 0
                cut.append(m.group(0))
        if h is None and re.search(r"\bв полдень\b", t):
            h, mi = 12, 0
            cut.append("в полдень")
        if h is None and re.search(r"\bв полночь\b", t):
            h, mi = 0, 0
            cut.append("в полночь")
        if h is None:
            return None, None
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None, None
    now = datetime.datetime.now()
    dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if tomorrow:
        dt += datetime.timedelta(days=1)
    elif dt <= now:
        dt += datetime.timedelta(days=1)
    rest = t
    for c in cut + ["завтра"]:
        rest = rest.replace(c, " ")
    return dt, re.sub(r"\s+", " ", rest).strip()


class Scheduler:
    def __init__(self, base_dir, on_fire):
        """on_fire(text) — куда сообщить, когда что-то сработало (Капри скажет вслух)."""
        self.path = os.path.join(base_dir, "scheduler.json")
        self.on_fire = on_fire
        self.lock = threading.Lock()
        self.reminders = []      # {"text": ..., "due": ts}
        self.timers = []         # {"name": ..., "due": ts, "total": sec}
        self.shutdown_at = None
        self.shutdown_restart = False
        self.last_fired = None   # текст последнего сработавшего напоминания — для «отложи»
        self._load()
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------- хранение ----------
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.reminders = data.get("reminders", [])
            self.timers = data.get("timers", [])
            self.shutdown_at = data.get("shutdown_at")
            self.shutdown_restart = data.get("shutdown_restart", False)
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("scheduler.json не прочитался, начинаю с пустого")

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"reminders": self.reminders, "timers": self.timers,
                           "shutdown_at": self.shutdown_at, "shutdown_restart": self.shutdown_restart},
                          f, ensure_ascii=False, indent=2)
        except OSError:
            log.exception("Не удалось сохранить scheduler.json")

    # ---------- фоновая проверка ----------
    def _loop(self):
        while True:
            time.sleep(1)
            now = time.time()
            with self.lock:
                due_t = [t for t in self.timers if t["due"] <= now]
                due_r = [r for r in self.reminders if r["due"] <= now]
                if due_t or due_r:
                    self.timers = [t for t in self.timers if t["due"] > now]
                    self.reminders = [r for r in self.reminders if r["due"] > now]
                    self._save()
            for t in due_t:
                name = f" «{t['name']}»" if t.get("name") else ""
                self.on_fire(f"Таймер{name} на {fmt_left(t['total'])} сработал. Время вышло!")
            for r in due_r:
                self.last_fired = r["text"]
                self.on_fire("Напоминаю: " + r["text"] + ".")

    # ---------- разбор команд ----------
    def handle(self, cmd):
        """Текст ответа, если команда наша; None — пусть разбираются остальные."""
        c = norm(cmd)
        try:
            return self._handle(c)
        except Exception:
            log.exception("Ошибка планировщика на команде: %s", cmd)
            return None

    def _handle(self, c):
        # --- отмена выключения ---
        if re.match(r"^отмени (выключение|перезагрузку|завершение работы)", c):
            r = subprocess.run(["shutdown", "/a"], capture_output=True, creationflags=NO_WINDOW)
            with self.lock:
                self.shutdown_at = None
                self._save()
            if r.returncode == 0:
                return "Выключение отменила. Живи дальше."
            return "Выключение и так не было запланировано."
        # --- сколько до выключения ---
        if re.search(r"(сколько до выключения|когда выключение|сколько до перезагрузки)", c):
            with self.lock:
                at = self.shutdown_at
            if at and at > time.time():
                return f"До {'перезагрузки' if self.shutdown_restart else 'выключения'} осталось {fmt_left(at - time.time())}."
            return "Выключение не запланировано."
        # --- таймеры: список / отмена / создание ---
        if re.search(r"\bтаймер", c):
            return self._timer_cmd(c)
        if re.search(r"^сколько осталось", c):
            with self.lock:
                if self.timers:
                    return self._timers_list()
            return "Таймеров нет."
        # --- напоминания ---
        if re.search(r"(мои|какие) напоминани|что ты должна напомнить", c):
            with self.lock:
                if not self.reminders:
                    return "Напоминаний нет. Скажи «напомни через 20 минут …» — появится."
                return self._reminders_list()
        if re.match(r"^отмени (напоминани|все напоминания)", c):
            return self._reminder_cancel(c)
        if re.match(r"^отложи\b", c) or re.match(r"^напомни позже\b", c):
            return self._snooze(c)
        if re.match(r"^напомни\b", c):
            return self._reminder_create(c)
        # --- отложенное выключение ---
        if re.match(r"^(выключи|перезагрузи|выруби)\b", c) and re.search(r"\b(компьютер|пк|комп)\b", c) and "через" in c:
            return self._shutdown_cmd(c)
        return None

    # ---------- таймеры ----------
    def _timers_list(self):
        now = time.time()
        parts = [f"{t['name'] or 'таймер ' + str(i + 1)} — осталось {fmt_left(t['due'] - now)}"
                 for i, t in enumerate(sorted(self.timers, key=lambda x: x["due"]))]
        return "Таймеры: " + "; ".join(parts) + "."

    def _timer_cmd(self, c):
        with self.lock:
            if re.search(r"(мои|какие|список) таймер|сколько.*таймер", c):
                return self._timers_list() if self.timers else "Таймеров нет."
            if re.match(r"^(отмени|убери|останови|выключи|отключи)", c):
                if not self.timers:
                    return "Таймеров нет."
                if "все" in c or len(self.timers) == 1:
                    self.timers = []
                    self._save()
                    return "Таймеры отменила."
                for i, t in enumerate(self.timers):
                    key = str(i + 1)
                    if (t.get("name") and t["name"] in c) or re.search(rf"\b({key}|{list(ORD)[i * 2] if i * 2 < len(ORD) else key})\b", c):
                        self.timers.pop(i)
                        self._save()
                        return f"Таймер «{t['name'] or key}» отменила."
                return "Какой? " + self._timers_list()
            # создание: «таймер (для яиц) на 8 минут»
            m = re.match(r"^(?:поставь |запусти |включи |поставить )?таймер\b(.*)$", c)
            tail = m.group(1).strip()
            name = None
            mn = re.search(r"\bдля\s+(.+?)(?=\s+на\b|$)", tail)
            if mn:
                name = mn.group(1).strip()
                tail = tail.replace(mn.group(0), " ")
            dur, rest = duration_after(tail, "на")
            if dur is None:
                dur = parse_duration(tail)
                rest = ""
            if dur is None:
                return "На сколько? Скажи, например: «таймер на пять минут»."
            if not name and rest:
                name = re.sub(r"^для\s+", "", rest).strip(" ,.") or None
            self.timers.append({"name": name, "due": time.time() + dur, "total": dur})
            self._save()
            label = f" «{name}»" if name else ""
            return f"Таймер{label} на {fmt_left(dur)} пошёл. Скажу, когда время выйдет."

    # ---------- напоминания ----------
    def _reminders_list(self):
        now = time.time()
        parts = [f"{i + 1}: {r['text']} — через {fmt_left(r['due'] - now)}"
                 for i, r in enumerate(sorted(self.reminders, key=lambda x: x["due"]))]
        return "Напоминания: " + "; ".join(parts) + "."

    def _reminder_create(self, c):
        body = re.sub(r"^напомни(?: мне)?\s*", "", c).strip()
        dur, rest = duration_after(body)
        if dur:
            due, text = time.time() + dur, rest
            when = f"через {fmt_left(dur)}"
        else:
            dt, text = clock_in(body)
            if dt is None:
                return "Когда напомнить? Скажи «напомни через 20 минут …» или «напомни в 18:30 …»."
            due = dt.timestamp()
            day = "завтра " if dt.date() != datetime.date.today() else ""
            when = f"{day}в {dt:%H:%M}"
        text = re.sub(r"^(что|чтобы|о том что|о том чтобы)\s+", "", text or "").strip(" ,.")
        if not text:
            text = "кое-что важное"
        with self.lock:
            self.reminders.append({"text": text, "due": due})
            self._save()
        return f"Записала: {when} напомню — {text}."

    def _reminder_cancel(self, c):
        with self.lock:
            if not self.reminders:
                return "Напоминаний нет."
            if "все" in c:
                self.reminders = []
                self._save()
                return "Все напоминания отменила."
            n = None
            md = re.search(r"\b(\d+)\b", c)
            if md:
                n = int(md.group(1))
            else:
                for w, v in ORD.items():
                    if re.search(rf"\b{w}\b", c):
                        n = v
                        break
            if n and 1 <= n <= len(self.reminders):
                r = self.reminders.pop(n - 1)
                self._save()
                return f"Отменила напоминание: {r['text']}."
            for i, r in enumerate(self.reminders):
                if r["text"] in c:
                    self.reminders.pop(i)
                    self._save()
                    return f"Отменила напоминание: {r['text']}."
            if len(self.reminders) == 1:
                r = self.reminders.pop()
                self._save()
                return f"Отменила напоминание: {r['text']}."
            return "Какое? Скажи номер. " + self._reminders_list()

    def _snooze(self, c):
        if not self.last_fired:
            return "Нечего откладывать — напоминаний ещё не срабатывало."
        dur = parse_duration(c) or 600
        with self.lock:
            self.reminders.append({"text": self.last_fired, "due": time.time() + dur})
            self._save()
        return f"Хорошо, напомню ещё раз через {fmt_left(dur)}: {self.last_fired}."

    # ---------- выключение ----------
    def _shutdown_cmd(self, c):
        dur, _ = duration_after(c)
        if dur is None:
            return "Через сколько? Например: «выключи компьютер через час»."
        restart = c.startswith("перезагрузи")
        subprocess.Popen(["shutdown", "/r" if restart else "/s", "/t", str(int(dur))],
                         creationflags=NO_WINDOW)
        with self.lock:
            self.shutdown_at = time.time() + dur
            self.shutdown_restart = restart
            self._save()
        at = datetime.datetime.fromtimestamp(self.shutdown_at)
        verb = "Перезагружу" if restart else "Выключу"
        return f"{verb} компьютер через {fmt_left(dur)}, в {at:%H:%M}. Передумаешь — скажи «отмени выключение»."