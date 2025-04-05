import json
import threading
import sqlite3
import datetime
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import paho.mqtt.client as mqtt

# --- Funktion zum Einlesen der Zugangsdaten ---
def read_credentials(file_path):
    """
    Liest die Zugangsdaten aus der angegebenen Textdatei.
    Die Datei sollte in der ersten Zeile den Benutzernamen und in der zweiten Zeile das Passwort enthalten.
    """
    with open(file_path, 'r') as f:
        lines = f.read().splitlines()
    if len(lines) < 2:
        raise ValueError("Die Datei muss mindestens zwei Zeilen enthalten: Username und Passwort.")
    return lines[0].strip(), lines[1].strip()

# Lese die Zugangsdaten aus der Datei "login.txt"
username, password = read_credentials('login.txt')

# MQTT Broker-Daten
broker = "mqtt-eu.zen-iot.com"
port = 1883

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app)

# Globale Variable zum Speichern der aktuellen MQTT-Daten
mqtt_data = {}
connected_once = False

# Definiere, welche Felder in der DB gespeichert werden sollen.
# Hier alle, die du im "Systemdaten"-Bereich (und ggf. Akku-Bereich) brauchst:
system_keys = [
    'sn',
    'hyperTmp',
    'wifiState',
    'buzzerSwitch',
    'masterSwitch',
    'hubState',
    'remainInputTime',
    'heatState',
    'inverseMaxPower',
    'inputLimit',
    'outputLimit',
    'acMode',
    'packState',
    'packData',
    'packNum'
    # Wenn du willst, kannst du hier weitere Felder ergänzen
]

# --- Akkustatus & Verbrauchsberechnung (optional, je nach Bedarf) ---
def compute_battery_status(data):
    """
    Bestimmt den Akkustatus anhand der empfangenen Werte.
    """
    acOutput = data.get("acOutputPower", 0)
    if acOutput > 0:
        return "Bypass"
    
    pack_state = data.get("packState", None)
    if pack_state == 1:
        return "Laden"
    elif pack_state == 2:
        return "Entladen"
    else:
        return "Standby"

def compute_consumption(data, status):
    """
    Ermittelt den Verbrauch abhängig vom Akkustatus.
    """
    if status == "Laden":
        return data.get("packInputPower", 0)
    elif status == "Entladen":
        return data.get("outputHomePower", 0)
    else:
        return 0

# --- SQLite Datenbank-Funktionen ---
def init_db():
    """Initialisiert die SQLite-Datenbank und erstellt die Tabelle, falls sie nicht existiert."""
    conn = sqlite3.connect('mqtt_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            data TEXT
        )
    ''')
    conn.commit()
    conn.close()

def store_system_data(data):
    """
    Speichert einen Subset der Daten (alle system_keys) in der DB.
    Außerdem werden Zellspannungsdaten aus packData extrahiert und als cellVoltages gespeichert.
    """
    # Nur die Keys speichern, die in system_keys definiert sind
    store_dict = { key: data.get(key) for key in system_keys }

    # Zellspannungsdaten, falls sie in packData stehen
    if 'packData' in data and isinstance(data['packData'], list) and len(data['packData']) > 0:
        first_pack = data['packData'][0]
        # Falls du 'cellVoltages' ebenfalls speichern willst, kannst du es hier übernehmen
        # oder Felder wie 'maxVol', 'minVol', 'totalVol' direkt in store_dict ablegen.
        # Beispiel:
        if 'maxVol' in first_pack:
            store_dict.setdefault('cellVoltages', {})
            store_dict['cellVoltages']['max'] = first_pack['maxVol']
        if 'minVol' in first_pack:
            store_dict.setdefault('cellVoltages', {})
            store_dict['cellVoltages']['min'] = first_pack['minVol']
        if 'totalVol' in first_pack:
            store_dict.setdefault('cellVoltages', {})
            store_dict['cellVoltages']['total'] = first_pack['totalVol']

    timestamp = datetime.datetime.now().isoformat()
    raw_data = json.dumps(store_dict)

    conn = sqlite3.connect('mqtt_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO system_data (timestamp, data)
        VALUES (?, ?)
    ''', (timestamp, raw_data))
    conn.commit()

    # Begrenze auf die letzten 50 Einträge (Beispiel, kann man ändern)
    cursor.execute('SELECT COUNT(*) FROM system_data')
    count = cursor.fetchone()[0]
    if count > 50:
        cursor.execute('''
            DELETE FROM system_data
            WHERE id NOT IN (
                SELECT id FROM system_data
                ORDER BY id DESC
                LIMIT 50
            )
        ''')
        conn.commit()

    conn.close()

def load_system_data():
    """
    Lädt beim Start der Anwendung die zuletzt gespeicherten Systemdaten aus der DB
    und gibt sie als Dictionary zurück.
    """
    conn = sqlite3.connect('mqtt_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT data FROM system_data ORDER BY id DESC LIMIT 1')
    row = cursor.fetchone()
    data = {}
    if row:
        try:
            data = json.loads(row[0])
            print("Geladene Systemdaten aus DB:", data)
        except Exception as e:
            print("Fehler beim Laden der Systemdaten:", e)
    conn.close()
    return data

# --- MQTT Callback-Funktionen ---
def on_connect(client, userdata, flags, rc):
    global connected_once
    if rc == 0:
        if not connected_once:
            print("Erfolgreich mit MQTT-Broker verbunden!")
            connected_once = True
        # Abonniere alle "state"-Topics unter dem Benutzernamen, z.B. "username/+/state"
        client.subscribe(username + '/+/state')
    else:
        print("Verbindungsfehler, Rückgabecode:", rc)

def on_message(client, userdata, msg):
    global mqtt_data
    try:
        # JSON-Daten parsen
        data = json.loads(msg.payload.decode("utf-8"))

        # Merge in die globalen Daten
        mqtt_data.update(data)

        # Akkustatus + Verbrauch neu berechnen (optional)
        akkustatus = compute_battery_status(mqtt_data)
        verbrauch = compute_consumption(mqtt_data, akkustatus)
        mqtt_data["akkustatus"] = akkustatus
        mqtt_data["verbrauch"] = verbrauch

        print("Neue MQTT-Daten:", mqtt_data)

        # An alle SocketIO-Clients senden
        socketio.emit('mqtt_update', mqtt_data)

        # Relevante Systemdaten (und Akku-Daten) in DB speichern
        store_system_data(mqtt_data)

    except Exception as e:
        print("Fehler beim Verarbeiten der MQTT-Nachricht:", e)

# --- MQTT Client Setup ---
mqtt_client = mqtt.Client(client_id="SolarFlowClient")
mqtt_client.username_pw_set(username, password)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(broker, port, keepalive=60)

def mqtt_loop():
    mqtt_client.loop_forever()

mqtt_thread = threading.Thread(target=mqtt_loop)
mqtt_thread.daemon = True
mqtt_thread.start()

# --- Flask-Routen ---
@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_request_update():
    # Beim ersten Verbinden gleich die aktuellen Daten schicken
    if mqtt_data:
        emit('mqtt_update', mqtt_data)

if __name__ == '__main__':
    # DB initialisieren
    init_db()

    # Letzte gespeicherte Systemdaten laden
    stored_data = load_system_data()

    # In mqtt_data mergen
    mqtt_data.update(stored_data)

    # Flask-SocketIO starten
    socketio.run(app, host='0.0.0.0', port=3211)
