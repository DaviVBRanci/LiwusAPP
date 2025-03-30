from flask import (
    Flask,
    render_template,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    session,
    flash,
    abort,
)
from flask_socketio import SocketIO, emit, join_room
import os
from werkzeug.utils import secure_filename
import base64
from datetime import datetime, timedelta
import time
import hashlib
import sys
import json
from plyer import notification
from pywebpush import webpush, WebPushException
import flask_dance
import emoji
import math
from jinja2.sandbox import SandboxedEnvironment
import logging
import re
import redis
import twilio
from twilio.rest import Client
from flask_talisman import Talisman

CALL_HISTORY_TEMPLATE = 'CALL_HISTORY_TEMPLATE.html'
CALL_TEMPLATE = 'CALL_TEMPLATE.html'
CHAT_TEMPLATE = 'CHAT_TEMPLATE.html'
CONTACTS_TEMPLATE = 'CONTACTS_TEMPLATE.html'
LIG_TEMPLATE = 'LIG_TEMPLATE.html'
LOGIN_TEMPLATE = 'LOGIN_TEMPLATE.html'
REGISTER_TEMPLATE = 'REGISTER_TEMPLATE.html'


app = Flask(__name__)
app.config['SECRET_KEY'] = 'chavinha'
app.config['UPLOAD_FOLDER'] = 'static/profile_pics'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB max upload
socketio = SocketIO(app)
app.jinja_env.globals.update(__builtins__=None)  # Desativa SSTI!
subscriptions = {}


sys.path.insert(0, '/home/SEU_USUARIO/mysite')
# Ensure upload folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('static/audio', exist_ok=True)

# Store online users and their status
online_users = {}
user_status = {}  # 'online', 'offline', 'typing'



# Load users data from file
# Load users data from file
def load_users():
    users = {}
    try:
        with open('logins.txt', 'r') as f:
            for line in f.readlines():
                parts = line.strip().split(':')
                if len(parts) >= 2:
                    username = parts[0]
                    password = parts[1]  # Senha em texto normal
                    contacts = (
                        parts[2].split(',')
                        if len(parts) > 2 and parts[2]
                        else []
                    )
                    profile_pic = (
                        parts[3]
                        if len(parts) > 3 and parts[3]
                        else 'default.png'
                    )
                    users[username] = {
                        'password': password,  # Senha em texto normal
                        'contacts': contacts,
                        'profile_pic': profile_pic,
                    }
    except FileNotFoundError:
        pass
    return users


# Save users data to file
def save_users(users):
    """Salva os dados dos usuários em um arquivo 'logins.txt'."""
    with open('logins.txt', 'w') as f:
        for username, data in users.items():
            # A senha é armazenada em texto normal
            password = data['password']
            # Junta os contatos em uma string separada por vírgulas
            contacts = ','.join(data.get('contacts', []))
            # Obtém a foto de perfil ou usa a padrão
            profile_pic = data.get('profile_pic', 'default.png')
            # Escreve os dados do usuário no arquivo
            f.write(f'{username}:{password}:{contacts}:{profile_pic}\n')


# Load messages between users
def load_messages(user1, user2):
    chat_id = '_'.join(sorted([user1, user2]))
    messages = []
    try:
        messages_file = f'messages_{chat_id}.json'
        if os.path.exists(messages_file):
            with open(messages_file, 'r') as f:
                messages = json.load(f)  # Carrega mensagens do arquivo JSON
    except Exception as e:
        print(f'Error loading messages: {e}')
    return messages


# Save messages between users
def save_message(user1, user2, sender, message_type, content, read=False):
    chat_id = '_'.join(sorted([user1, user2]))
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    messages_file = f'messages_{chat_id}.json'

    # Carregar mensagens existentes
    messages = load_messages(user1, user2)

    # Adicionar nova mensagem
    messages.append({
        'timestamp': timestamp,
        'sender': sender,
        'type': message_type,
        'content': content,
        'read': read,  # Adiciona o status de leitura
    })
    # Salvar mensagens atualizadas de volta ao arquivo
    with open(messages_file, 'w') as f:
        json.dump(messages, f, indent=4)  # Salva como JSON formatado


def mark_messages_as_read(user1, user2):
    chat_id = '_'.join(sorted([user1, user2]))
    messages = load_messages(user1, user2)

    for message in messages:
        if message['sender'] == user2:  # Se a mensagem for do destinatário
            message['read'] = True  # Marca como lida

    # Salvar mensagens atualizadas de volta ao arquivo
    with open(f'messages_{chat_id}.json', 'w') as f:
        json.dump(messages, f, indent=4)


