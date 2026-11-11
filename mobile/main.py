import flet as ft
import requests
import asyncio

async def main(page: ft.Page):
    page.title = "Капри Remote"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 20
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    
    # Инициализируем хранилище (Flet 1.x)
    prefs = ft.SharedPreferences()
    saved_ip = await prefs.get("pc_ip") or "192.168.0.8"
    
    ip_field = ft.TextField(
        label="IP-адрес ПК",
        value=saved_ip,
        width=400,
        text_size=16,
        keyboard_type=ft.KeyboardType.TEXT
    )
    
    command_field = ft.TextField(
        label="Команда для Капри",
        hint_text="Например: какая погода?",
        width=400,
        text_size=16,
        on_submit=lambda e: send_command(e)
    )
    
    response_text = ft.Text(
        "Здесь будет ответ Капри...",
        size=16,
        color=ft.Colors.GREY_400,
        text_align=ft.TextAlign.CENTER
    )
    
    progress = ft.ProgressRing(visible=False, width=30, height=30)
    
    history = []
    history_text = ft.Text("", size=14, color=ft.Colors.GREY_500, text_align=ft.TextAlign.CENTER)
    
    async def send_command(e):
        command = command_field.value.strip()
        if not command:
            return
        
        await prefs.set("pc_ip", ip_field.value.strip())
        
        response_text.value = "⏳ Отправка..."
        response_text.color = ft.Colors.YELLOW
        progress.visible = True
        page.update()
        
        try:
            ip = ip_field.value.strip()
            url = f"http://{ip}:5000/command"
            
            # Выполняем запрос в фоновом потоке, чтобы UI не зависал
            def do_request():
                return requests.post(url, json={"command": command}, timeout=5)
            
            resp = await asyncio.to_thread(do_request)
            data = resp.json()
            
            if data.get("success"):
                response_text.value = f"✅ Отправлено: {command}"
                response_text.color = ft.Colors.GREEN
                
                history.insert(0, command)
                if len(history) > 5:
                    history.pop()
                history_text.value = "Последние: " + " | ".join(history[:5])
            else:
                response_text.value = f"❌ Ошибка сервера: {data.get('error', 'Неизвестно')}"
                response_text.color = ft.Colors.RED
                
        except requests.exceptions.ConnectionError:
            response_text.value = "❌ Не удалось подключиться.\nПроверь IP и запущен ли сервер."
            response_text.color = ft.Colors.RED
        except requests.exceptions.Timeout:
            response_text.value = "❌ Превышено время ожидания ответа от ПК."
            response_text.color = ft.Colors.RED
        except Exception as ex:
            response_text.value = f"❌ Ошибка: {str(ex)}"
            response_text.color = ft.Colors.RED
        finally:
            progress.visible = False
            command_field.value = ""
            page.update()
    
    # ИСПРАВЛЕНО ДЛЯ FLET 1.0: ElevatedButton -> Button, text -> content
    send_btn = ft.Button(
        content="Отправить",
        on_click=send_command,
        width=200,
        height=50,
        style=ft.ButtonStyle(
            color=ft.Colors.WHITE,
            bgcolor=ft.Colors.BLUE_700
        )
    )
    
    page.add(
        ft.Column(
            [
                ft.Text(
                    "🤖 Капри Remote",
                    size=28,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.BLUE_400
                ),
                ft.Text(
                    "Управление Капри с телефона",
                    size=14,
                    color=ft.Colors.GREY_500
                ),
                ft.Divider(height=30, color=ft.Colors.TRANSPARENT),
                ip_field,
                command_field,
                send_btn,
                progress,
                ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                history_text,
                ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                ft.Container(
                    content=response_text,
                    padding=15,
                    border_radius=10,
                    bgcolor=ft.Colors.GREY_800,
                    width=400,
                    height=100,
                    # ИСПРАВЛЕНО ДЛЯ FLET 1.0: ft.alignment.center -> ft.Alignment.CENTER
                    alignment=ft.Alignment.CENTER
                )
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=15
        )
    )

ft.run(main)