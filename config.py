# =====================================================
# Konfigurationsdatei für den Abmeldungs-Bot
# =====================================================
# Hier werden alle wichtigen IDs und der Bot-Token zentral gespeichert.
# Die Variablen werden in main.py importiert.
# WICHTIG: Diese Datei sollte niemals öffentlich geteilt werden,
# da der Token vollen Zugriff auf den Bot gewährt.
# =====================================================

# Der geheime Bot-Token aus dem Discord Developer Portal.
# Mit diesem Token meldet sich der Bot bei Discord an.
TOKEN = "MTQ5NzEyNzk2NzQ0MzEyNDMxNA.G21_YY.ZYcpxgoI1kgi2K0OHJ9LjsXdvv_Y1sLBJeAfxw"

# Liste der Rollen-IDs, deren Mitglieder den /abmelden Befehl benutzen dürfen.
# Diese Personen dürfen sich NUR selbst abmelden.
ALLOWED_ROLES = [1481549011369459822]

# Liste der Rollen-IDs der Leitung.
# Diese Personen dürfen Abmeldungen für andere Mitglieder erstellen,
# Abmeldungen bearbeiten/entfernen, Statistiken einsehen
# und die Zurückmelden-Buttons anderer Mitglieder benutzen.
LEITUNG_ROLES = [
	1481549011369459822
]

# ID des Channels, in den die Abmeldungs-Embeds gepostet werden.
ABMELDUNGEN_CHANNEL_ID = 1481548120641765391

# ID des Channels, in dem das Live-Dashboard angezeigt wird.
DASHBOARD_CHANNEL_ID = 1481548120641765392

# ID des Log-Channels. Hier werden alle Aktionen protokolliert.
LOG_CHANNEL_ID = 1481548120641765392  # Hier die Channel-ID eintragen

# ID der Teamleiter-Rolle. Diese Rolle wird im Abmeldungs-Channel
# bei jeder neuen Abmeldung gepingt.
TEAMLEITER_ROLE_ID = 1481549011369459822  # Hier die Rollen-ID eintragen

# Maximale Dauer einer Abmeldung in Tagen.
MAX_ABMELDUNG_DAYS = 30