def load_groups():
    try:
        with open('groups.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}  # Retorna um dicionário vazio se o arquivo não existir
    except Exception as e:
        print(f'Erro ao carregar grupos: {e}')
        return {}


# Função para salvar grupos em um arquivo JSON
def save_groups(groups):
    try:
        with open('groups.json', 'w') as f:
            json.dump(
                groups, f, indent=4
            )  # Salva os grupos como JSON formatado
    except Exception as e:
        print(f'Erro ao salvar grupos: {e}')


def calculate_call_duration(caller):
    """Calcula a duração da chamada para o chamador especificado."""
    if caller not in call_start_times:
        call_start_times[caller] = (
            datetime.now()
        )  # Armazena o horário de início

    # Calcule a duração
    start_time = call_start_times.pop(caller, None)
    if start_time:
        duration = datetime.now() - start_time
        return str(duration)  # Retorna a duração como string
    return '00:00:00'


@app.route('/call_history')
def call_history():
    if 'username' not in session:
        return redirect(url_for('index'))

    history = load_call_history()  # Carrega o histórico de chamadas
    return render_template(
        CALL_HISTORY_TEMPLATE, history=history
    )  # Crie um template para exibir o histórico


def send_notification(username, title, message):
    subscription_info = subscriptions.get(username)

    if subscription_info:
        try:
            webpush(
                subscription_info=subscription_info,
                data=json.dumps({'title': title, 'body': message}),
                vapid_private_key='LS0tLS1CRUdJTiBFQyBQUklWQVRFIEtFWS0tLS0tCk1IY0NBUUVFSUZ6eTR6eUZkbUNka2FsUjRIRmh2Q2szaVdoNUNmcXRyZTJmQ1d4TVRBTE5vQW9HQ0NxR1NNNDkKQXdFSG9VUURRZ0FFNU5RTmZVQmc3UEdUTEYrYmRmWVVNZXVDY1FLdi9YYjhXbVc0VFhJcm9iQTduVUdxcENTZAp3bjV5THN3cDZ4em5jQ0VxdUVpdHJNUk5jZjQvaXlrSEFBPT0KLS0tLS1FTkQgRUMgUFJJVkFURSBLRVktLS0tLQo=',  # Substitua pela sua chave privada
                vapid_claims={
                    'sub': 'rancidavi@gmail.com'
                },  # Substitua pelo seu email
            )
        except WebPushException as ex:
            print(f'Failed to send notification: {ex}')


@app.route('/call/<contact>')
def call(contact):
    if 'username' not in session:
        return redirect(url_for('index'))

    username = session['username']
    users = load_users()

    if username in users and contact in users:
        call_type = request.args.get('type', 'video')
        is_initiator = request.args.get('initiator', 'false').lower() == 'true'

        return render_template(
            CALL_TEMPLATE,
            username=username,
            contact=contact,
            call_type=call_type,
            is_initiator=is_initiator,
            contact_pic=users[contact].get('profile_pic', 'default.png'),
        )

    return redirect(url_for('contacts'))


@socketio.on('call_request')
def handle_call_request(data):
    caller = session.get('username')
    target = data.get('target')
    call_type = data.get('type', 'video')

    if not caller or not target:
        return

    # Get caller's profile pic
    users = load_users()
    profile_pic = users.get(caller, {}).get('profile_pic', 'default.png')

    # Send call request to target
    emit(
        'call_request',
        {'caller': caller, 'type': call_type, 'profile_pic': profile_pic},
        room=target,
    )


@socketio.on('answer_call')
def handle_answer_call(data):
    responder = session.get('username')
    caller = data.get('caller')
    accepted = data.get('accepted', False)

    if not responder or not caller:
        return

    # Send response to caller
    emit(
        'call_answered',
        {'target': responder, 'accepted': accepted},
        room=caller,
    )


@socketio.on('cancel_call')
def handle_cancel_call(data):
    caller = session.get('username')
    target = data.get('target')

    if not caller or not target:
        return

    # Send cancellation to target
    emit('call_cancelled', {'caller': caller}, room=target)


@socketio.on('ice_candidate')
def handle_ice_candidate(data):
    sender = session.get('username')
    target = data.get('target')
    candidate = data.get('candidate')

    if not sender or not target or not candidate:
        return

    # Forward ICE candidate to target
    emit(
        'ice_candidate',
        {'sender': sender, 'candidate': candidate},
        room=target,
    )


@socketio.on('call_offer')
def handle_call_offer(data):
    sender = session.get('username')
    target = data.get('target')
    offer = data.get('offer')

    if not sender or not target or not offer:
        return

    # Forward offer to target
    emit('call_offer', {'sender': sender, 'offer': offer}, room=target)


@socketio.on('call_answer')
def handle_call_answer(data):
    sender = session.get('username')
    target = data.get('target')
    answer = data.get('answer')

    if not sender or not target or not answer:
        return

    # Forward answer to target
    emit('call_answer', {'sender': sender, 'answer': answer}, room=target)


@socketio.on('end_call')
def handle_end_call(data):
    sender = session.get('username')
    target = data.get('target')

    if not sender or not target:
        return

    # Calcule a duração da chamada apenas com o chamador
    duration = calculate_call_duration(sender)  # Passa apenas o 'sender'

    # Crie um registro de chamada
    call_record = {
        'caller': sender,
        'receiver': target,
        'start_time': datetime.now().isoformat(),  # Hora de início
        'duration': duration,
        'answered': data.get('answered', False),  # Se a chamada foi atendida
    }

    # Salve o registro de chamada
    save_call_history(call_record)

    # Notifique o destinatário que a chamada foi encerrada
    emit('call_ended', {'sender': sender}, room=target)


call_data = {}


@socketio.on('start_call')
def handle_start_call(data):
    sender = session.get('username')
    target = data.get('target')

    if not sender or not target:
        return

    # Armazene o horário de início da chamada
    call_data[sender] = {
        'target': target,
        'start_time': datetime.now(),
        'answered': False,  # Inicialmente, a chamada não foi atendida
    }


@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('contacts'))
    return render_template(LOGIN_TEMPLATE, messages=None)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()

        if not username or not password:
            return render_template(
                LOGIN_TEMPLATE,
                messages=['Por favor, preencha todos os campos.'],
            )

        users = load_users()
        if username in users and users[username]['password'] == password:
            session['username'] = username
            session['profile_pic'] = users[username].get(
                'profile_pic', 'default.png'
            )
            return redirect(url_for('contacts'))
        else:
            return render_template(
                LOGIN_TEMPLATE, messages=['Usuário ou senha incorretos.']
            )

    return redirect(url_for('index'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        confirm = request.form['confirm'].strip()

        if not username or not password or not confirm:
            return render_template(
                REGISTER_TEMPLATE,
                messages=['Por favor, preencha todos os campos.'],
            )

        if password != confirm:
            return render_template(
                REGISTER_TEMPLATE, messages=['As senhas não coincidem.']
            )

        users = load_users()
        if username in users:
            return render_template(
                REGISTER_TEMPLATE, messages=['Nome de usuário já existe.']
            )

        # Add new user
        users[username] = {
            'password': password,
            'contacts': [],
            'profile_pic': 'default.png',
        }
        save_users(users)

        return render_template(
            LOGIN_TEMPLATE, messages=['Cadastro realizado com sucesso!']
        )

    return render_template(REGISTER_TEMPLATE, messages=None)


@app.route('/logout')
def logout():
    if 'username' in session:
        username = session['username']
        if username in online_users:
            user_status[username] = 'offline'
            socketio.emit(
                'status_update',
                {'user': username, 'status': 'offline'},
                broadcast=True,
            )
        session.pop('username', None)
    return redirect(url_for('index'))


@app.route('/notify', methods=['POST'])
def notify():
    data = request.json
    action = data.get('action')  # 'call' ou 'message'
    username = data.get('username')
    caller_or_sender = data.get('caller_or_sender')

    if action == 'call':
        send_notification(
            username, 'Nova Chamada', f'{caller_or_sender} está chamando você!'
        )
    elif action == 'message':
        send_notification(username, 'Nova Mensagem', f' enviou uma mensagem.')

    return jsonify({'success': True}), 200


app.route('/subscribe', methods=['POST'])


def subscribe():
    subscription = request.json.get('subscription')
    username = request.json.get('username')  # Para identificar o usuário

    if subscription and username:
        subscriptions[username] = subscription  # Armazene a assinatura
        return jsonify({'success': True}), 201

    return jsonify({'success': False}), 400


@app.route('/contacts')
def contacts():
    if 'username' not in session:
        return redirect(url_for('index'))

    username = session['username']
    users = load_users()

    if username in users:
        user_contacts = users[username].get('contacts', [])
        contacts_data = []

        for contact in user_contacts:
            if contact:  # Skip empty contacts
                contact_status = user_status.get(contact, 'offline')
                contact_pic = users.get(contact, {}).get(
                    'profile_pic', 'default.png'
                )
                contacts_data.append({
                    'username': contact,
                    'status': contact_status,
                    'profile_pic': contact_pic,
                })

        return render_template(
            CONTACTS_TEMPLATE,
            username=username,
            contacts=contacts_data,
            profile_pic=users[username].get('profile_pic', 'default.png'),
        )

    return redirect(url_for('index'))


@app.route('/add_contact', methods=['POST'])
def add_contact():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'Não autenticado'})

    username = session['username']
    contact = request.form['contact'].strip()

    if not contact:
        return jsonify({
            'success': False,
            'message': 'Por favor, digite um nome de usuário.',
        })

    users = load_users()

    # Check if contact exists
    if contact not in users:
        return jsonify({
            'success': False,
            'message': f'Usuário {contact} não encontrado.',
        })

    # Check if trying to add self
    if contact == username:
        return jsonify({
            'success': False,
            'message': 'Você não pode adicionar a si mesmo como contato.',
        })

    # Add contact to user's contact list
    if username in users:
        if 'contacts' not in users[username]:
            users[username]['contacts'] = []

        if contact in users[username]['contacts']:
            return jsonify({
                'success': False,
                'message': f'Contato {contact} já existe na sua lista.',
            })

        users[username]['contacts'].append(contact)
        save_users(users)

        contact_status = user_status.get(contact, 'offline')
        contact_pic = users.get(contact, {}).get('profile_pic', 'default.png')

        return jsonify({
            'success': True,
            'message': f'Contato {contact} adicionado com sucesso!',
            'contact': {
                'username': contact,
                'status': contact_status,
                'profile_pic': contact_pic,
            },
        })

    return jsonify({'success': False, 'message': 'Erro ao adicionar contato.'})


