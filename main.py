"""
Abmeldungs-Bot für Discord
==========================
Stellt Slash-Befehle zum Verwalten von Abmeldungen bereit:
- /abmelden, /abmeldung_bearbeiten, /abmeldung_entfernen, /statistik
- Persistentes Live-Dashboard mit Fortschrittsanzeige
- Automatische Erinnerungen, Log-Channel, JSON-Persistenz
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config

# =====================================================
# Bot-Initialisierung
# =====================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = False  # Wird für Slash-Commands nicht benötigt.

bot = commands.Bot(command_prefix="!", intents=intents)

# =====================================================
# Datenhaltung
# =====================================================
# Aktive Abmeldungen: message_id -> Datensatz.
abmeldungen: dict[int, dict] = {}

# Statistik je Benutzer: user_id -> {"anzahl": int, "tage": float}.
statistik: dict[int, dict] = {}

# ID der Dashboard-Nachricht im Dashboard-Channel.
dashboard_message_id: int | None = None

# Geplante Abmeldungen, die in der Zukunft starten (key = interne Plan-ID).
geplante_abmeldungen: dict[str, dict] = {}

# Pfad zur Persistenz-Datei (liegt neben main.py).
DATEN_PFAD = os.path.join(os.path.dirname(__file__), "abmeldungen.json")


# =====================================================
# Persistenz: Speichern und Laden
# =====================================================
def speichere_daten() -> None:
    """Schreibt alle Abmeldungen, Statistiken und die Dashboard-ID in JSON."""
    daten = {
        "abmeldungen": {str(mid): eintrag for mid, eintrag in abmeldungen.items()},
        "statistik": {str(uid): werte for uid, werte in statistik.items()},
        "dashboard_message_id": dashboard_message_id,
        "geplante_abmeldungen": geplante_abmeldungen,
    }
    try:
        with open(DATEN_PFAD, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️  Fehler beim Speichern: {e}")


def lade_daten() -> None:
    """Lädt vorhandene Daten beim Start des Bots."""
    global dashboard_message_id

    if not os.path.exists(DATEN_PFAD):
        return

    try:
        with open(DATEN_PFAD, "r", encoding="utf-8") as f:
            daten = json.load(f)
    except Exception as e:
        print(f"⚠️  Fehler beim Laden: {e}")
        return

    for mid, eintrag in daten.get("abmeldungen", {}).items():
        abmeldungen[int(mid)] = eintrag

    for uid, werte in daten.get("statistik", {}).items():
        statistik[int(uid)] = werte

    for plan_id, eintrag in daten.get("geplante_abmeldungen", {}).items():
        geplante_abmeldungen[str(plan_id)] = eintrag

    dashboard_message_id = daten.get("dashboard_message_id")
    print(
        f"📂 {len(abmeldungen)} aktive und "
        f"{len(geplante_abmeldungen)} geplante Abmeldung(en) geladen."
    )


# =====================================================
# Hilfsfunktionen: Rollen
# =====================================================
def hat_rolle_aus(member: discord.abc.User, rollen_liste: list[int]) -> bool:
    """Prüft, ob ein Mitglied mindestens eine Rolle aus der Liste besitzt."""
    if not isinstance(member, discord.Member):
        return False
    member_role_ids = {rolle.id for rolle in member.roles}
    return any(rid in member_role_ids for rid in rollen_liste)


def ist_leitung(member: discord.abc.User) -> bool:
    """True, wenn das Mitglied eine Leitungsrolle besitzt."""
    return hat_rolle_aus(member, config.LEITUNG_ROLES)


def ist_berechtigt(member: discord.abc.User) -> bool:
    """True, wenn ALLOWED_ROLES oder LEITUNG_ROLES vorhanden ist."""
    return hat_rolle_aus(member, config.ALLOWED_ROLES) or ist_leitung(member)


# =====================================================
# Hilfsfunktionen: Dauer parsen und Fortschritt
# =====================================================
def parse_dauer_in_tage(dauer: str) -> float | None:
    """Versucht, einen Dauer-String in eine Anzahl Tage umzuwandeln.

    Unterstützte Beispiele: "3 Tage", "1 Tag", "2 Wochen", "1 Monat", "5".
    Gibt None zurück, wenn nichts erkennbar ist.
    """
    if not dauer:
        return None

    text = dauer.lower().strip()
    treffer = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if not treffer:
        return None

    try:
        zahl = float(treffer.group(1).replace(",", "."))
    except ValueError:
        return None

    # Einheit erkennen.
    if "stunde" in text or "std" in text:
        return zahl / 24.0
    if "woche" in text:
        return zahl * 7.0
    if "monat" in text:
        return zahl * 30.0
    if "jahr" in text:
        return zahl * 365.0
    # Standard: Tage.
    return zahl


def fortschrittsbalken(start_iso: str, dauer_tage: float | None) -> str:
    """Erzeugt einen 10-stelligen Balken inkl. Prozent und Resttext."""
    if not start_iso or not dauer_tage or dauer_tage <= 0:
        return "░░░░░░░░░░ —"

    try:
        start = datetime.fromisoformat(start_iso)
    except ValueError:
        return "░░░░░░░░░░ —"

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    jetzt = datetime.now(timezone.utc)
    vergangen = (jetzt - start).total_seconds()
    gesamt = dauer_tage * 86400.0
    anteil = max(0.0, min(1.0, vergangen / gesamt))
    bloecke = int(round(anteil * 10))
    balken = "█" * bloecke + "░" * (10 - bloecke)
    prozent = int(round(anteil * 100))

    if anteil >= 1.0:
        zusatz = "abgelaufen"
    else:
        rest_sek = max(0, gesamt - vergangen)
        rest_tage = rest_sek / 86400.0
        if rest_tage >= 1:
            zusatz = f"noch {rest_tage:.1f} Tag(e)"
        else:
            rest_std = rest_sek / 3600.0
            zusatz = f"noch {rest_std:.1f} Std."

    return f"{balken} {prozent}% ({zusatz})"


# =====================================================
# Farb- und Stil-Konstanten
# =====================================================
FARBE_ABMELDUNG = discord.Color.from_rgb(220, 53, 69)      # kräftiges Rot
FARBE_ZURUECK = discord.Color.from_rgb(40, 167, 69)        # frisches Grün
FARBE_DASHBOARD = discord.Color.from_rgb(59, 130, 246)     # ruhiges Blau
FARBE_DASHBOARD_LEER = discord.Color.from_rgb(34, 197, 94) # Grün bei "alle anwesend"
FARBE_ERINNERUNG = discord.Color.from_rgb(249, 115, 22)    # warmes Orange
FARBE_BESTAETIGUNG = discord.Color.from_rgb(59, 130, 246)  # Blau
FARBE_LOG_NEU = discord.Color.from_rgb(220, 53, 69)        # Rot
FARBE_LOG_ZURUECK = discord.Color.from_rgb(40, 167, 69)    # Grün
FARBE_LOG_BEARBEITEN = discord.Color.from_rgb(234, 179, 8) # Gelb
FARBE_LOG_ENTFERNEN = discord.Color.from_rgb(75, 85, 99)   # Dunkelgrau


def _bot_footer(embed: discord.Embed) -> discord.Embed:
    """Setzt einen einheitlichen Footer mit Bot-Name und Icon."""
    if bot.user:
        embed.set_footer(
            text=f"{bot.user.name} • Abmeldungs-System",
            icon_url=bot.user.display_avatar.url,
        )
    else:
        embed.set_footer(text="Abmeldungs-System")
    return embed


# =====================================================
# Embed-Builder
# =====================================================
def baue_abmeldungs_embed(eintrag: dict, zurueckgemeldet: bool = False) -> discord.Embed:
    """Erstellt das Embed für eine einzelne Abmeldung."""
    if zurueckgemeldet:
        # Zurückgemeldet: grünes Embed, alte Felder durchgestrichen.
        embed = discord.Embed(
            title="✅ Teammitglied zurückgemeldet",
            description=(
                f"<@{eintrag['user_id']}> ist wieder anwesend.\n"
                f"_Die Abmeldung wurde erfolgreich beendet._"
            ),
            color=FARBE_ZURUECK,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="👤 Teammitglied",
            value=f"~~{eintrag['name']}~~",
            inline=True,
        )
        embed.add_field(
            name="📅 Datum",
            value=f"~~{eintrag['datum']}~~",
            inline=True,
        )
        embed.add_field(
            name="⏳ Dauer",
            value=f"~~{eintrag['dauer']}~~",
            inline=True,
        )
        embed.add_field(
            name="📝 Grund",
            value=f"~~{eintrag['grund']}~~",
            inline=False,
        )
        if eintrag.get("leitung_id"):
            embed.add_field(
                name="🛡️ Eingetragen von",
                value=f"~~<@{eintrag['leitung_id']}>~~",
                inline=False,
            )
        # Genauer Zeitpunkt der Zurückmeldung.
        zeit = datetime.now(timezone.utc)
        embed.add_field(
            name="🕐 Zurückgemeldet am",
            value=f"<t:{int(zeit.timestamp())}:F>",
            inline=False,
        )
    else:
        # Aktive Abmeldung: rotes Embed.
        embed = discord.Embed(
            title="🔴 Teammitglied abgemeldet",
            description=f"<@{eintrag['user_id']}> ist aktuell nicht verfügbar.",
            color=FARBE_ABMELDUNG,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="👤 Teammitglied",
            value=eintrag["name"],
            inline=True,
        )
        embed.add_field(
            name="📅 Datum",
            value=eintrag["datum"],
            inline=True,
        )
        embed.add_field(
            name="⏳ Dauer",
            value=eintrag["dauer"],
            inline=True,
        )
        embed.add_field(
            name="📝 Grund",
            value=eintrag["grund"],
            inline=False,
        )
        if eintrag.get("leitung_id"):
            embed.add_field(
                name="🛡️ Eingetragen von",
                value=f"<@{eintrag['leitung_id']}>",
                inline=False,
            )

    # Profilbild als Thumbnail, falls bekannt.
    if eintrag.get("user_avatar_url"):
        embed.set_thumbnail(url=eintrag["user_avatar_url"])

    return _bot_footer(embed)


def baue_dashboard_embed() -> discord.Embed:
    """Erstellt das Dashboard-Embed mit aktiven und geplanten Abmeldungen."""
    jetzt = datetime.now(timezone.utc)
    anzahl_aktiv = len(abmeldungen)
    anzahl_geplant = len(geplante_abmeldungen)

    # Niemand aktiv UND niemand geplant → komplett leer.
    if anzahl_aktiv == 0 and anzahl_geplant == 0:
        embed = discord.Embed(
            title="📋 Abmeldungs-Dashboard",
            description=(
                "# ✅ Alle Teammitglieder sind anwesend!\n\n"
                "🟢 🟢 🟢 🟢 🟢\n"
                "_Aktuell liegt keine Abmeldung vor._"
            ),
            color=FARBE_DASHBOARD_LEER,
            timestamp=jetzt,
        )
        embed.add_field(
            name="📊 Status",
            value="**0** aktive Abmeldungen",
            inline=False,
        )
        if bot.user:
            embed.set_footer(
                text=f"{bot.user.name} • Zuletzt aktualisiert",
                icon_url=bot.user.display_avatar.url,
            )
        else:
            embed.set_footer(text="Zuletzt aktualisiert")
        return embed

    # Sonst Dashboard mit (mindestens) einer der beiden Sektionen.
    embed = discord.Embed(
        title="📋 Abmeldungs-Dashboard",
        description=(
            f"## 📊 Aktuell abgemeldet: **{anzahl_aktiv}** "
            f"{'Mitglied' if anzahl_aktiv == 1 else 'Mitglieder'}"
            + (
                f"\n📅 Geplant: **{anzahl_geplant}**"
                if anzahl_geplant > 0
                else ""
            )
            + "\n_Live-Übersicht aller laufenden und geplanten Abmeldungen._"
        ),
        color=FARBE_DASHBOARD,
        timestamp=jetzt,
    )

    # ---- Sektion: Aktuell abgemeldet ----
    if anzahl_aktiv > 0:
        embed.add_field(
            name="🔴 Aktuell abgemeldet",
            value=f"_{anzahl_aktiv} aktive "
            f"{'Abmeldung' if anzahl_aktiv == 1 else 'Abmeldungen'}_",
            inline=False,
        )
        sortiert = sorted(
            abmeldungen.values(),
            key=lambda e: e.get("start_time", ""),
            reverse=True,
        )
        for eintrag in sortiert:
            balken = fortschrittsbalken(
                eintrag.get("start_time", ""),
                eintrag.get("dauer_tage"),
            )
            wert = (
                f"<@{eintrag['user_id']}>\n"
                f"📅 **Datum:** {eintrag['datum']}\n"
                f"⏳ **Dauer:** {eintrag['dauer']}\n"
                f"📝 **Grund:** {eintrag['grund']}\n"
                f"`{balken}`"
            )
            embed.add_field(
                name=f"👤 {eintrag['name']}",
                value=wert,
                inline=False,
            )

    # ---- Sektion: Geplante Abmeldungen ----
    if anzahl_geplant > 0:
        embed.add_field(
            name="📅 Geplante Abmeldungen",
            value=f"_{anzahl_geplant} "
            f"{'Eintrag' if anzahl_geplant == 1 else 'Einträge'} vorgemerkt_",
            inline=False,
        )
        geplant_sortiert = sorted(
            geplante_abmeldungen.values(),
            key=lambda e: e.get("start_datum_iso", ""),
        )
        for plan in geplant_sortiert:
            wert = (
                f"<@{plan['user_id']}>\n"
                f"📅 **Start:** {plan['start_datum']}\n"
                f"📅 **Ende:** {plan['end_datum']}\n"
                f"📝 **Grund:** {plan['grund']}"
            )
            embed.add_field(
                name=f"🕓 {plan['name']}",
                value=wert,
                inline=False,
            )

    if bot.user:
        embed.set_footer(
            text=f"{bot.user.name} • Zuletzt aktualisiert",
            icon_url=bot.user.display_avatar.url,
        )
    else:
        embed.set_footer(text="Zuletzt aktualisiert")
    return embed


# =====================================================
# Dashboard und Logging
# =====================================================
async def aktualisiere_dashboard() -> None:
    """Aktualisiert die Dashboard-Nachricht."""
    global dashboard_message_id

    channel = bot.get_channel(config.DASHBOARD_CHANNEL_ID)
    if channel is None:
        print("⚠️  Dashboard-Channel konnte nicht gefunden werden.")
        return

    embed = baue_dashboard_embed()

    if dashboard_message_id is not None:
        try:
            nachricht = await channel.fetch_message(dashboard_message_id)
            await nachricht.edit(embed=embed)
            speichere_daten()
            return
        except discord.NotFound:
            dashboard_message_id = None

    nachricht = await channel.send(embed=embed)
    dashboard_message_id = nachricht.id
    speichere_daten()


# =====================================================
# Logging-System
# =====================================================
# Action-Typen als Konstanten – an Aufrufern eindeutig.
LOG_NEUE_ABMELDUNG       = "neue_abmeldung"
LOG_GEPLANTE_ABMELDUNG   = "geplante_abmeldung"
LOG_GEPLANTE_AKTIVIERT   = "geplante_aktiviert"
LOG_ZURUECK_CHANNEL      = "zurueck_channel"
LOG_ZURUECK_DM           = "zurueck_dm"
LOG_ZURUECK_LEITUNG      = "zurueck_leitung"
LOG_BEARBEITET           = "bearbeitet"
LOG_ENTFERNT             = "entfernt"
LOG_ERINNERUNG           = "erinnerung"

# Mapping: action_type -> (Titel, Farbe, Emoji)
_LOG_KONFIG: dict[str, tuple[str, discord.Color, str]] = {
    LOG_NEUE_ABMELDUNG:     ("Neue Abmeldung erstellt",
                             FARBE_LOG_NEU, "🔴"),
    LOG_GEPLANTE_ABMELDUNG: ("Geplante Abmeldung erstellt",
                             discord.Color.from_rgb(59, 130, 246), "📅"),
    LOG_GEPLANTE_AKTIVIERT: ("Geplante Abmeldung aktiviert",
                             discord.Color.from_rgb(34, 197, 94), "✅"),
    LOG_ZURUECK_CHANNEL:    ("Zurückmeldung über Channel-Button",
                             FARBE_LOG_ZURUECK, "🟢"),
    LOG_ZURUECK_DM:         ("Zurückmeldung über DM-Button",
                             FARBE_LOG_ZURUECK, "🟢"),
    LOG_ZURUECK_LEITUNG:    ("Zurückmeldung durch Leitung",
                             FARBE_LOG_ZURUECK, "🟢"),
    LOG_BEARBEITET:         ("Abmeldung bearbeitet",
                             FARBE_LOG_BEARBEITEN, "🟡"),
    LOG_ENTFERNT:           ("Abmeldung entfernt",
                             FARBE_LOG_ENTFERNEN, "⚫"),
    LOG_ERINNERUNG:         ("Erinnerungs-DM gesendet",
                             FARBE_ERINNERUNG, "⏰"),
}


async def log_action(
    action_type: str,
    *,
    hauptperson: discord.abc.User | int,
    ausfuehrer: discord.abc.User | None = None,
    felder: list[tuple[str, str]] | None = None,
) -> None:
    """Schreibt einen formatierten Log-Eintrag in den LOG_CHANNEL_ID.

    - action_type: eine der LOG_*-Konstanten.
    - hauptperson: die betroffene Person (User-Objekt oder ID).
    - ausfuehrer:  optional die Person, die die Aktion ausgeführt hat.
    - felder:      Liste von (Name, Wert)-Paaren für zusätzliche Details.
    """
    if not config.LOG_CHANNEL_ID:
        return
    channel = bot.get_channel(config.LOG_CHANNEL_ID)
    if channel is None:
        return

    titel, farbe, emoji = _LOG_KONFIG.get(
        action_type,
        ("Log-Eintrag", discord.Color.dark_grey(), "🗒️"),
    )

    # Hauptperson auf Mention abbilden.
    if isinstance(hauptperson, int):
        hauptperson_mention = f"<@{hauptperson}>"
    else:
        hauptperson_mention = hauptperson.mention

    jetzt = datetime.now(timezone.utc)
    embed = discord.Embed(
        title=f"{emoji} {titel}",
        color=farbe,
        timestamp=jetzt,
    )
    embed.add_field(name="📌 Aktion", value=titel, inline=True)
    embed.add_field(name="👤 Betroffene Person", value=hauptperson_mention, inline=True)
    if ausfuehrer is not None:
        embed.add_field(
            name="🛡️ Ausgeführt von",
            value=ausfuehrer.mention,
            inline=True,
        )
    embed.add_field(
        name="🕐 Zeitpunkt",
        value=f"<t:{int(jetzt.timestamp())}:F>",
        inline=False,
    )
    if felder:
        for name, wert in felder:
            embed.add_field(name=name, value=wert, inline=False)

    _bot_footer(embed)

    try:
        await channel.send(embed=embed)
    except Exception as e:
        print(f"⚠️  Log konnte nicht geschrieben werden: {e}")


# =====================================================
# View / Button für die Zurückmeldung
# =====================================================
class ZurueckmeldenView(discord.ui.View):
    """Persistente View mit dem Zurückmelden-Button."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Zurückmelden",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="zurueckmelden_button",
    )
    async def zurueckmelden(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        nachricht_id = interaction.message.id if interaction.message else None
        eintrag = abmeldungen.get(nachricht_id) if nachricht_id else None

        if eintrag is None:
            button.disabled = True
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(
                "⚠️ Diese Abmeldung ist nicht mehr aktiv.",
                ephemeral=True,
            )
            return

        # Erlaubt: betroffener Benutzer selbst ODER Leitung.
        if interaction.user.id != eintrag["user_id"] and not ist_leitung(interaction.user):
            await interaction.response.send_message(
                "❌ Nur die abgemeldete Person oder die Leitung "
                "kann diese Abmeldung beenden.",
                ephemeral=True,
            )
            return

        # Statistik aktualisieren.
        await beende_abmeldung(nachricht_id, eintrag, interaction)

        neues_embed = baue_abmeldungs_embed(eintrag, zurueckgemeldet=True)
        button.disabled = True
        button.label = "Zurückgemeldet"
        await interaction.response.edit_message(embed=neues_embed, view=self)

        await aktualisiere_dashboard()
        # Wer hat den Channel-Button geklickt – User selbst oder Leitung?
        if interaction.user.id == eintrag["user_id"]:
            channel_action = LOG_ZURUECK_CHANNEL
        else:
            channel_action = LOG_ZURUECK_LEITUNG
        await log_action(
            channel_action,
            hauptperson=eintrag["user_id"],
            ausfuehrer=interaction.user,
            felder=[
                ("📅 Abmeldung vom", eintrag["datum"]),
                ("⏳ Geplante Dauer", eintrag["dauer"]),
            ],
        )


class DMZurueckmeldenView(discord.ui.View):
    """Persistente View für den Zurückmelden-Button in der Bestätigungs-DM."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Zurückmelden",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="dm_zurueckmelden_button",
    )
    async def dm_zurueckmelden(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        # Eintrag finden anhand der DM-Message-ID.
        dm_msg_id = interaction.message.id if interaction.message else None
        nachricht_id: int | None = None
        eintrag: dict | None = None
        for mid, ein in abmeldungen.items():
            if ein.get("dm_message_id") == dm_msg_id:
                nachricht_id = mid
                eintrag = ein
                break

        if eintrag is None or nachricht_id is None:
            button.disabled = True
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(
                "⚠️ Diese Abmeldung ist nicht mehr aktiv.",
                ephemeral=True,
            )
            return

        # Originalnachricht im Abmeldungs-Channel auf 'zurückgemeldet' setzen.
        kanal = bot.get_channel(
            eintrag.get("channel_id", config.ABMELDUNGEN_CHANNEL_ID)
        )
        if kanal is not None:
            try:
                ursprung = await kanal.fetch_message(nachricht_id)
                neues_embed = baue_abmeldungs_embed(eintrag, zurueckgemeldet=True)
                kanal_view = ZurueckmeldenView()
                for kind in kanal_view.children:
                    if isinstance(kind, discord.ui.Button):
                        kind.disabled = True
                        kind.label = "Zurückgemeldet"
                await ursprung.edit(embed=neues_embed, view=kanal_view)
            except discord.NotFound:
                pass

        # Statistik aktualisieren und aus aktiver Liste entfernen.
        await beende_abmeldung(nachricht_id, eintrag, interaction)
        await aktualisiere_dashboard()
        await log_action(
            LOG_ZURUECK_DM,
            hauptperson=eintrag["user_id"],
            ausfuehrer=interaction.user,
            felder=[
                ("📅 Abmeldung vom", eintrag["datum"]),
                ("⏳ Geplante Dauer", eintrag["dauer"]),
            ],
        )

        # DM-Button deaktivieren und Bestätigung anzeigen.
        button.disabled = True
        button.label = "Zurückgemeldet"
        bestaetigungs_embed = discord.Embed(
            title="✅ Du wurdest erfolgreich zurückgemeldet!",
            description=(
                "Schön, dass du wieder da bist! Deine Abmeldung wurde "
                "erfolgreich beendet."
            ),
            color=FARBE_ZURUECK,
            timestamp=datetime.now(timezone.utc),
        )
        _bot_footer(bestaetigungs_embed)
        await interaction.response.edit_message(
            embed=bestaetigungs_embed, view=self
        )


async def beende_abmeldung(
    nachricht_id: int,
    eintrag: dict,
    interaction: discord.Interaction | None = None,
) -> None:
    """Entfernt eine Abmeldung aus der aktiven Liste und pflegt die Statistik."""
    user_id = eintrag["user_id"]
    eintrag_statistik = statistik.setdefault(user_id, {"anzahl": 0, "tage": 0.0})

    # Tatsächlich abwesende Tage berechnen.
    start_iso = eintrag.get("start_time")
    if start_iso:
        try:
            start = datetime.fromisoformat(start_iso)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            tatsaechliche_tage = max(
                0.0,
                (datetime.now(timezone.utc) - start).total_seconds() / 86400.0,
            )
            eintrag_statistik["tage"] = round(
                eintrag_statistik["tage"] + tatsaechliche_tage, 2
            )
        except ValueError:
            pass

    abmeldungen.pop(nachricht_id, None)
    speichere_daten()


# =====================================================
# Hilfsfunktionen: Suche von Abmeldungen
# =====================================================
def finde_abmeldung_fuer_user(user_id: int) -> tuple[int, dict] | None:
    """Findet die (jüngste) aktive Abmeldung für einen Benutzer."""
    treffer = [(mid, e) for mid, e in abmeldungen.items() if e["user_id"] == user_id]
    if not treffer:
        return None
    # Jüngste zuerst (höchste message_id).
    treffer.sort(key=lambda x: x[0], reverse=True)
    return treffer[0]


async def aktualisiere_originalnachricht(eintrag: dict, message_id: int) -> None:
    """Bearbeitet die ursprüngliche Abmeldungs-Nachricht im Channel."""
    channel = bot.get_channel(eintrag.get("channel_id", config.ABMELDUNGEN_CHANNEL_ID))
    if channel is None:
        return
    try:
        nachricht = await channel.fetch_message(message_id)
        await nachricht.edit(embed=baue_abmeldungs_embed(eintrag))
    except discord.NotFound:
        pass
    except Exception as e:
        print(f"⚠️  Originalnachricht nicht aktualisierbar: {e}")


# =====================================================
# Slash-Command: /abmelden  (mit Modal-Dialog)
# =====================================================
async def _veroeffentliche_abmeldung_core(
    *,
    zielperson: discord.Member,
    leitung_id: int | None,
    ausgefuehrt_von: discord.abc.User,
    name: str,
    datum: str,
    grund: str,
    dauer: str,
    dauer_tage: float,
) -> tuple[discord.abc.GuildChannel | None, int | None]:
    """Kern-Funktion: postet Embed, sendet DM mit Button, aktualisiert
    Dashboard und schreibt Log. Liefert (channel, message_id) zurück.
    Wird sowohl beim sofortigen /abmelden als auch beim Auto-Aktivieren
    geplanter Abmeldungen verwendet.
    """
    channel = bot.get_channel(config.ABMELDUNGEN_CHANNEL_ID)
    if channel is None:
        return None, None

    jetzt = datetime.now(timezone.utc)
    eintrag = {
        "user_id": zielperson.id,
        "leitung_id": leitung_id,
        "name": name,
        "datum": datum,
        "grund": grund,
        "dauer": dauer,
        "dauer_tage": dauer_tage,
        "start_time": jetzt.isoformat(),
        "channel_id": channel.id,
        "vor_ablauf_dm": False,
        "letzte_erinnerung": None,
        "user_avatar_url": str(zielperson.display_avatar.url),
        "dm_message_id": None,
    }

    embed = baue_abmeldungs_embed(eintrag)
    view = ZurueckmeldenView()

    inhalt = ""
    if config.TEAMLEITER_ROLE_ID:
        inhalt = f"<@&{config.TEAMLEITER_ROLE_ID}>"

    nachricht = await channel.send(
        content=inhalt,
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions(roles=True, users=False),
    )

    abmeldungen[nachricht.id] = eintrag

    s = statistik.setdefault(zielperson.id, {"anzahl": 0, "tage": 0.0})
    s["anzahl"] = int(s.get("anzahl", 0)) + 1

    # Bestätigungs-DM mit Zurückmelden-Button an die abgemeldete Person.
    try:
        dm_embed = discord.Embed(
            title="📬 Abmeldung bestätigt",
            description=(
                f"Hallo {zielperson.mention}, deine Abmeldung wurde "
                "erfolgreich eingetragen. Hier nochmal die Details "
                "auf einen Blick:"
            ),
            color=FARBE_BESTAETIGUNG,
            timestamp=datetime.now(timezone.utc),
        )
        dm_embed.add_field(name="📅 Datum", value=datum, inline=True)
        dm_embed.add_field(name="⏳ Dauer", value=dauer, inline=True)
        dm_embed.add_field(name="📝 Grund", value=grund, inline=False)
        if leitung_id:
            dm_embed.add_field(
                name="🛡️ Eingetragen von",
                value=f"<@{leitung_id}>",
                inline=False,
            )
        dm_embed.add_field(
            name="\u200b",
            value=(
                "💚 Wir wünschen dir eine gute Zeit – melde dich gerne "
                "direkt über den ✅-Knopf hier zurück."
            ),
            inline=False,
        )
        dm_embed.set_thumbnail(url=zielperson.display_avatar.url)
        _bot_footer(dm_embed)

        dm_view = DMZurueckmeldenView()
        dm_nachricht = await zielperson.send(embed=dm_embed, view=dm_view)
        eintrag["dm_message_id"] = dm_nachricht.id
    except discord.Forbidden:
        pass

    speichere_daten()
    await aktualisiere_dashboard()

    await log_action(
        LOG_NEUE_ABMELDUNG,
        hauptperson=zielperson,
        ausfuehrer=ausgefuehrt_von,
        felder=[
            ("📅 Startdatum", datum),
            ("⏳ Dauer", dauer),
            ("📝 Grund", grund),
        ],
    )

    return channel, nachricht.id


async def _veroeffentliche_abmeldung(
    interaction: discord.Interaction,
    zielperson: discord.Member,
    leitung_id: int | None,
    name: str,
    datum: str,
    grund: str,
    dauer: str,
    dauer_tage: float,
) -> None:
    """Wrapper für die Modal-Antwort: postet die Abmeldung und sendet
    eine ephemere Bestätigung an den Aufrufer."""
    channel, _ = await _veroeffentliche_abmeldung_core(
        zielperson=zielperson,
        leitung_id=leitung_id,
        ausgefuehrt_von=interaction.user,
        name=name,
        datum=datum,
        grund=grund,
        dauer=dauer,
        dauer_tage=dauer_tage,
    )
    if channel is None:
        await interaction.followup.send(
            "⚠️ Der Abmeldungs-Channel wurde nicht gefunden.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"✅ Abmeldung wurde in {channel.mention} veröffentlicht.",
        ephemeral=True,
    )


async def _plane_abmeldung(
    interaction: discord.Interaction,
    *,
    zielperson: discord.Member,
    leitung_id: int | None,
    name: str,
    grund: str,
    start_dt: datetime,
    end_dt: datetime,
    dauer_tage: int,
) -> None:
    """Speichert eine Abmeldung als 'geplant' und sendet die DM-Vorschau."""
    plan_id = f"plan_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{zielperson.id}"
    dauer_str = f"{dauer_tage} Tag" if dauer_tage == 1 else f"{dauer_tage} Tage"
    datum_str = (
        f"{start_dt.strftime('%d.%m.%Y')} – {end_dt.strftime('%d.%m.%Y')}"
    )

    geplante_abmeldungen[plan_id] = {
        "plan_id": plan_id,
        "user_id": zielperson.id,
        "leitung_id": leitung_id,
        "ausgefuehrt_von_id": interaction.user.id,
        "name": name,
        "grund": grund,
        "start_datum": start_dt.strftime("%d.%m.%Y"),
        "end_datum": end_dt.strftime("%d.%m.%Y"),
        "start_datum_iso": start_dt.date().isoformat(),
        "end_datum_iso": end_dt.date().isoformat(),
        "datum": datum_str,
        "dauer": dauer_str,
        "dauer_tage": float(dauer_tage),
        "user_avatar_url": str(zielperson.display_avatar.url),
        "status": "geplant",
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
    }
    speichere_daten()
    await aktualisiere_dashboard()

    # DM an die geplante Person.
    try:
        dm_embed = discord.Embed(
            title="📅 Abmeldung geplant",
            description=(
                f"Hallo {zielperson.mention}, deine Abmeldung wurde "
                f"erfolgreich **vorgemerkt** und wird automatisch am "
                f"**{start_dt.strftime('%d.%m.%Y')}** aktiviert."
            ),
            color=FARBE_DASHBOARD,
            timestamp=datetime.now(timezone.utc),
        )
        dm_embed.add_field(name="📅 Startdatum", value=start_dt.strftime("%d.%m.%Y"), inline=True)
        dm_embed.add_field(name="📅 Enddatum", value=end_dt.strftime("%d.%m.%Y"), inline=True)
        dm_embed.add_field(name="⏳ Dauer", value=dauer_str, inline=True)
        dm_embed.add_field(name="📝 Grund", value=grund, inline=False)
        if leitung_id:
            dm_embed.add_field(
                name="🛡️ Geplant von",
                value=f"<@{leitung_id}>",
                inline=False,
            )
        dm_embed.set_thumbnail(url=zielperson.display_avatar.url)
        _bot_footer(dm_embed)
        await zielperson.send(embed=dm_embed)
    except discord.Forbidden:
        pass

    await log_action(
        LOG_GEPLANTE_ABMELDUNG,
        hauptperson=zielperson,
        ausfuehrer=interaction.user,
        felder=[
            ("📅 Startdatum", datum_str),
            ("⏳ Dauer", dauer),
            ("📝 Grund", grund),
        ],
    )

    await interaction.followup.send(
        f"📅 Deine Abmeldung wurde geplant und wird am "
        f"**{start_dt.strftime('%d.%m.%Y')}** automatisch aktiviert.",
        ephemeral=True,
    )


def _parse_ddmmyyyy(text: str) -> "datetime | None":
    """Parst einen Datumsstring im Format DD.MM.YYYY."""
    try:
        return datetime.strptime(text.strip(), "%d.%m.%Y")
    except (ValueError, AttributeError):
        return None


def _finde_mitglied(guild: discord.Guild | None, query: str) -> discord.Member | None:
    """Sucht ein Mitglied per Username, Display-Name oder Mention/ID."""
    if guild is None:
        return None
    q = query.strip().lstrip("@")
    # Mention <@id> oder reine ID.
    if q.startswith("<@") and q.endswith(">"):
        q = q.strip("<@!>")
    if q.isdigit():
        m = guild.get_member(int(q))
        if m is not None:
            return m
    q_low = q.lower()
    for m in guild.members:
        kandidaten = [m.name.lower(), m.display_name.lower()]
        global_name = getattr(m, "global_name", None)
        if global_name:
            kandidaten.append(global_name.lower())
        if q_low in kandidaten:
            return m
    return None


class AbmeldenModal(discord.ui.Modal):
    """Popup-Formular für eine neue Abmeldung."""

    def __init__(self, *, fuer_leitung: bool):
        super().__init__(title="📋 Neue Abmeldung", timeout=600)
        self.fuer_leitung = fuer_leitung

        self.grund_input = discord.ui.TextInput(
            label="📝 Grund",
            placeholder="z.B. Urlaub, Krankheit...",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=True,
        )
        self.startdatum_input = discord.ui.TextInput(
            label="📅 Startdatum",
            placeholder="z.B. 24.04.2026",
            style=discord.TextStyle.short,
            max_length=10,
            required=True,
        )
        self.enddatum_input = discord.ui.TextInput(
            label="📅 Enddatum",
            placeholder="z.B. 30.04.2026",
            style=discord.TextStyle.short,
            max_length=10,
            required=True,
        )
        self.add_item(self.grund_input)
        self.add_item(self.startdatum_input)
        self.add_item(self.enddatum_input)

        self.teammitglied_input: discord.ui.TextInput | None = None
        if fuer_leitung:
            self.teammitglied_input = discord.ui.TextInput(
                label="👤 Teammitglied (optional)",
                placeholder="Discord-Username – leer = du selbst",
                style=discord.TextStyle.short,
                max_length=100,
                required=False,
            )
            self.add_item(self.teammitglied_input)

    async def on_submit(self, interaction: discord.Interaction):
        # 1) Datumsformat prüfen.
        start_dt = _parse_ddmmyyyy(self.startdatum_input.value)
        end_dt = _parse_ddmmyyyy(self.enddatum_input.value)
        if start_dt is None or end_dt is None:
            await interaction.response.send_message(
                "❌ Ungültiges Datumsformat! Bitte nutze DD.MM.YYYY "
                "(z.B. 24.04.2026)",
                ephemeral=True,
            )
            return

        # 2) Startdatum nicht in der Vergangenheit.
        heute = datetime.now().date()
        if start_dt.date() < heute:
            await interaction.response.send_message(
                "❌ Das Startdatum kann nicht in der Vergangenheit liegen!",
                ephemeral=True,
            )
            return

        # 3) Enddatum muss nach Startdatum liegen.
        if end_dt.date() <= start_dt.date():
            await interaction.response.send_message(
                "❌ Das Enddatum muss nach dem Startdatum liegen!",
                ephemeral=True,
            )
            return

        # 4) Maximaldauer prüfen.
        dauer_tage = (end_dt.date() - start_dt.date()).days
        if dauer_tage > config.MAX_ABMELDUNG_DAYS:
            await interaction.response.send_message(
                f"❌ Die maximale Abmeldungsdauer beträgt "
                f"{config.MAX_ABMELDUNG_DAYS} Tage!",
                ephemeral=True,
            )
            return

        # 5) Zielperson bestimmen (Leitung kann andere abmelden).
        zielperson: discord.Member = interaction.user  # type: ignore
        leitung_id: int | None = None
        if (
            self.fuer_leitung
            and self.teammitglied_input is not None
            and self.teammitglied_input.value.strip()
        ):
            gefunden = _finde_mitglied(
                interaction.guild, self.teammitglied_input.value
            )
            if gefunden is None:
                await interaction.response.send_message(
                    f"❌ Mitglied **{self.teammitglied_input.value.strip()}** "
                    "wurde nicht gefunden!",
                    ephemeral=True,
                )
                return
            zielperson = gefunden
            leitung_id = interaction.user.id

        # Validierung erfolgreich – jetzt antworten und veröffentlichen.
        await interaction.response.defer(ephemeral=True)

        name = zielperson.display_name
        grund = self.grund_input.value.strip()

        # Geplant, falls Startdatum > morgen liegt.
        tage_bis_start = (start_dt.date() - heute).days
        if tage_bis_start > 1:
            await _plane_abmeldung(
                interaction,
                zielperson=zielperson,
                leitung_id=leitung_id,
                name=name,
                grund=grund,
                start_dt=start_dt,
                end_dt=end_dt,
                dauer_tage=dauer_tage,
            )
            return

        # Sofort aktivieren.
        datum_str = (
            f"{start_dt.strftime('%d.%m.%Y')} – {end_dt.strftime('%d.%m.%Y')}"
        )
        dauer_str = f"{dauer_tage} Tag" if dauer_tage == 1 else f"{dauer_tage} Tage"
        await _veroeffentliche_abmeldung(
            interaction=interaction,
            zielperson=zielperson,
            leitung_id=leitung_id,
            name=name,
            datum=datum_str,
            grund=grund,
            dauer=dauer_str,
            dauer_tage=float(dauer_tage),
        )


@bot.tree.command(
    name="abmelden",
    description="Erstellt eine neue Abmeldung über ein Eingabe-Formular.",
)
async def abmelden(interaction: discord.Interaction):
    """Öffnet das Modal zur Erstellung einer Abmeldung."""
    if not ist_berechtigt(interaction.user):
        await interaction.response.send_message(
            "❌ Du hast nicht die nötige Rolle, um diesen Befehl zu nutzen.",
            ephemeral=True,
        )
        return

    modal = AbmeldenModal(fuer_leitung=ist_leitung(interaction.user))
    await interaction.response.send_modal(modal)


# =====================================================
# Slash-Command: /abmeldung_bearbeiten
# =====================================================
@bot.tree.command(
    name="abmeldung_bearbeiten",
    description="Bearbeitet eine bestehende Abmeldung eines Mitglieds.",
)
@app_commands.describe(
    user="Mitglied, dessen Abmeldung bearbeitet werden soll",
    datum="Neues Datum (optional)",
    grund="Neuer Grund (optional)",
    dauer="Neue Dauer (optional)",
)
async def abmeldung_bearbeiten(
    interaction: discord.Interaction,
    user: discord.Member,
    datum: str | None = None,
    grund: str | None = None,
    dauer: str | None = None,
):
    """Aktualisiert die Felder einer aktiven Abmeldung."""
    # Nur Leitung oder die betroffene Person selbst darf bearbeiten.
    if interaction.user.id != user.id and not ist_leitung(interaction.user):
        await interaction.response.send_message(
            "❌ Nur die betroffene Person oder die Leitung darf bearbeiten.",
            ephemeral=True,
        )
        return

    treffer = finde_abmeldung_fuer_user(user.id)
    if treffer is None:
        await interaction.response.send_message(
            f"⚠️ Für {user.mention} existiert aktuell keine Abmeldung.",
            ephemeral=True,
        )
        return

    message_id, eintrag = treffer
    aenderungen: list[str] = []
    log_felder: list[tuple[str, str]] = []

    if datum:
        alter_wert = eintrag["datum"]
        eintrag["datum"] = datum
        aenderungen.append(f"Datum → {datum}")
        log_felder.append(("📅 Datum", f"`{alter_wert}` → **{datum}**"))
    if grund:
        alter_wert = eintrag["grund"]
        eintrag["grund"] = grund
        aenderungen.append(f"Grund → {grund}")
        log_felder.append(("📝 Grund", f"`{alter_wert}` → **{grund}**"))
    if dauer:
        neue_tage = parse_dauer_in_tage(dauer)
        if neue_tage is not None and neue_tage > config.MAX_ABMELDUNG_DAYS:
            await interaction.response.send_message(
                f"❌ Die Dauer überschreitet das Maximum von "
                f"{config.MAX_ABMELDUNG_DAYS} Tagen.",
                ephemeral=True,
            )
            return
        alter_wert = eintrag["dauer"]
        eintrag["dauer"] = dauer
        eintrag["dauer_tage"] = neue_tage
        # Erinnerungsstatus zurücksetzen, da sich der Zeitpunkt verschoben hat.
        eintrag["vor_ablauf_dm"] = False
        eintrag["letzte_erinnerung"] = None
        aenderungen.append(f"Dauer → {dauer}")
        log_felder.append(("⏳ Dauer", f"`{alter_wert}` → **{dauer}**"))

    if not aenderungen:
        await interaction.response.send_message(
            "ℹ️ Du hast keine Änderungen angegeben.",
            ephemeral=True,
        )
        return

    speichere_daten()
    await aktualisiere_originalnachricht(eintrag, message_id)
    await aktualisiere_dashboard()
    await log_action(
        LOG_BEARBEITET,
        hauptperson=user,
        ausfuehrer=interaction.user,
        felder=log_felder,
    )

    await interaction.response.send_message(
        "✅ Abmeldung aktualisiert: " + ", ".join(aenderungen),
        ephemeral=True,
    )


# =====================================================
# Slash-Command: /abmeldung_entfernen
# =====================================================
@bot.tree.command(
    name="abmeldung_entfernen",
    description="Entfernt die Abmeldung eines Mitglieds (nur Leitung).",
)
@app_commands.describe(user="Mitglied, dessen Abmeldung entfernt werden soll")
async def abmeldung_entfernen(
    interaction: discord.Interaction,
    user: discord.Member,
):
    """Entfernt eine aktive Abmeldung komplett."""
    if not ist_leitung(interaction.user):
        await interaction.response.send_message(
            "❌ Nur die Leitung darf Abmeldungen entfernen.",
            ephemeral=True,
        )
        return

    treffer = finde_abmeldung_fuer_user(user.id)
    if treffer is None:
        await interaction.response.send_message(
            f"⚠️ Für {user.mention} existiert aktuell keine Abmeldung.",
            ephemeral=True,
        )
        return

    message_id, eintrag = treffer
    await beende_abmeldung(message_id, eintrag)

    # Originalnachricht als entfernt markieren, Button deaktivieren.
    channel = bot.get_channel(eintrag.get("channel_id", config.ABMELDUNGEN_CHANNEL_ID))
    if channel is not None:
        try:
            nachricht = await channel.fetch_message(message_id)
            entfernt_embed = discord.Embed(
                title="🗑️ Abmeldung entfernt",
                description=(
                    f"Die Abmeldung von <@{user.id}> wurde von "
                    f"{interaction.user.mention} entfernt."
                ),
                color=FARBE_LOG_ENTFERNEN,
                timestamp=datetime.now(timezone.utc),
            )
            entfernt_embed.add_field(
                name="📅 Datum", value=f"~~{eintrag['datum']}~~", inline=True
            )
            entfernt_embed.add_field(
                name="⏳ Dauer", value=f"~~{eintrag['dauer']}~~", inline=True
            )
            entfernt_embed.add_field(
                name="📝 Grund", value=f"~~{eintrag['grund']}~~", inline=False
            )
            if eintrag.get("user_avatar_url"):
                entfernt_embed.set_thumbnail(url=eintrag["user_avatar_url"])
            _bot_footer(entfernt_embed)

            view = ZurueckmeldenView()
            for kind in view.children:
                if isinstance(kind, discord.ui.Button):
                    kind.disabled = True
                    kind.label = "Entfernt"
            await nachricht.edit(embed=entfernt_embed, view=view)
        except discord.NotFound:
            pass

    await aktualisiere_dashboard()
    await log_action(
        LOG_ENTFERNT,
        hauptperson=user,
        ausfuehrer=interaction.user,
        felder=[
            ("📅 Abmeldung vom", eintrag["datum"]),
            ("⏳ Dauer", eintrag["dauer"]),
            ("📝 Grund", eintrag["grund"]),
        ],
    )

    await interaction.response.send_message(
        f"🗑️ Abmeldung von {user.mention} entfernt.",
        ephemeral=True,
    )


# =====================================================
# Slash-Command: /statistik
# =====================================================
@bot.tree.command(
    name="statistik",
    description="Zeigt Abmeldungs-Statistiken eines Mitglieds (nur Leitung).",
)
@app_commands.describe(user="Mitglied, dessen Statistik angezeigt werden soll")
async def statistik_befehl(
    interaction: discord.Interaction,
    user: discord.Member,
):
    """Zeigt Anzahl und Summe der Abmeldungstage eines Mitglieds."""
    if not ist_leitung(interaction.user):
        await interaction.response.send_message(
            "❌ Nur die Leitung darf Statistiken einsehen.",
            ephemeral=True,
        )
        return

    werte = statistik.get(user.id, {"anzahl": 0, "tage": 0.0})
    aktiv = finde_abmeldung_fuer_user(user.id) is not None

    embed = discord.Embed(
        title=f"📈 Statistik für {user.display_name}",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Anzahl Abmeldungen",
        value=str(int(werte.get("anzahl", 0))),
        inline=True,
    )
    embed.add_field(
        name="Tage abwesend (Summe)",
        value=f"{float(werte.get('tage', 0.0)):.1f}",
        inline=True,
    )
    embed.add_field(
        name="Aktuell abgemeldet",
        value="Ja" if aktiv else "Nein",
        inline=True,
    )
    embed.set_thumbnail(url=user.display_avatar.url)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# =====================================================
# Hintergrund-Task: Erinnerungen
# =====================================================
@tasks.loop(minutes=30)
async def erinnerungs_task():
    """Sendet DM-Vorwarnungen und Pings nach Ablauf der Dauer."""
    if not abmeldungen:
        return

    jetzt = datetime.now(timezone.utc)
    aenderung = False

    for message_id, eintrag in list(abmeldungen.items()):
        dauer_tage = eintrag.get("dauer_tage")
        start_iso = eintrag.get("start_time")
        if not dauer_tage or not start_iso:
            continue

        try:
            start = datetime.fromisoformat(start_iso)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        ende = start + timedelta(days=float(dauer_tage))
        ein_tag_vorher = ende - timedelta(days=1)

        # 1) DM einen Tag vor Ablauf, falls noch nicht gesendet.
        if not eintrag.get("vor_ablauf_dm") and ein_tag_vorher <= jetzt < ende:
            user = bot.get_user(eintrag["user_id"])
            if user is None:
                try:
                    user = await bot.fetch_user(eintrag["user_id"])
                except Exception:
                    user = None
            if user is not None:
                try:
                    erinn_embed = discord.Embed(
                        title="⏰ Erinnerung: Abmeldung läuft bald ab",
                        description=(
                            "Deine Abmeldung läuft in **weniger als 24 Stunden** ab.\n"
                            "Bitte denke daran, dich rechtzeitig zurückzumelden."
                        ),
                        color=FARBE_ERINNERUNG,
                        timestamp=jetzt,
                    )
                    erinn_embed.add_field(
                        name="📅 Datum", value=eintrag["datum"], inline=True
                    )
                    erinn_embed.add_field(
                        name="⏳ Dauer", value=eintrag["dauer"], inline=True
                    )
                    erinn_embed.add_field(
                        name="📝 Grund", value=eintrag["grund"], inline=False
                    )
                    erinn_embed.add_field(
                        name="🕐 Läuft ab",
                        value=f"<t:{int(ende.timestamp())}:R>",
                        inline=False,
                    )
                    if eintrag.get("user_avatar_url"):
                        erinn_embed.set_thumbnail(url=eintrag["user_avatar_url"])
                    _bot_footer(erinn_embed)
                    await user.send(embed=erinn_embed)
                    # Log: Erinnerungs-DM wurde erfolgreich verschickt.
                    await log_action(
                        LOG_ERINNERUNG,
                        hauptperson=user,
                        felder=[
                            ("📅 Abmeldung vom", eintrag["datum"]),
                            ("⏳ Dauer", eintrag["dauer"]),
                            ("🕐 Läuft ab", f"<t:{int(ende.timestamp())}:F>"),
                            ("📨 Kanal", "Direktnachricht (vor Ablauf)"),
                        ],
                    )
                except discord.Forbidden:
                    pass
            eintrag["vor_ablauf_dm"] = True
            aenderung = True

        # 2) Nach Ablauf: alle 24 Stunden Ping im Abmeldungs-Channel.
        if jetzt >= ende:
            letzte = eintrag.get("letzte_erinnerung")
            sende_ping = True
            if letzte:
                try:
                    letzte_dt = datetime.fromisoformat(letzte)
                    if letzte_dt.tzinfo is None:
                        letzte_dt = letzte_dt.replace(tzinfo=timezone.utc)
                    sende_ping = (jetzt - letzte_dt) >= timedelta(hours=24)
                except ValueError:
                    pass

            if sende_ping:
                channel = bot.get_channel(
                    eintrag.get("channel_id", config.ABMELDUNGEN_CHANNEL_ID)
                )
                if channel is not None:
                    try:
                        await channel.send(
                            f"⚠️ <@{eintrag['user_id']}> deine Abmeldung "
                            f"vom {eintrag['datum']} ist abgelaufen. "
                            f"Bitte zurückmelden!",
                            allowed_mentions=discord.AllowedMentions(users=True),
                        )
                    except Exception as e:
                        print(f"⚠️  Erinnerungs-Ping fehlgeschlagen: {e}")
                eintrag["letzte_erinnerung"] = jetzt.isoformat()
                aenderung = True

    if aenderung:
        speichere_daten()


@erinnerungs_task.before_loop
async def vor_erinnerungen():
    await bot.wait_until_ready()


# =====================================================
# Hintergrund-Task: Geplante Abmeldungen aktivieren
# =====================================================
@tasks.loop(hours=1)
async def aktivierungs_task():
    """Prüft stündlich, ob geplante Abmeldungen jetzt starten sollen."""
    if not geplante_abmeldungen:
        return

    heute = datetime.now().date()

    # Über Kopie iterieren, weil wir während der Schleife ggf. löschen.
    for plan_id, plan in list(geplante_abmeldungen.items()):
        start_iso = plan.get("start_datum_iso")
        if not start_iso:
            continue
        try:
            start_date = datetime.fromisoformat(start_iso).date()
        except ValueError:
            continue

        # Aktivieren, sobald das Startdatum erreicht (oder überschritten) ist.
        if start_date > heute:
            continue

        # Zielperson finden (über alle Server, in denen der Bot ist).
        zielperson: discord.Member | None = None
        for guild in bot.guilds:
            mitglied = guild.get_member(plan["user_id"])
            if mitglied is not None:
                zielperson = mitglied
                break

        if zielperson is None:
            print(f"⚠️  Geplante Abmeldung {plan_id}: Mitglied nicht gefunden.")
            continue

        # Person, die ursprünglich geplant hat (für Logging).
        ausgefuehrt_von_id = plan.get("ausgefuehrt_von_id", plan["user_id"])
        ausgefuehrt_von = (
            bot.get_user(ausgefuehrt_von_id)
            or zielperson
        )

        channel, _ = await _veroeffentliche_abmeldung_core(
            zielperson=zielperson,
            leitung_id=plan.get("leitung_id"),
            ausgefuehrt_von=ausgefuehrt_von,
            name=plan.get("name", zielperson.display_name),
            datum=plan["datum"],
            grund=plan["grund"],
            dauer=plan["dauer"],
            dauer_tage=float(plan["dauer_tage"]),
        )

        # Plan-Eintrag aufräumen.
        geplante_abmeldungen.pop(plan_id, None)
        speichere_daten()

        # Aktivierungs-DM an die Person.
        if channel is not None:
            try:
                aktiv_embed = discord.Embed(
                    title="✅ Deine geplante Abmeldung ist jetzt aktiv!",
                    description=(
                        f"Hallo {zielperson.mention}, deine vorgemerkte "
                        "Abmeldung wurde soeben automatisch aktiviert."
                    ),
                    color=FARBE_BESTAETIGUNG,
                    timestamp=datetime.now(timezone.utc),
                )
                aktiv_embed.add_field(
                    name="📅 Datum", value=plan["datum"], inline=True
                )
                aktiv_embed.add_field(
                    name="⏳ Dauer", value=plan["dauer"], inline=True
                )
                aktiv_embed.add_field(
                    name="📝 Grund", value=plan["grund"], inline=False
                )
                aktiv_embed.set_thumbnail(url=zielperson.display_avatar.url)
                _bot_footer(aktiv_embed)
                await zielperson.send(embed=aktiv_embed)
            except discord.Forbidden:
                pass

        # Log: geplante Abmeldung wurde automatisch aktiviert.
        await log_action(
            LOG_GEPLANTE_AKTIVIERT,
            hauptperson=zielperson,
            felder=[
                ("📅 Startdatum", plan["datum"]),
                ("⏳ Dauer", plan["dauer"]),
                ("📝 Grund", plan["grund"]),
                ("ℹ️ Hinweis", "Automatisch durch den Aktivierungs-Task gestartet."),
            ],
        )


@aktivierungs_task.before_loop
async def vor_aktivierung():
    await bot.wait_until_ready()


# =====================================================
# Bot-Events
# =====================================================
@bot.event
async def on_ready():
    """Wird ausgeführt, sobald der Bot verbunden ist."""
    bot.add_view(ZurueckmeldenView())
    bot.add_view(DMZurueckmeldenView())

    try:
        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} Slash-Befehl(e) synchronisiert.")
    except Exception as e:
        print(f"⚠️  Fehler beim Synchronisieren der Slash-Befehle: {e}")

    await aktualisiere_dashboard()

    if not erinnerungs_task.is_running():
        erinnerungs_task.start()
    if not aktivierungs_task.is_running():
        aktivierungs_task.start()

    print(f"✅ Bot ist online und eingeloggt als {bot.user} (ID: {bot.user.id})")


# =====================================================
# Bot starten
# =====================================================
if __name__ == "__main__":
    lade_daten()
    bot.run(config.TOKEN)