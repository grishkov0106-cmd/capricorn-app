from flask import Flask, request, jsonify
from flask_cors import CORS
import threading

app = Flask(__name__)
CORS(app)

# Очередь команд, которые будет читать Капри
command_queue = []

@app.route('/command', methods=['POST'])
def receive_command():
    """Принимает команду от телефона"""
    data = request.json
    command = data.get('command', '').strip()
    
    if not command:
        return jsonify({'success': False, 'error': 'Пустая команда'})
    
    # Добавляем команду в очередь для Капри
    command_queue.append(command)
    print(f"📱 Получена команда с телефона: {command}")
    
    return jsonify({'success': True, 'message': 'Команда принята'})

@app.route('/queue', methods=['GET'])
def get_queue():
    """Капри забирает отсюда команды"""
    if command_queue:
        cmd = command_queue.pop(0)
        return jsonify({'command': cmd})
    return jsonify({'command': None})

def start_server():
    """Запуск сервера в фоновом потоке"""
    # host='0.0.0.0' означает, что сервер доступен всем в твоей локальной сети Wi-Fi
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if __name__ == '__main__':
    print("🌐 Сервер для телефона запущен на порту 5000")
    start_server()