@app.route('/chat/<contact>')
def chat(contact):
    if 'username' not in session:
        return redirect(url_for('index'))

    username = session['username']
    users = load_users()

    # Verifica se o contato está na lista de contatos do usuário
    if username in users and contact in users[username]['contacts']:
        try:
            messages = load_messages(username, contact)
            contact_status = user_status.get(contact, 'offline')

            # Marcar mensagens como lidas
            mark_messages_as_read(username, contact)

            # Formatar mensagens com timestamp atualizado, incluindo o status de leitura
            for message in messages:
                read_status = '✔️' if message['read'] else '✖️'
                message['timestamp'] = (
                    f'{message["timestamp"]}, visto por: {contact if message["read"] else "não visto"}'
                )

            # Notificação para Windows
            if contact_status == 'offline':
                notification.notify(
                    title='Contato Offline',
                    message=f'{contact} está offline. Sua mensagem será entregue quando ele estiver online.',
                    app_name='Messaging App',
                )

            return render_template(
                CHAT_TEMPLATE
                + """
                <script>
                // Solicitar permissão para mostrar notificações
                Notification.requestPermission().then(permission => {
                    if (permission === 'granted') {
                        console.log('Permissão para notificações concedida.');
                    }
                });

                

                // Socket.IO event listener for incoming calls
                socket.on('call_request', (data) => {
                    const caller = data.caller;
                    const incomingCallModal = document.createElement('div');
                    incomingCallModal.className = 'incoming-call-modal';
                    incomingCallModal.innerHTML = `
                        <div style="background: #fff; padding: 20px; border-radius: 5px; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 1000;">
                            <h3>Chamada recebida de ${caller}</h3>
                            <button id="answer-call">Atender</button>
                            <button id="decline-call">Recusar</button>
                        </div>
                    `;
                    document.body.appendChild(incomingCallModal);

                    // Lógica para atender ou recusar a chamada
                    document.getElementById('answer-call').onclick = () => {
                        socket.emit('answer_call', { caller: caller, accepted: true });
                        window.location.href = `/call/${caller}?type=video&initiator=false`; // Redireciona para a página de chamada
                    };

                    document.getElementById('decline-call').onclick = () => {
                        socket.emit('answer_call', { caller: caller, accepted: false });
                        document.body.removeChild(incomingCallModal); // Remove modal
                    };
                });
                </script>
            """,
                username=username,
                contact=contact,
                messages=messages,
                contact_status=contact_status,
                profile_pic=users[username].get('profile_pic', 'default.png'),
                contact_pic=users[contact].get('profile_pic', 'default.png'),
            )
        except Exception as e:
            print(f'Erro ao carregar mensagens: {e}')
            return redirect(url_for('contacts'))

    return redirect(url_for('contacts'))


