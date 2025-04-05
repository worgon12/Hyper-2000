#!/bin/bash
# Installationsskript für Debian (ohne virtuelles Environment)
# Dieses Skript installiert alle benötigten Pakete, ruft (falls nötig) Zugangsdaten ab
# und richtet einen systemd-Dienst ein, der deine Anwendung dauerhaft im Hintergrund startet.
#
# Hinweis: Dieses Skript benötigt sudo-Rechte. Führe es aus mit:
#    sudo ./install.sh

# Aktualisiere den Paketindex und installiere benötigte Systempakete
echo "Aktualisiere Paketindex und installiere Systempakete..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip jq curl

# Funktion zum Abrufen der Zugangsdaten via API
function get_credentials() {
  read -p "Bitte Hub Seriennummer eingeben: " HUB_SN
  read -p "Bitte E-Mail-Adresse eingeben: " EMAIL

  echo "Sende API-Anfrage an Zendure..."
  RESPONSE=$(curl -s -X POST -H "Content-Type: application/json" \
    -d "{\"snNumber\":\"$HUB_SN\",\"account\":\"$EMAIL\"}" \
    https://app.zendure.tech/eu/developer/api/apply)

  SUCCESS=$(echo "$RESPONSE" | jq -r '.success')
  if [ "$SUCCESS" != "true" ]; then
    echo "API-Anfrage fehlgeschlagen. Antwort:"
    echo "$RESPONSE"
    exit 1
  fi

  APPKEY=$(echo "$RESPONSE" | jq -r '.data.appKey')
  SECRET=$(echo "$RESPONSE" | jq -r '.data.secret')

  if [ -z "$APPKEY" ] || [ -z "$SECRET" ] || [ "$APPKEY" == "null" ] || [ "$SECRET" == "null" ]; then
    echo "Fehler beim Extrahieren von appKey oder secret."
    exit 1
  fi

  echo "$APPKEY" > login.txt
  echo "$SECRET" >> login.txt
  echo "API-Anfrage erfolgreich. AppKey und Secret wurden in login.txt gespeichert."
}

# Prüfe, ob login.txt bereits existiert
if [ -f "login.txt" ]; then
  echo "login.txt wurde bereits gefunden."
  read -p "Möchtest du die vorhandenen Zugangsdaten verwenden? (j/n): " USE_EXISTING
  if [ "$USE_EXISTING" != "j" ] && [ "$USE_EXISTING" != "J" ]; then
    echo "Neue Zugangsdaten werden abgerufen..."
    get_credentials
  else
    echo "Verwende bestehende Zugangsdaten aus login.txt."
  fi
else
  echo "Keine Zugangsdaten gefunden. Neue Daten werden abgerufen..."
  get_credentials
fi

# Installiere die benötigten Python-Pakete global (mit --break-system-packages, um PEP 668-Konflikte zu vermeiden)
echo "Installiere Python-Abhängigkeiten global..."
sudo pip3 install --break-system-packages Flask Flask-SocketIO paho-mqtt eventlet

# Erstelle systemd-Service-Datei
SERVICE_FILE="/etc/systemd/system/hyper2000.service"
echo "Erstelle systemd-Service-Datei in $SERVICE_FILE ..."
sudo bash -c "cat > $SERVICE_FILE <<EOF
[Unit]
Description=Hyper2000 Flask/SocketIO App
After=network.target

[Service]
User=$(whoami)
WorkingDirectory=$(pwd)
ExecStart=python3 $(pwd)/app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF"

# Systemd-Daemon neu laden, Service aktivieren und starten
echo "Lade systemd-Daemon neu und aktiviere den Service..."
sudo systemctl daemon-reload
sudo systemctl enable hyper2000.service
sudo systemctl start hyper2000.service

# Lese die lokale IP-Adresse aus (wähle die erste, falls mehrere vorhanden)
IP=$(hostname -I | awk '{print $1}')
echo "Installation abgeschlossen!"
echo "Die Anwendung läuft nun dauerhaft als systemd-Service."
echo "Die Weboberfläche ist erreichbar unter: http://$IP:3211"

