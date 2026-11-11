import flet as ft
import requests

def main(page: ft.Page):
    page.title = "Капри Remote"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 20
    
    # Поле IP-адреса ПК
    ip_field = ft.TextField(
        label="IP-адрес ПК",
        value="127.0.0.1",
        width=400,
        text_size=16
    )
    
    # Поле для ввода команды
    command_field = ft.TextField(
        label="Команда для Капри",
        hint_text="Например: какая погода?",
        width=400,
        text_size=16,
        on_submit=lambda e: send_command(e)
    )
    
    # Область для отображения ответа
    response_text = ft.Text(
        "Здесь будет ответ Капри...",
        size=16,
        color=ft.Colors.GREY_400
    )
    
    # Индикатор загрузки
    progress = ft.ProgressRing(visible=False)
    
    # Функция отправки команды (синхронная)
    def send_command(e):
        command = command_field.value.strip()
        if not command:
            return
        
        # Показываем индикатор загрузки
        response_text.value = "⏳ Отправка..."
        response_text.color = ft.Colors.YELLOW
        progress.visible = True
        page.update()
        
        try:
            ip = ip_field.value.strip()
            url = f"http://{ip}:5000/command"
            
            resp = requests.post(
                url, 
                json={"command": command}, 
                timeout=5
            )
            data = resp.json()
            
            if data.get("success"):
                response_text.value = f"✅ Отправлено: {command}"
                response_text.color = ft.Colors.GREEN
            else:
                response_text.value = f"❌ Ошибка: {data.get('error')}"
                response_text.color = ft.Colors.RED
                
        except requests.exceptions.ConnectionError:
            response_text.value = "❌ Не удалось подключиться к ПК. Проверь IP и запущен ли сервер."
            response_text.color = ft.Colors.RED
        except Exception as ex:
            response_text.value = f"❌ Ошибка: {ex}"
            response_text.color = ft.Colors.RED
        finally:
            progress.visible = False
            command_field.value = ""
            page.update()
    
    # Кнопка отправки
    send_btn = ft.ElevatedButton(
        "Отправить",
        on_click=send_command,
        width=200,
        height=50,
        style=ft.ButtonStyle(
            color=ft.Colors.WHITE,
            bgcolor=ft.Colors.BLUE_700
        )
    )
    
    # Размещаем элементы
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
                ft.Divider(height=30, color=ft.Colors.TRANSPARENT),
                ft.Container(
                    content=response_text,
                    padding=15,
                    border_radius=10,
                    bgcolor=ft.Colors.GREY_800,
                    width=400,
                    height=100
                )
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=15
        )
    )

ft.app(target=main)