@app.route('/remove_contact', methods=['POST'])
def remove_contact():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'Não autenticado'})

    username = session['username']
    contact_to_remove = request.json.get('contact')

    users = load_users()  # Carregar usuários do arquivo

    if username in users:
        if contact_to_remove in users[username]['contacts']:
            users[username]['contacts'].remove(
                contact_to_remove
            )  # Remove o contato
            save_users(users)  # Salva a lista de usuários atualizada
            return jsonify({
                'success': True,
                'message': 'Contato removido com sucesso!',
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Contato não encontrado.',
            })

    return jsonify({'success': False, 'message': 'Erro ao remover contato.'})


@app.route('/upload_profile_pic', methods=['POST'])
def upload_profile_pic():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'Não autenticado'})

    if 'profile_pic' not in request.files:
        return jsonify({'success': False, 'message': 'Nenhum arquivo enviado'})

    file = request.files['profile_pic']

    if file.filename == '':
        return jsonify({
            'success': False,
            'message': 'Nenhum arquivo selecionado',
        })

    if file:
        # Ensure filename is secure
        filename = secure_filename(file.filename)
        # Add username and timestamp to make filename unique
        unique_filename = (
            f'{session["username"]}_{int(time.time())}_{filename}'
        )
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)

        # Save the file
        file.save(file_path)

        # Update user profile in database
        users = load_users()
        if session['username'] in users:
            users[session['username']]['profile_pic'] = unique_filename
            save_users(users)
            session['profile_pic'] = unique_filename

            # Notify all contacts about profile pic update
            socketio.emit(
                'profile_pic_update',
                {'user': session['username'], 'profile_pic': unique_filename},
                broadcast=True,
            )

            return jsonify({
                'success': True,
                'message': 'Foto de perfil atualizada com sucesso!',
                'profile_pic': unique_filename,
            })

    return jsonify({
        'success': False,
        'message': 'Erro ao atualizar foto de perfil',
    })


