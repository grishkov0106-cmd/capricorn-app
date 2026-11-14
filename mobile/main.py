import asyncio
import flet as ft
import requests


async def main(page: ft.Page):
    page.title = "Капри Remote"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 20
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER

    prefs = ft.SharedPreferences()
    saved_ip = await prefs.get("pc_ip") or "192.168.0.8"
    saved_token = await prefs.get("token") or ""

    ip_field = ft.TextField(label="IP-адрес ПК", value=saved_ip, width=400)
    token_field = ft.TextField(label="Токен (трей Капри → Телефон → IP и токен…)",
                               value=saved_token, width=400,
                               password=True, can_reveal_password=True)
    command_field = ft.TextField(label="Команда для Капри", hint_text="Например: который час",
                                 width=400)
    response_text = ft.Text("Здесь будет ответ Капри...", size=16,
                            color=ft.Colors.GREY_400, text_align=ft.TextAlign.CENTER)
    progress = ft.ProgressRing(visible=False, width=30, height=30)

    async def send_command(e):
        command = (command_field.value or "").strip()
        if not command:
            return
        ip = (ip_field.value or "").strip()
        token = (token_field.value or "").strip()
        await prefs.set("pc_ip", ip)
        await prefs.set("token", token)
        base = f"http://{ip}:5000"
        headers = {"X-Token": token}
        response_text.value = "⏳ Отправка..."
        response_text.color = ft.Colors.YELLOW
        progress.visible = True
        page.update()
        try:
            resp = await asyncio.to_thread(requests.post, base + "/command",
                                           json={"command": command}, headers=headers, timeout=5)
            if resp.status_code == 401:
                response_text.value = "🔒 Неверный токен. Скопируй его из трея Капри."
                response_text.color = ft.Colors.RED
                return
            data = resp.json()
            if not data.get("success"):
                response_text.value = f"❌ {data.get('error', 'Ошибка сервера')}"
                response_text.color = ft.Colors.RED
                return
            cmd_id = data["id"]
            response_text.value = "🤖 Капри думает..."
            page.update()
            for _ in range(90):                      # ждём ответ до полутора минут
                await asyncio.sleep(1)
                r = await asyncio.to_thread(requests.get, base + "/result",
                                            params={"id": cmd_id}, headers=headers, timeout=5)
                d = r.json()
                if d.get("ready"):
                    response_text.value = "Капри: " + d["text"]
                    response_text.color = ft.Colors.GREEN
                    return
            response_text.value = "⌛ Капри не ответила за 90 секунд."
            response_text.color = ft.Colors.ORANGE
        except requests.exceptions.ConnectionError:
            response_text.value = "❌ Нет связи. Проверь IP, один ли Wi-Fi и включён ли сервер (трей → Телефон)."
            response_text.color = ft.Colors.RED
        except requests.exceptions.Timeout:
            response_text.value = "❌ ПК не отвечает (timeout)."
            response_text.color = ft.Colors.RED
        except Exception as ex:
            response_text.value = f"❌ Ошибка: {ex}"
            response_text.color = ft.Colors.RED
        finally:
            progress.visible = False
            command_field.value = ""
            page.update()

    command_field.on_submit = send_command
    send_btn = ft.Button(content="Отправить", on_click=send_command, width=200, height=50,
                         style=ft.ButtonStyle(color=ft.Colors.WHITE, bgcolor=ft.Colors.BLUE_700))

    page.add(ft.Column([
        ft.Text("🤖 Капри Remote", size=28, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_400),
        ft.Text("Управление Капри с телефона", size=14, color=ft.Colors.GREY_500),
        ft.Divider(height=30, color=ft.Colors.TRANSPARENT),
        ip_field, token_field, command_field, send_btn, progress,
        ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
        ft.Container(content=response_text, padding=15, border_radius=10,
                     bgcolor=ft.Colors.GREY_800, width=400, height=140,
                     alignment=ft.Alignment.CENTER),
    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=15))


ft.run(main)