@app.route('/save_audio', methods=['POST'])
def save_audio():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'Não autenticado'})

    username = session['username']
    contact = request.form.get('contact')
    audio_data = request.form.get('audio')

    if not audio_data or not contact:
        return jsonify({'success': False, 'message': 'Dados inválidos'})

    # Remove header from base64 data
    if ';base64,' in audio_data:
        audio_data = audio_data.split(';base64,')[1]

    # Generate a unique filename for the audio
    audio_filename = f'audio_{username}_{int(time.time())}.webm'
    audio_path = os.path.join('static/audio', audio_filename)

    # Save the audio file
    with open(audio_path, 'wb') as f:
        f.write(base64.b64decode(audio_data))

    # Save message reference
    save_message(username, contact, username, 'audio', audio_filename)

    # Emit message to recipient
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    socketio.emit(
        'new_message',
        {
            'sender': username,
            'receiver': contact,
            'type': 'audio',
            'content': audio_filename,
            'timestamp': timestamp,
        },
        room=contact,
    )

    return jsonify({
        'success': True,
        'message': 'Mensagem de áudio enviada',
        'audio_url': f'/static/audio/{audio_filename}',
    })


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'Nenhum arquivo selecionado'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        # Aqui você pode salvar a referência do arquivo no banco de dados ou em um arquivo
        return jsonify({
            'message': 'Arquivo enviado com sucesso!',
            'filename': filename,
        }), 200

    return jsonify({'error': 'Tipo de arquivo não permitido'}), 400


def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov'}
    return (
        '.' in filename
        and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    )


@app.route('/create_group', methods=['GET', 'POST'])
def create_group():
    if 'username' not in session:
        return redirect(url_for('index'))

    username = session['username']
    users = load_users()

    if request.method == 'POST':
        group_name = request.form.get('group_name', 'Group1')  # Nome do grupo
        members = request.form.getlist(
            'members'
        )  # Lista de membros a serem adicionados

        # Verificar se o grupo já existe
        groups = load_groups()
        if group_name not in groups:
            groups[group_name] = members
        else:
            groups[group_name].extend(members)

        save_groups(groups)  # Salva grupos atualizados

        # Adicionar o grupo à lista de contatos do usuário
        for member in members:
            if member in users:
                if 'contacts' not in users[member]:
                    users[member]['contacts'] = []
                if group_name not in users[member]['contacts']:
                    users[member]['contacts'].append(group_name)

        save_users(users)  # Salva usuários atualizados

        # Emitir uma mensagem para todos os membros do grupo
        socketio.emit(
            'new_group_message',
            {
                'sender': username,
                'group_name': group_name,
                'message': f'Grupo {group_name} criado com sucesso!',
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            },
        )

        # Emitir evento para que os membros se juntem à sala
        for member in members:
            socketio.emit(
                'join_group', {'group_name': group_name, 'member': member}
            )

        return redirect(
            url_for('chat_group', group_name=group_name)
        )  # Redireciona para o chat do grupo

    # Carregar contatos do usuário
    user_contacts = users.get(username, {}).get('contacts', [])
    contacts_data = []

    for contact in user_contacts:
        if contact:  # Ignorar contatos vazios
            contact_pic = users.get(contact, {}).get(
                'profile_pic', 'default.png'
            )
            contacts_data.append({
                'username': contact,
                'profile_pic': contact_pic,
            })

    # HTML para criar grupo
    GROUP_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Criar Grupo - Messaging App</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
                font-family: Arial, sans-serif;
            }

            body {
                background-color: #f0f2f5;
                color: #333;
                line-height: 1.6;
                height: 100vh;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
            }

            .container {
                max-width: 600px;
                background-color: #fff;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
            }

            h3 {
                margin-bottom: 20px;
                color: #128C7E;
                text-align: center;
            }

            select {
                width: 100%;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                margin-bottom: 20px;
                height: 150px; /* Altura para permitir múltiplas seleções */
                overflow-y: auto; /* Rolagem se necessário */
            }

            button {
                width: 100%;
                padding: 10px;
                background-color: #128C7E;
                color: #fff;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 16px;
            }

            button:hover {
                background-color: #0E7369;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <form method="POST" action="/create_group">
                <h3>Criar Novo Grupo</h3>
                <input type="text" name="group_name" placeholder="Nome do Grupo" required>
                <select name="members" multiple required>
                    {% for contact in contacts %}
                        <option value="{{ contact.username }}">{{ contact.username }}</option>
                    {% endfor %}
                </select>
                <button type="submit">Criar Grupo</button>
            </form>
        </div>
    </body>
    </html>
    """

    return render_template(
        GROUP_TEMPLATE, username=username, contacts=contacts_data
    )

    username = session['username']
    users = load_users()
    groups = load_groups()

    # Carregar mensagens do grupo (se houver)
    messages = load_messages(
        username, group_name
    )  # Carregar mensagens do grupo

    if request.method == 'POST':
        message = request.form.get('message')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Emitir a nova mensagem para todos os membros do grupo
        socketio.emit(
            'new_group_message',
            {
                'sender': username,
                'group_name': group_name,
                'message': message,
                'timestamp': timestamp,
            },
        )

        # Salvar a mensagem (opcional, se você quiser persistir mensagens)
        save_message(username, group_name, message, timestamp)

        return redirect(url_for('chat_group', group_name=group_name))

    # HTML para o chat do grupo
    CHAT_GROUP_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Chat do Grupo - {{ group_name }}</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
                font-family: Arial, sans-serif;
            }

            body {
                background-color: #f0f2f5;
                color: #333;
                line-height: 1.6;
            }

            .container {
                max-width: 800px;
                margin: 20px auto;
                background-color: #fff;
                border-radius: 8px;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
                padding: 20px;
            }

            h1 {
                text-align: center;
                color: #128C7E;
            }

            .messages-container {
                margin-top: 20px;
                max-height: 400px;
                overflow-y: auto;
                border: 1px solid #ddd;
                padding: 10px;
            }

            form {
                display: flex;
                margin-top: 10px;
            }

            input[type="text"] {
                flex: 1;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }

            button {
                padding: 10px;
                background-color: #128C7E;
                color: #fff;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                margin-left: 10px;
            }

            button:hover {
                background-color: #0E7369;
            }

            .contact-item {
                cursor: pointer;
                color: #128C7E;
                text-decoration: underline;
            }

            .contact-item:hover {
                color: #0E7369;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Chat do Grupo: {{ group_name }}</h1>
            <div class="messages-container" id="messages-container">
                {% for msg in messages %}
                    <div>
                        <strong>{{ msg.sender }}</strong>: {{ msg.content }} <em>{{ msg.timestamp }}</em>
                    </div>
                {% endfor %}
            </div>
            <form id="message-form" method="POST">
                <input type="text" name="message" placeholder="Digite sua mensagem..." required>
                <button type="submit">Enviar</button>
            </form>
            <div>
                <h3>Membros do Grupo</h3>
                {% for member in group_members %}
                    <div class="contact-item" onclick="window.location.href='http://127.0.0.1:5000/chat_group/{{ group_name|replace(' ', '%20') }}'">
                        {{ member }}
                    </div>
                {% endfor %}
            </div>
        </div>

        <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
        <script>
            const socket = io();
            const groupName = "{{ group_name }}";

            // Escutar novas mensagens de grupo
            socket.on('new_group_message', (data) => {
                const messagesContainer = document.getElementById('messages-container');
                const newMessage = document.createElement('div');
                newMessage.innerHTML = `<strong>${data.sender}</strong>: ${data.message} <em>${data.timestamp}</em>`;
                messagesContainer.appendChild(newMessage);
                messagesContainer.scrollTop = messagesContainer.scrollHeight;  // Rolagem automática
            });
        </script>
    </body>
    </html>
    """

    group_members = groups.get(group_name, [])
    return render_template(
        CHAT_GROUP_TEMPLATE,
        group_name=group_name,
        messages=messages,
        username=username,
        group_members=group_members,
    )


@socketio.on('send_group_message')
def handle_group_message(data):
    group_name = data['group_name']
    message = data['message']
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Emitir a nova mensagem para todos os membros do grupo
    socketio.emit(
        'new_group_message',
        {
            'sender': request.sid,  # Usar o SID do remetente
            'group_name': group_name,
            'message': message,
            'timestamp': timestamp,
        },
        room=group_name,
    )

    username = session['username']
    users = load_users()
    groups = load_groups()

    # Carregar mensagens do grupo (se houver)
    messages = load_messages(
        username, group_name
    )  # Carregar mensagens do grupo

    if request.method == 'POST':
        message = request.form.get('message')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Emitir a nova mensagem para todos os membros do grupo
        socketio.emit(
            'new_group_message',
            {
                'sender': username,
                'group_name': group_name,
                'message': message,
                'timestamp': timestamp,
            },
        )

        # Salvar a mensagem (opcional, se você quiser persistir mensagens)
        save_message(username, group_name, message, timestamp)

        return redirect(url_for('chat_group', group_name=group_name))

    # HTML para o chat do grupo
    CHAT_GROUP_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Chat do Grupo - {{ group_name }}</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
                font-family: Arial, sans-serif;
            }

            body {
                background-color: #f0f2f5;
                color: #333;
                line-height: 1.6;
            }

            .container {
                max-width: 800px;
                margin: 20px auto;
                background-color: #fff;
                border-radius: 8px;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
                padding: 20px;
            }

            h1 {
                text-align: center;
                color: #128C7E;
            }

            .messages-container {
                margin-top: 20px;
                max-height: 400px;
                overflow-y: auto;
                border: 1px solid #ddd;
                padding: 10px;
            }

            form {
                display: flex;
                margin-top: 10px;
            }

            input[type="text"] {
                flex: 1;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }

            button {
                padding: 10px;
                background-color: #128C7E;
                color: #fff;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                margin-left: 10px;
            }

            button:hover {
                background-color: #0E7369;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Chat do Grupo: {{ group_name }}</h1>
            <div class="messages-container" id="messages-container">
                {% for msg in messages %}
                    <div>
                        <strong>{{ msg.sender }}</strong>: {{ msg.content }} <em>{{ msg.timestamp }}</em>
                    </div>
                {% endfor %}
            </div>
            <form id="message-form" method="POST">
                <input type="text" name="message" placeholder="Digite sua mensagem..." required>
                <button type="submit">Enviar</button>
            </form>
        </div>

        <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
        <script>
            const socket = io();
            const groupName = "{{ group_name }}";

            // Escutar novas mensagens de grupo
            socket.on('new_group_message', (data) => {
                const messagesContainer = document.getElementById('messages-container');
                const newMessage = document.createElement('div');
                newMessage.innerHTML = `<strong>${data.sender}</strong>: ${data.message} <em>${data.timestamp}</em>`;
                messagesContainer.appendChild(newMessage);
                messagesContainer.scrollTop = messagesContainer.scrollHeight;  // Rolagem automática
            });
        </script>
    </body>
    </html>
    """

    return render_template(
        CHAT_GROUP_TEMPLATE,
        group_name=group_name,
        messages=messages,
        username=username,
    )


def load_call_history():
    try:
        with open('call_history.json', 'r') as f:
            return json.load(
                f
            )  # Carrega o histórico de chamadas do arquivo JSON
    except FileNotFoundError:
        return []  # Retorna uma lista vazia se o arquivo não existir
    except Exception as e:
        print(f'Erro ao carregar histórico de chamadas: {e}')
        return []


def save_call_history(call_record):
    history = load_call_history()  # Carrega o histórico existente
    history.append(call_record)  # Adiciona o novo registro de chamada

    with open('call_history.json', 'w') as f:
        json.dump(history, f, indent=4)  # Salva o histórico atualizado


@socketio.on('send_group_message')
def handle_group_message(data):
    group_name = data['group_name']
    message = data['message']
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Emitir a nova mensagem para todos os membros do grupo
    socketio.emit(
        'new_group_message',
        {
            'sender': request.sid,  # Usar o SID do remetente
            'group_name': group_name,
            'message': message,
            'timestamp': timestamp,
        },
        room=group_name,
    )


@socketio.on('connect')
def handle_connect():
    if 'username' not in session:
        return False  # Desconectar se não estiver autenticado


@socketio.on('send_group_message')
def handle_group_message(data):
    group_name = data['group_name']
    message = data['message']
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Emitir a nova mensagem para todos os membros do grupo
    socketio.emit(
        'new_group_message',
        {
            'sender': request.sid,  # Usar o SID do remetente
            'group_name': group_name,
            'message': message,
            'timestamp': timestamp,
        },
        room=group_name,
    )


@app.route('/send_media', methods=['POST'])
def send_media():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'Não autenticado'})

    username = session['username']
    recipient = request.form['recipient'].strip()
    media = request.files['media']

    if not recipient or not media:
        return jsonify({
            'success': False,
            'message': 'Destinatário ou arquivo não fornecido.',
        })

    # Salvar o arquivo
    media_folder = 'static/media'  # Pasta para armazenar mídias
    os.makedirs(media_folder, exist_ok=True)

    media_filename = secure_filename(media.filename)
    media_path = os.path.join(media_folder, media_filename)
    media.save(media_path)

    # Salvar mensagem com status de leitura padrão como False
    save_message(
        username, recipient, username, 'media', media_filename, read=False
    )

    # Emitir mensagem para o destinatário
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    socketio.emit(
        'new_message',
        {
            'sender': username,
            'receiver': recipient,
            'type': 'media',
            'content': media_filename,
            'timestamp': timestamp,
        },
        room=recipient,
    )

    return jsonify({
        'success': True,
        'message': 'Arquivo enviado com sucesso!',
    })


@app.route('/clear_chat/<contact>', methods=['POST'])
def clear_chat(contact):
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'Não autenticado'})

    username = session['username']
    chat_id = '_'.join(sorted([username, contact]))
    messages_file = f'messages_{chat_id}.json'

    # Limpar o arquivo de mensagens
    with open(messages_file, 'w') as f:
        json.dump([], f)  # Escreve um array vazio

    return jsonify({'success': True, 'message': 'Conversa limpa com sucesso!'})


@socketio.on('send_group_message')
def handle_group_message(data):
    group_name = data['group_name']
    message = data['message']
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Emitir a nova mensagem para todos os membros do grupo
    socketio.emit(
        'new_group_message',
        {
            'sender': request.sid,  # Usar o SID do remetente
            'group_name': group_name,
            'message': message,
            'timestamp': timestamp,
        },
        room=group_name,
    )


@app.route('/chat_group/<group_name>', methods=['GET'])
def chat_group(group_name):
    if 'username' not in session:
        return redirect(url_for('index'))

    username = session['username']
    users = load_users()
    groups = load_groups()

    # Carregar mensagens do grupo (se houver)
    messages = load_messages(
        username, group_name
    )  # Carregar mensagens do grupo

    # HTML para o chat do grupo
    CHAT_GROUP_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Chat do Grupo - {{ group_name }}</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
                font-family: Arial, sans-serif;
            }

            body {
                background-color: #f0f2f5;
                color: #333;
                line-height: 1.6;
            }

            .container {
                max-width: 800px;
                margin: 20px auto;
                background-color: #fff;
                border-radius: 8px;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
                padding: 20px;
            }

            h1 {
                text-align: center;
                color: #128C7E;
            }

            .messages-container {
                margin-top: 20px;
                max-height: 400px;
                overflow-y: auto;
                border: 1px solid #ddd;
                padding: 10px;
            }

            form {
                display: flex;
                margin-top: 10px;
            }

            input[type="text"] {
                flex: 1;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }

            button {
                padding: 10px;
                background-color: #128C7E;
                color: #fff;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                margin-left: 10px;
            }

            button:hover {
                background-color: #0E7369;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Chat do Grupo: {{ group_name }}</h1>
            <div class="messages-container" id="messages-container">
                {% for msg in messages %}
                    <div>
                        <strong>{{ msg.sender }}</strong>: {{ msg.content }} <em>{{ msg.timestamp }}</em>
                    </div>
                {% endfor %}
            </div>
            <form id="message-form" method="POST">
                <input type="text" name="message" placeholder="Digite sua mensagem..." required>
                <button type="submit">Enviar</button>
            </form>
        </div>

        <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
        <script>
            const socket = io();
            const groupName = "{{ group_name }}";

            // Escutar novas mensagens de grupo
            socket.on('new_group_message', (data) => {
                const messagesContainer = document.getElementById('messages-container');
                const newMessage = document.createElement('div');
                newMessage.innerHTML = `<strong>${data.sender}</strong>: ${data.message} <em>${data.timestamp}</em>`;
                messagesContainer.appendChild(newMessage);
                messagesContainer.scrollTop = messagesContainer.scrollHeight;  // Rolagem automática
            });

            // Enviar mensagem
            document.getElementById('message-form').onsubmit = function(event) {
                event.preventDefault();
                const messageInput = this.querySelector('input[name="message"]');
                const message = messageInput.value;

                // Emitir a nova mensagem
                socket.emit('send_group_message', {
                    group_name: groupName,
                    message: message
                });

                messageInput.value = '';  // Limpar campo de entrada
            };
        </script>
    </body>
    </html>
    """

    return render_template(
        CHAT_GROUP_TEMPLATE,
        group_name=group_name,
        messages=messages,
        username=username,
    )


@socketio.on('send_group_message')
def handle_group_message(data):
    group_name = data['group_name']
    message = data['message']
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Emitir a nova mensagem para todos os membros do grupo
    socketio.emit(
        'new_group_message',
        {
            'sender': request.sid,  # Usar o SID do remetente
            'group_name': group_name,
            'message': message,
            'timestamp': timestamp,
        },
        room=group_name,
    )


@app.route('/rename_group/<old_name>', methods=['POST'])
def rename_group(old_name):
    if 'username' not in session:
        return redirect(url_for('index'))

    new_name = request.form.get('new_name')
    groups = load_groups()

    if old_name in groups and new_name:
        groups[new_name] = groups.pop(old_name)  # Renomeia o grupo

    save_groups(groups)  # Salva grupos atualizados
    return redirect(url_for('contacts'))  # Redireciona após renomear


# Socket.IO events
@socketio.on('send_file')
def handle_send_file(data):
    sender = session.get('username')
    receiver = data.get('receiver')
    file = data.get('file')  # O arquivo deve ser enviado como base64

    if not sender or not receiver or not file:
        return

    # Salvar o arquivo
    filename = f'{sender}_{int(time.time())}_{file["filename"]}'
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    with open(file_path, 'wb') as f:
        f.write(base64.b64decode(file['content']))

    # Emitir a mensagem para o destinatário
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    emit(
        'new_file_message',
        {
            'sender': sender,
            'receiver': receiver,
            'filename': filename,
            'timestamp': timestamp,
        },
        room=receiver,
    )


@socketio.on('send_group_message')
def handle_group_message(data):
    group_name = data['group_name']
    message = data['message']
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    groups = {}
    # Verifica se o grupo existe
    if group_name in groups:
        # Emite a nova mensagem para todos os membros do grupo
        emit(
            'new_group_message',
            {
                'group_name': group_name,
                'message': message,
                'timestamp': timestamp,
            },
            room=group_name,
        )  # Envia para todos na sala do grupo


@socketio.on('connect')
def handle_connect():
    if 'username' in session:
        username = session['username']
        online_users[username] = request.sid
        user_status[username] = 'online'
        join_room(username)  # Join a room with the user's name
        emit(
            'status_update',
            {'user': username, 'status': 'online'},
            broadcast=True,
        )


@socketio.on('disconnect')
def handle_disconnect():
    if 'username' in session:
        username = session['username']
        if username in online_users:
            online_users.pop(username)
            user_status[username] = 'offline'
            emit(
                'status_update',
                {'user': username, 'status': 'offline'},
                broadcast=True,
            )


@socketio.on('send_message')
def handle_message(data):
    sender = session.get('username')
    receiver = data.get('receiver')
    message = data.get('message')
    message_type = data.get('type', 'text')

    if not sender or not receiver or not message:
        return

    # Save message to file
    save_message(sender, receiver, sender, message_type, message)

    # Send to receiver if online
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    emit(
        'new_message',
        {
            'sender': sender,
            'type': message_type,
            'content': message,
            'timestamp': timestamp,
        },
        room=receiver,
    )

    # Send confirmation back to sender
    emit(
        'message_sent',
        {
            'receiver': receiver,
            'type': message_type,
            'content': message,
            'timestamp': timestamp,
        },
    )


@socketio.on('typing')
def handle_typing(data):
    sender = session.get('username')
    receiver = data.get('receiver')
    is_typing = data.get('typing', False)

    if not sender or not receiver:
        return

    status = 'typing' if is_typing else 'online'
    user_status[sender] = status

    emit('status_update', {'user': sender, 'status': status}, room=receiver)


if __name__ == '__main__':
    # Create default profile pic if it doesn't exist
    default_pic_path = os.path.join(app.config['UPLOAD_FOLDER'], 'default.png')
    app.run(debug=True, host='0.0.0.0', port=5000)
