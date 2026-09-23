import flet as ft
import sqlite3
from datetime import datetime, timedelta
from contextlib import contextmanager
import logging
from pathlib import Path
import hashlib
import os
import threading
import logging

# Dies definiert logger immer, auch wenn noch nicht vom Launcher injiziert
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Datenbank-Pfad - KORRIGIERT für Persistenz
SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / "handball_tracker.db"


# === HILFSFUNKTIONEN ===

def get_log_lines(n=300):
    """Liest die letzten `n` Zeilen aus der Logdatei und gibt sie als String zurück."""
    try:
        # Versuche vom Launcher die Log-Datei zu finden
        launcher_log = Path(__file__).parent.parent / "launcher.log"
        
        if launcher_log.exists():
            log_path = launcher_log
        else:
            # Fallback: es gibt keine App-spezifische Logdatei
            return "Logs werden vom Launcher-System verwaltet"
        
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
            last = lines[-n:]
            return ''.join(last)
    except Exception as e:
        logger.exception(f"Fehler beim Lesen der Logdatei: {e}")
        return f"Fehler beim Lesen der Logdatei: {e}"


# === DATENBANKVERBINDUNG ===

@contextmanager
def get_db():
    """Context Manager für Datenbankverbindung"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_database():
    """Initialisiert die Datenbank beim ersten Start"""
    
    db_exists = DB_PATH.exists()
    
    if db_exists:
        logger.debug("Datenbank geladen")
    else:
        logger.info("Neue Datenbank wird erstellt")
    
    with get_db() as conn:
        c = conn.cursor()
        
        # Spieler-Tabelle
        c.execute('''CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            is_admin BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        # Getränke-Tabelle mit Bezahlstatus
        c.execute('''CREATE TABLE IF NOT EXISTS drinks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            drink_type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            date TEXT NOT NULL,
            notes TEXT,
            paid BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (player_id) REFERENCES players (id)
        )''')
        
        # Getränke-Typen Tabelle
        c.execute('''CREATE TABLE IF NOT EXISTS drink_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            price REAL DEFAULT 0.0
        )''')
        
        # Standard-Getränke einfügen
        default_drinks = [
            ('Bier 0,33', 0.50),
            ('Bier 0,5', 1.00),
            ('Fanta 0,33', 0.50),
            ('Fanta 0,5', 1.00),
        ]
        
        for drink, price in default_drinks:
            c.execute('INSERT OR IGNORE INTO drink_types (name, price) VALUES (?, ?)', 
                     (drink, price))
        
        # Admin-Benutzer erstellen falls nicht vorhanden
        c.execute('SELECT COUNT(*) FROM players WHERE is_admin = 1')
        admin_count = c.fetchone()[0]
        
        if admin_count == 0:
            admin_password_hash = hash_password("admin")
            c.execute('INSERT OR IGNORE INTO players (username, password_hash, full_name, is_admin) VALUES (?, ?, ?, ?)',
                     ("admin", admin_password_hash, "Administrator", 1))
            logger.info("Admin-Benutzer erstellt: admin / admin")
        
        # Anzahl vorhandener Einträge ausgeben
        c.execute('SELECT COUNT(*) FROM players')
        player_count = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM drinks')
        drink_count = c.fetchone()[0]
        


def hash_password(password: str) -> str:
    """Hasht Passwort mit SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()


# === HAUPTKLASSE ===

class HandballTrackerApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "Handball Getränke-Tracker"
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.padding = 0
        self.page.scroll = ft.ScrollMode.AUTO
        
        self.current_user = None
        self.current_tab = 0
        self.is_admin = False
        self.login_error_banner = ft.Container(height=0)
        self.add_drink_success_banner = ft.Container(height=0)
        self.pending_user_delete = None
        
        logger.info("HandballTrackerApp wird initialisiert")
        
        self.load_session()
        self.setup_ui()
    
    def load_session(self):
        """Lädt gespeicherte Session aus Browser Storage"""
        try:
            if self.page.client_storage.contains_key("user_id"):
                user_id = self.page.client_storage.get("user_id")
                self.current_user = self.get_user_by_id(user_id)
                if self.current_user:
                    self.is_admin = self.current_user.get('is_admin', False)
                    logger.info(f"Session geladen: {self.current_user['full_name']} (Admin: {self.is_admin})")
        except Exception as e:
            logger.exception(f"Fehler beim Laden der Session: {e}")
    
    def save_session(self, user_id=None):
        """Speichert Session in Browser Storage"""
        try:
            if user_id:
                self.page.client_storage.set("user_id", user_id)
                logger.info(f"Session gespeichert: User ID {user_id}")
            else:
                self.page.client_storage.remove("user_id")
                logger.info("Session gelöscht")
        except Exception as e:
            logger.exception(f"Fehler beim Speichern der Session: {e}")
    
    # === DATENBANK-FUNKTIONEN ===
    
    def get_user_by_id(self, user_id):
        """Holt Benutzer-Daten aus Datenbank"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('SELECT id, username, full_name, is_admin FROM players WHERE id = ?', (user_id,))
                row = c.fetchone()
                if row:
                    return {
                        "player_id": row[0],
                        "username": row[1],
                        "full_name": row[2],
                        "is_admin": bool(row[3])
                    }
        except Exception as e:
            logger.exception(f"Fehler beim Abrufen des Benutzers: {e}")
        return None
    
    def register_user(self, username, password, full_name):
        """Registriert neuen Benutzer"""
        try:
            password_hash = hash_password(password)
            with get_db() as conn:
                c = conn.cursor()
                c.execute('INSERT INTO players (username, password_hash, full_name) VALUES (?, ?, ?)',
                         (username, password_hash, full_name))
                user_id = c.lastrowid
                logger.info(f"Neuer Spieler registriert: {full_name} (ID: {user_id})")
                return True, user_id, None
        except sqlite3.IntegrityError:
            logger.warning(f"Registrierung fehlgeschlagen: Username '{username}' bereits vergeben")
            return False, None, "Username bereits vergeben"
        except Exception as e:
            logger.exception(f"Fehler bei der Registrierung: {e}")
            return False, None, str(e)
    
    def login_user(self, username, password):
        """Authentifiziert Benutzer"""
        try:
            password_hash = hash_password(password)
            with get_db() as conn:
                c = conn.cursor()
                c.execute('SELECT id, username, full_name, is_admin FROM players WHERE username = ? AND password_hash = ?',
                         (username, password_hash))
                row = c.fetchone()
                if row:
                    logger.info(f"Erfolgreicher Login: {row[1]}")
                    return True, {
                        "player_id": row[0],
                        "username": row[1],
                        "full_name": row[2],
                        "is_admin": bool(row[3])
                    }
                logger.warning(f"Login fehlgeschlagen für User: {username}")
                return False, None
        except Exception as e:
            logger.exception(f"Fehler beim Login: {e}")
            return False, None
    
    def get_drink_types(self):
        """Holt alle verfügbaren Getränke-Typen"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('SELECT name, price FROM drink_types ORDER BY name')
                return [{"name": row[0], "price": row[1]} for row in c.fetchall()]
        except Exception as e:
            logger.exception(f"Fehler beim Abrufen der Getränketypen: {e}")
            return []
    
    def add_drink(self, player_id, drink_type, quantity, date, notes=""):
        """Fügt Getränk-Eintrag hinzu"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('INSERT INTO drinks (player_id, drink_type, quantity, date, notes) VALUES (?, ?, ?, ?, ?)',
                         (player_id, drink_type, quantity, date, notes))
                drink_id = c.lastrowid
                logger.info(f"Getränk eingetragen: {quantity}x {drink_type} für User ID {player_id} (Drink ID: {drink_id})")
                return True, drink_id
        except Exception as e:
            logger.exception(f"Fehler beim Speichern des Getränks: {e}")
            return False, str(e)
    
    def get_player_drinks(self, player_id):
        """Holt alle Getränke eines Spielers"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('''SELECT d.id, d.drink_type, d.quantity, d.date, d.notes, dt.price, d.paid
                            FROM drinks d
                            LEFT JOIN drink_types dt ON d.drink_type = dt.name
                            WHERE d.player_id = ?
                            ORDER BY d.date DESC, d.created_at DESC''', (player_id,))
                
                drinks = []
                for row in c.fetchall():
                    drinks.append({
                        "id": row[0],
                        "drink_type": row[1],
                        "quantity": row[2],
                        "date": row[3],
                        "notes": row[4],
                        "price": row[5] or 0.0,
                        "paid": bool(row[6])
                    })
                return drinks
        except Exception as e:
            logger.exception(f"Fehler beim Abrufen der Getränke für Player {player_id}: {e}")
            return []
    
    def delete_drink(self, drink_id):
        """Löscht einen Getränke-Eintrag"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('DELETE FROM drinks WHERE id = ?', (drink_id,))
                logger.info(f"Getränk gelöscht: ID {drink_id}")
                return True
        except Exception as e:
            logger.exception(f"Fehler beim Löschen des Getränks: {e}")
            return False
    
    def get_player_stats(self, player_id):
        """Berechnet Statistiken für einen Spieler - NUR UNBEZAHLTE GETRÄNKE"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('''SELECT d.drink_type, SUM(d.quantity) as total, dt.price
                            FROM drinks d
                            LEFT JOIN drink_types dt ON d.drink_type = dt.name
                            WHERE d.player_id = ? AND d.paid = 0
                            GROUP BY d.drink_type''', (player_id,))
                
                stats = []
                total_cost = 0.0
                
                for row in c.fetchall():
                    drink_type = row[0]
                    total_quantity = row[1]
                    price = row[2] or 0.0
                    cost = total_quantity * price
                    total_cost += cost
                    
                    stats.append({
                        "drink_type": drink_type,
                        "total_quantity": total_quantity,
                        "price_per_unit": price,
                        "total_cost": cost
                    })
                
                return stats, total_cost
        except Exception as e:
            logger.exception(f"Fehler beim Berechnen der Statistiken: {e}")
            return [], 0.0
    
    def get_team_stats(self):
        """Holt Team-weite Statistiken - ALLE GETRÄNKE"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('''SELECT p.full_name, d.drink_type, SUM(d.quantity) as total, dt.price
                            FROM drinks d
                            JOIN players p ON d.player_id = p.id
                            LEFT JOIN drink_types dt ON d.drink_type = dt.name
                            GROUP BY p.full_name, d.drink_type
                            ORDER BY total DESC''')
                
                team_data = {}
                for row in c.fetchall():
                    player = row[0]
                    drink = row[1]
                    quantity = row[2]
                    price = row[3] or 0.0
                    
                    if player not in team_data:
                        team_data[player] = {"drinks": {}, "total_cost": 0.0}
                    
                    team_data[player]["drinks"][drink] = quantity
                    team_data[player]["total_cost"] += quantity * price
                
                return team_data
        except Exception as e:
            logger.exception(f"Fehler beim Abrufen der Team-Statistiken: {e}")
            return {}
    
    # === ADMIN-FUNKTIONEN ===
    
    def get_all_users(self):
        """Holt alle Benutzer für Admin-View"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('''SELECT id, username, full_name, is_admin, created_at 
                            FROM players 
                            ORDER BY created_at DESC''')
                
                users = []
                for row in c.fetchall():
                    users.append({
                        "id": row[0],
                        "username": row[1],
                        "full_name": row[2],
                        "is_admin": bool(row[3]),
                        "created_at": row[4]
                    })
                return users
        except Exception as e:
            logger.exception(f"Fehler beim Abrufen aller Benutzer: {e}")
            return []
    
    def delete_user(self, user_id):
        """Löscht einen Benutzer und alle seine Getränke-Einträge"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                # Zuerst Getränke-Einträge löschen
                c.execute('DELETE FROM drinks WHERE player_id = ?', (user_id,))
                # Dann Benutzer löschen
                c.execute('DELETE FROM players WHERE id = ?', (user_id,))
                logger.info(f"Benutzer gelöscht: ID {user_id}")
                return True
        except Exception as e:
            logger.exception(f"Fehler beim Löschen des Benutzers: {e}")
            return False
    
    def get_all_drinks(self):
        """Holt alle Getränke für Admin-View"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('''SELECT d.id, p.full_name, d.drink_type, d.quantity, d.date, d.notes, dt.price, d.paid
                            FROM drinks d
                            JOIN players p ON d.player_id = p.id
                            LEFT JOIN drink_types dt ON d.drink_type = dt.name
                            ORDER BY d.date DESC, d.created_at DESC''')
                
                drinks = []
                for row in c.fetchall():
                    drinks.append({
                        "id": row[0],
                        "player_name": row[1],
                        "drink_type": row[2],
                        "quantity": row[3],
                        "date": row[4],
                        "notes": row[5],
                        "price": row[6] or 0.0,
                        "paid": bool(row[7])
                    })
                return drinks
        except Exception as e:
            logger.exception(f"Fehler beim Abrufen aller Getränke: {e}")
            return []
    
    def toggle_paid_status(self, drink_id, paid_status):
        """Markiert Getränk als bezahlt/nicht bezahlt"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('UPDATE drinks SET paid = ? WHERE id = ?', (paid_status, drink_id))
                status_text = "bezahlt" if paid_status else "offen"
                logger.info(f"Getränk {drink_id} als {status_text} markiert")
                return True
        except Exception as e:
            logger.exception(f"Fehler beim Aktualisieren des Bezahlstatus: {e}")
            return False
    
    def mark_drinks_paid_until_date(self, target_date, player_id=None):
        """Markiert alle Getränke bis zu einem bestimmten Datum als bezahlt."""
        try:
            with get_db() as conn:
                c = conn.cursor()
                if player_id is not None:
                    c.execute('UPDATE drinks SET paid = 1 WHERE date <= ? AND player_id = ?', (target_date, player_id))
                else:
                    c.execute('UPDATE drinks SET paid = 1 WHERE date <= ?', (target_date,))
                updated_count = c.rowcount
                logger.info(f"{updated_count} Getränke bis {target_date} als bezahlt markiert")
                return True, updated_count
        except Exception as e:
            logger.exception(f"Fehler beim Massen-Update: {e}")
            return False, 0
    
    def toggle_admin_status(self, user_id):
        """Ändert den Admin-Status eines Benutzers"""
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('SELECT is_admin FROM players WHERE id = ?', (user_id,))
                current_status = bool(c.fetchone()[0])
                new_status = not current_status
                
                c.execute('UPDATE players SET is_admin = ? WHERE id = ?', (new_status, user_id))
                status_text = "Administrator" if new_status else "normaler Benutzer"
                logger.info(f"Benutzer {user_id} ist jetzt {status_text}")
                return True, new_status
        except Exception as e:
            logger.exception(f"Fehler beim Ändern des Admin-Status: {e}")
            return False, None

    # === UI-FUNKTIONEN ===
    
    def setup_ui(self):
        """Baut die Haupt-UI auf"""
        self.page.clean()
        
        if not hasattr(self.page, 'theme_mode'):
            self.page.theme_mode = ft.ThemeMode.LIGHT
            
        self.page.theme = ft.Theme(
            color_scheme_seed=ft.Colors.BLUE,
            use_material3=True,
        )
        
        if self.current_user is None:
            self.show_login_screen()
        else:
            self.show_main_screen()
    
    def open_date_picker(self, picker):
        """Öffnet einen DatePicker"""
        picker.open = True
        self.page.update()
    
    def on_drink_date_change(self, e, button):
        """Handler für Datumänderungen im Getränke-Tab"""
        if e.data and button:
            date_str = e.data.split('T')[0]
            self.selected_date = datetime.strptime(date_str, "%Y-%m-%d")
            button.text = self.selected_date.strftime("%Y-%m-%d")
            self.page.update()
    
    def on_admin_date_change(self, e, button):
        """Handler für Datumänderungen im Admin-Tab"""
        if e.data and button:
            date_str = e.data.split('T')[0]
            self.admin_selected_date = datetime.strptime(date_str, "%Y-%m-%d")
            button.text = self.admin_selected_date.strftime("%Y-%m-%d")
            self.page.update()
    
    def show_snackbar(self, message: str, color):
        """Zeigt eine Snackbar-Benachrichtigung"""
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(message, color=ft.Colors.WHITE),
            bgcolor=color,
            duration=3000,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _clear_login_banner(self):
        try:
            self.login_error_banner.content = None
            self.login_error_banner.bgcolor = None
            self.login_error_banner.height = 0
            self.page.update()
        except Exception:
            pass

    def _clear_add_banner(self):
        try:
            self.add_drink_success_banner.content = None
            self.add_drink_success_banner.bgcolor = None
            self.add_drink_success_banner.height = 0
            self.page.update()
        except Exception:
            pass
    
    def show_login_screen(self):
        """Zeigt Login-Bildschirm"""
        logger.debug("Zeige Login-Bildschirm")
        
        self.page.appbar = ft.AppBar(
            title=ft.Text("Handball Tracker - Login"),
            center_title=True,
            bgcolor=ft.Colors.BLUE_700,
        )
        
        login_error_banner = self.login_error_banner
        
        username_field = ft.TextField(
            label="Username",
            prefix_icon=ft.Icons.PERSON,
            autofocus=True,
            width=300,
        )
        
        password_field = ft.TextField(
            label="Passwort",
            prefix_icon=ft.Icons.LOCK,
            password=True,
            can_reveal_password=True,
            width=300,
        )
        
        def login_clicked(e):
            if not username_field.value or not password_field.value:
                login_error_banner.content = ft.Text("Bitte alle Felder ausfüllen", color=ft.Colors.WHITE)
                login_error_banner.bgcolor = ft.Colors.RED
                login_error_banner.padding = ft.Padding(10, 8, 10, 8)
                login_error_banner.border_radius = 6
                login_error_banner.height = None
                self.page.update()
                self.show_snackbar("Bitte alle Felder ausfüllen", ft.Colors.RED)
                threading.Timer(3.0, lambda: self._clear_login_banner()).start()
                return
            
            success, user = self.login_user(username_field.value, password_field.value)
            
            if success:
                login_error_banner.content = None
                login_error_banner.bgcolor = None
                login_error_banner.height = 0
                self.current_user = user
                self.is_admin = user.get('is_admin', False)
                self.save_session(user["player_id"])
                self.show_snackbar(f"Willkommen, {user['full_name']}!", ft.Colors.GREEN)
                self.setup_ui()
            else:
                login_error_banner.content = ft.Text("Falscher Benutzername oder falsches Passwort", color=ft.Colors.WHITE)
                login_error_banner.bgcolor = ft.Colors.RED
                login_error_banner.padding = ft.Padding(10, 8, 10, 8)
                login_error_banner.border_radius = 6
                login_error_banner.height = None
                self.page.update()
                self.show_snackbar("Falscher Username oder Passwort", ft.Colors.RED)
                threading.Timer(3.0, lambda: self._clear_login_banner()).start()
        
        def register_clicked(e):
            self.show_register_screen()
        
        login_button = ft.ElevatedButton(
            "Einloggen",
            icon=ft.Icons.LOGIN,
            on_click=login_clicked,
            width=300,
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.BLUE_700,
                color=ft.Colors.WHITE,
            ),
        )
        
        register_button = ft.TextButton(
            "Noch kein Account? Registrieren",
            on_click=register_clicked,
        )
        
        self.page.add(
            ft.Container(
                content=ft.Column(
                    [
                        login_error_banner,
                        ft.Container(height=20),
                        ft.Icon(ft.Icons.SPORTS_HANDBALL, size=100, color=ft.Colors.BLUE_700),
                        ft.Text("Handball Getränke-Tracker", 
                               size=24, 
                               weight=ft.FontWeight.BOLD, 
                               text_align=ft.TextAlign.CENTER),
                        ft.Container(height=20),
                        username_field,
                        password_field,
                        ft.Container(height=10),
                        login_button,
                        register_button,
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=10,
                ),
                padding=40,
                alignment=ft.Alignment(0, 0),
            )
        )
    
    def show_register_screen(self):
        """Zeigt Registrierungs-Bildschirm"""
        logger.debug("Zeige Registrierungsbildschirm")
        
        self.page.clean()
        
        self.page.appbar = ft.AppBar(
            title=ft.Text("Registrierung"),
            center_title=True,
            bgcolor=ft.Colors.BLUE_700,
            leading=ft.IconButton(
                ft.Icons.ARROW_BACK,
                on_click=lambda e: self.setup_ui()
            ),
        )
        
        full_name_field = ft.TextField(
            label="Vollständiger Name",
            prefix_icon=ft.Icons.PERSON,
            width=300,
        )
        
        username_field = ft.TextField(
            label="Username",
            prefix_icon=ft.Icons.ACCOUNT_CIRCLE,
            width=300,
        )
        
        password_field = ft.TextField(
            label="Passwort",
            prefix_icon=ft.Icons.LOCK,
            password=True,
            width=300,
        )
        
        password_confirm_field = ft.TextField(
            label="Passwort bestätigen",
            prefix_icon=ft.Icons.LOCK,
            password=True,
            width=300,
        )
        
        def register_submit(e):
            if not all([full_name_field.value, username_field.value, password_field.value]):
                self.show_snackbar("Bitte alle Felder ausfüllen", ft.Colors.RED)
                return
            
            if password_field.value != password_confirm_field.value:
                self.show_snackbar("Passwörter stimmen nicht überein", ft.Colors.RED)
                return
            
            if len(password_field.value) < 4:
                self.show_snackbar("Passwort muss mind. 4 Zeichen haben", ft.Colors.RED)
                return
            
            success, user_id, error = self.register_user(
                username_field.value,
                password_field.value,
                full_name_field.value
            )
            
            if success:
                self.show_snackbar("Registrierung erfolgreich! Bitte einloggen.", ft.Colors.GREEN)
                self.setup_ui()
            else:
                self.show_snackbar(error or "Registrierung fehlgeschlagen", ft.Colors.RED)
        
        self.page.add(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Container(height=20),
                        ft.Icon(ft.Icons.PERSON_ADD, size=80, color=ft.Colors.BLUE_700),
                        ft.Text("Neuen Account erstellen", size=20, weight=ft.FontWeight.BOLD),
                        ft.Container(height=20),
                        full_name_field,
                        username_field,
                        password_field,
                        password_confirm_field,
                        ft.Container(height=10),
                        ft.ElevatedButton(
                            "Registrieren",
                            icon=ft.Icons.CHECK,
                            on_click=register_submit,
                            width=300,
                            style=ft.ButtonStyle(
                                bgcolor=ft.Colors.BLUE_700,
                                color=ft.Colors.WHITE,
                            ),
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=10,
                ),
                padding=40,
            )
        )
    
    def show_main_screen(self):
        """Zeigt Haupt-Bildschirm mit Tabs"""
        logger.debug(f"Zeige Hauptbildschirm für {self.current_user['full_name']}")
        
        self.page.clean()
        
        def logout_clicked(e):
            self.current_user = None
            self.is_admin = False
            self.save_session()
            logger.info("Benutzer abgemeldet")
            self.setup_ui()
        
        def toggle_theme(e):
            self.page.theme_mode = (
                ft.ThemeMode.DARK
                if self.page.theme_mode == ft.ThemeMode.LIGHT
                else ft.ThemeMode.LIGHT
            )
            self.page.update()

        self.page.appbar = ft.AppBar(
            title=ft.Text(f"Hallo, {self.current_user['full_name']}"),
            center_title=True,
            bgcolor=ft.Colors.BLUE_700,
            actions=[
                ft.IconButton(
                    ft.Icons.DARK_MODE if self.page.theme_mode == ft.ThemeMode.LIGHT else ft.Icons.LIGHT_MODE,
                    on_click=toggle_theme,
                    tooltip="Dark/Light Mode"
                ),
                ft.IconButton(
                    ft.Icons.LOGOUT,
                    on_click=logout_clicked,
                    tooltip="Ausloggen"
                ),
            ],
        )
        
        def navigation_change(e):
            self.current_tab = e.control.selected_index
            self.page.overlay.clear()
            self.update_tab_content()
        
        destinations = [
            ft.NavigationBarDestination(
                icon=ft.Icons.ADD_CIRCLE_OUTLINE,
                selected_icon=ft.Icons.ADD_CIRCLE,
                label="Eintragen"
            ),
            ft.NavigationBarDestination(
                icon=ft.Icons.HISTORY_OUTLINED,
                selected_icon=ft.Icons.HISTORY,
                label="Historie"
            ),
            ft.NavigationBarDestination(
                icon=ft.Icons.BAR_CHART_OUTLINED,
                selected_icon=ft.Icons.BAR_CHART,
                label="Statistik"
            ),
            ft.NavigationBarDestination(
                icon=ft.Icons.GROUPS_OUTLINED,
                selected_icon=ft.Icons.GROUPS,
                label="Team"
            ),
        ]
        
        if self.is_admin:
            destinations.append(
                ft.NavigationBarDestination(
                    icon=ft.Icons.ADMIN_PANEL_SETTINGS_OUTLINED,
                    selected_icon=ft.Icons.ADMIN_PANEL_SETTINGS,
                    label="Admin"
                )
            )
        
        self.page.navigation_bar = ft.NavigationBar(
            destinations=destinations,
            selected_index=self.current_tab,
            on_change=navigation_change,
        )
        
        self.tab_content = ft.Container(expand=True)
        
        self.page.add(self.tab_content)
        self.update_tab_content()
    
    def update_tab_content(self):
        """Aktualisiert Tab-Inhalt basierend auf ausgewähltem Tab"""
        content = None
        
        if self.current_tab == 0:
            content = self.show_add_drink_tab()
        elif self.current_tab == 1:
            content = self.show_history_tab()
        elif self.current_tab == 2:
            content = self.show_stats_tab()
        elif self.current_tab == 3:
            content = self.show_team_tab()
        elif self.current_tab == 4 and self.is_admin:
            content = self.show_admin_tab()
        
        if content:
            self.tab_content.content = content
            self.page.update()
    
    def show_add_drink_tab(self):
        """Tab: Getränk eintragen"""
        logger.debug("Lade Getränk-Tab")
        
        drink_types = self.get_drink_types()

        if not hasattr(self, 'selected_date'):
            self.selected_date = datetime.now()

        add_success_banner = self.add_drink_success_banner

        drink_date_picker = ft.DatePicker(
            on_change=lambda e: self.on_drink_date_change(e, None),
            first_date=datetime(2020, 1, 1),
            last_date=datetime(2030, 12, 31),
        )

        date_button = ft.ElevatedButton(
            text=self.selected_date.strftime("%Y-%m-%d"),
            icon=ft.Icons.CALENDAR_MONTH,
            on_click=lambda _: self.open_date_picker(drink_date_picker),
            width=300,
        )

        drink_date_picker.on_change = lambda e: self.on_drink_date_change(e, date_button)
        self.page.overlay.append(drink_date_picker)

        drink_dropdown = ft.Dropdown(
            label="Getränk",
            options=[ft.DropdownOption(d["name"]) for d in drink_types],
            width=300,
        )

        quantity_field = ft.TextField(
            label="Anzahl",
            value="1",
            keyboard_type=ft.KeyboardType.NUMBER,
            width=300,
        )

        notes_field = ft.TextField(
            label="Notizen (optional)",
            multiline=True,
            max_lines=3,
            width=300,
        )

        def save_drink(e):
            if not drink_dropdown.value:
                self.show_snackbar("Bitte Getränk auswählen", ft.Colors.RED)
                return

            try:
                quantity = int(quantity_field.value)
                if quantity <= 0:
                    raise ValueError()
            except Exception:
                self.show_snackbar("Bitte gültige Anzahl eingeben", ft.Colors.RED)
                return

            selected_date = self.selected_date.strftime("%Y-%m-%d")

            success, result = self.add_drink(
                self.current_user['player_id'],
                drink_dropdown.value,
                quantity,
                selected_date,
                notes_field.value or ""
            )

            if success:
                add_success_banner.content = ft.Text(f"{quantity}x {drink_dropdown.value} eingetragen!", color=ft.Colors.WHITE)
                add_success_banner.bgcolor = ft.Colors.GREEN_700
                add_success_banner.padding = ft.Padding(10, 8, 10, 8)
                add_success_banner.border_radius = 6
                add_success_banner.height = None
                self.page.update()
                self.show_snackbar(f"{quantity}x {drink_dropdown.value} eingetragen!", ft.Colors.GREEN)
                threading.Timer(3.0, lambda: self._clear_add_banner()).start()
                self.tab_content.content = self.show_add_drink_tab()
                self.page.update()
            else:
                self.show_snackbar(result or "Fehler beim Speichern", ft.Colors.RED)

        content = ft.Column(
            [
                add_success_banner,
                ft.Container(height=20),
                ft.Text("Getränk eintragen", size=24, weight=ft.FontWeight.BOLD),
                ft.Container(height=20),
                drink_dropdown,
                quantity_field,
                date_button,
                notes_field,
                ft.Container(height=10),
                ft.ElevatedButton(
                    "Speichern",
                    icon=ft.Icons.SAVE,
                    on_click=save_drink,
                    width=300,
                    style=ft.ButtonStyle(
                        bgcolor=ft.Colors.GREEN_700,
                        color=ft.Colors.WHITE,
                    ),
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.AUTO,
        )

        return content
    
    def show_history_tab(self):
        """Tab: Historie anzeigen"""
        logger.debug("Lade Historie-Tab")
        
        if not self.current_user:
            self.tab_content.content = ft.Container(
                content=ft.Column(
                    [
                        ft.Container(height=100),
                        ft.Icon(ft.Icons.WARNING, size=80, color=ft.Colors.ORANGE_700),
                        ft.Text("Bitte zuerst einloggen", size=18, color=ft.Colors.GREY_700),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=40,
            )
            self.page.update()
            return

        drinks = self.get_player_drinks(self.current_user['player_id'])
        
        if not drinks:
            self.tab_content.content = ft.Container(
                content=ft.Column(
                    [
                        ft.Container(height=100),
                        ft.Icon(ft.Icons.INBOX, size=100, color=ft.Colors.GREY_400),
                        ft.Text("Noch keine Einträge", size=20, color=ft.Colors.GREY_600),
                        ft.Text("Trage dein erstes Getränk ein!", size=14, color=ft.Colors.GREY_500),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=40,
            )
            self.page.update()
            return
        
        drink_list = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)
        
        for drink in drinks:
            def make_delete_handler(drink_id):
                def delete(e):
                    if self.delete_drink(drink_id):
                        self.show_snackbar("Eintrag gelöscht", ft.Colors.GREEN)
                        self.show_history_tab()
                    else:
                        self.show_snackbar("Fehler beim Löschen", ft.Colors.RED)
                return delete
            
            status_badge = ft.Container(
                content=ft.Text(
                    "Bezahlt" if drink['paid'] else "● Offen",
                    color=ft.Colors.GREEN_700 if drink['paid'] else ft.Colors.ORANGE_700,
                    size=12,
                    weight=ft.FontWeight.BOLD
                ),
                padding=ft.Padding(8, 4, 8, 4),
                bgcolor=ft.Colors.GREEN_100 if drink['paid'] else ft.Colors.ORANGE_100,
                border_radius=10,
            )
            
            drink_card = ft.Card(
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.LOCAL_DRINK, color=ft.Colors.BLUE_700, size=30),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                f"{drink['quantity']}x {drink['drink_type']}",
                                                size=16,
                                                weight=ft.FontWeight.BOLD
                                            ),
                                            ft.Text(
                                                drink['date'],
                                                size=12,
                                                color=ft.Colors.GREY_600
                                            ),
                                        ],
                                        spacing=2,
                                        expand=True,
                                    ),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                f"€{drink['price'] * drink['quantity']:.2f}",
                                                size=16,
                                                weight=ft.FontWeight.BOLD
                                            ),
                                            status_badge,
                                        ],
                                        horizontal_alignment=ft.CrossAxisAlignment.END,
                                    ),
                                ],
                            ),
                            ft.Text(
                                drink['notes'],
                                size=12,
                                color=ft.Colors.GREY_700
                            ) if drink.get('notes') else ft.Container(height=0),
                            ft.Row(
                                [
                                    ft.Container(expand=True),
                                    ft.IconButton(
                                        ft.Icons.DELETE,
                                        icon_color=ft.Colors.RED_400,
                                        icon_size=20,
                                        on_click=make_delete_handler(drink['id']),
                                    ) if self.is_admin and not drink['paid'] else ft.Container(height=0),
                                ]
                            ),
                        ],
                    ),
                    padding=15,
                ),
            )
            drink_list.controls.append(drink_card)
        
        self.tab_content.content = ft.Container(
            content=drink_list,
            padding=20,
        )
        self.page.update()
    
    def show_stats_tab(self):
        """Tab: Persönliche Statistiken"""
        logger.debug("Lade Statistik-Tab")
        
        if not self.current_user:
            self.tab_content.content = ft.Container(
                content=ft.Column(
                    [
                        ft.Container(height=100),
                        ft.Icon(ft.Icons.WARNING, size=80, color=ft.Colors.ORANGE_700),
                        ft.Text("Bitte zuerst einloggen", size=18, color=ft.Colors.GREY_700),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=40,
            )
            self.page.update()
            return

        stats, total_cost = self.get_player_stats(self.current_user['player_id'])
        
        stats_column = ft.Column(spacing=15, scroll=ft.ScrollMode.AUTO)
        
        stats_column.controls.append(
            ft.Container(
                content=ft.Text("Deine offenen Beträge", size=24, weight=ft.FontWeight.BOLD),
                padding=20,
            )
        )
        
        stats_column.controls.append(
            ft.Container(
                content=ft.Text("Nur unbezahlte Getränke werden angezeigt", 
                               size=12, color=ft.Colors.GREY_600),
                padding=ft.Padding(20, 0, 0, 10),
            )
        )
        
        if not stats:
            stats_column.controls.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Container(height=50),
                            ft.Icon(ft.Icons.BAR_CHART, size=100, color=ft.Colors.GREY_400),
                            ft.Text("Keine offenen Beträge", size=20, color=ft.Colors.GREY_600),
                            ft.Text("Alle Getränke sind bezahlt oder du hast noch keine eingetragen", 
                                   size=14, color=ft.Colors.GREY_500),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=40,
                )
            )
        else:
            for stat in stats:
                stat_card = ft.Card(
                    content=ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Icon(ft.Icons.LOCAL_DRINK, 
                                               color=ft.Colors.BLUE_700, 
                                               size=30),
                                        ft.Text(stat['drink_type'], 
                                               size=18, 
                                               weight=ft.FontWeight.BOLD),
                                    ],
                                ),
                                ft.Divider(),
                                ft.Row(
                                    [
                                        ft.Text("Gesamt:", weight=ft.FontWeight.BOLD),
                                        ft.Text(f"{stat['total_quantity']} Stück"),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                ft.Row(
                                    [
                                        ft.Text("Preis/Stück:", weight=ft.FontWeight.BOLD),
                                        ft.Text(f"€{stat['price_per_unit']:.2f}"),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                ft.Row(
                                    [
                                        ft.Text("Kosten:", weight=ft.FontWeight.BOLD),
                                        ft.Text(
                                            f"€{stat['total_cost']:.2f}",
                                            color=ft.Colors.RED_700,
                                            weight=ft.FontWeight.BOLD
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                            ],
                            spacing=10,
                        ),
                        padding=20,
                    ),
                )
                stats_column.controls.append(stat_card)
            
            total_card = ft.Card(
                content=ft.Container(
                    content=ft.Row(
                        [
                            ft.Text("OFFENER BETRAG:", size=20, weight=ft.FontWeight.BOLD),
                            ft.Text(
                                f"€{total_cost:.2f}",
                                size=24,
                                weight=ft.FontWeight.BOLD,
                                color=ft.Colors.RED_700
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    padding=20,
                    bgcolor=ft.Colors.BLUE_50,
                ),
            )
            stats_column.controls.append(total_card)
        
        self.tab_content.content = ft.Container(
            content=stats_column,
            padding=20,
        )
        self.page.update()
    
    def show_team_tab(self):
        """Tab: Team-Statistiken"""
        logger.debug("Lade Team-Tab")
        
        team_data = self.get_team_stats()
        
        team_column = ft.Column(spacing=15, scroll=ft.ScrollMode.AUTO)
        
        team_column.controls.append(
            ft.Container(
                content=ft.Text("Team-Statistiken", size=24, weight=ft.FontWeight.BOLD),
                padding=20,
            )
        )
        
        team_column.controls.append(
            ft.Container(
                content=ft.Text("Alle Getränke (bezahlt und unbezahlt)", 
                               size=12, color=ft.Colors.GREY_600),
                padding=ft.Padding(20, 0, 0, 10),
            )
        )
        
        if not team_data:
            team_column.controls.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Container(height=50),
                            ft.Icon(ft.Icons.GROUPS, size=100, color=ft.Colors.GREY_400),
                            ft.Text("Noch keine Team-Daten", size=20, color=ft.Colors.GREY_600),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=40,
                )
            )
        else:
            sorted_players = sorted(
                team_data.items(),
                key=lambda x: x[1]['total_cost'],
                reverse=True
            )
            
            for rank, (player_name, data) in enumerate(sorted_players, 1):
                medal = ""
                if rank == 1:
                    medal = "🥇 "
                elif rank == 2:
                    medal = "🥈 "
                elif rank == 3:
                    medal = "🥉 "
                
                drinks_text = ", ".join([f"{drink}: {qty}x" 
                                        for drink, qty in data['drinks'].items()])
                
                player_card = ft.Card(
                    content=ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(f"{rank}.", size=20, weight=ft.FontWeight.BOLD),
                                        ft.Icon(ft.Icons.PERSON, color=ft.Colors.BLUE_700, size=30),
                                        ft.Text(
                                            f"{medal}{player_name}",
                                            size=18,
                                            weight=ft.FontWeight.BOLD,
                                            expand=True,
                                        ),
                                        ft.Text(
                                            f"€{data['total_cost']:.2f}",
                                            size=18,
                                            weight=ft.FontWeight.BOLD,
                                            color=ft.Colors.RED_700,
                                        ),
                                    ],
                                ),
                                ft.Divider(),
                                ft.Text(drinks_text, size=12, color=ft.Colors.GREY_700),
                            ],
                            spacing=8,
                        ),
                        padding=20,
                    ),
                )
                team_column.controls.append(player_card)
        
        self.tab_content.content = ft.Container(
            content=team_column,
            padding=20,
        )
        self.page.update()
    
    def show_admin_tab(self):
        """Tab: Admin-Funktionen"""
        logger.debug("Lade Admin-Tab")
        
        admin_column = ft.Column(spacing=20, scroll=ft.ScrollMode.AUTO)
        
        admin_column.controls.append(
            ft.Container(
                content=ft.Text("Admin-Bereich", size=24, weight=ft.FontWeight.BOLD),
                padding=20,
            )
        )
        
        if not hasattr(self, 'admin_selected_date'):
            self.admin_selected_date = datetime.now()
        
        admin_date_picker = ft.DatePicker(
            on_change=lambda e: self.on_admin_date_change(e, None),
            first_date=datetime(2020, 1, 1),
            last_date=datetime(2030, 12, 31),
        )
        
        admin_date_button = ft.ElevatedButton(
            text=self.admin_selected_date.strftime("%Y-%m-%d"),
            icon=ft.Icons.CALENDAR_MONTH,
            on_click=lambda _: self.open_date_picker(admin_date_picker),
            width=300,
        )
        
        admin_date_picker.on_change = lambda e: self.on_admin_date_change(e, admin_date_button)
        self.page.overlay.append(admin_date_picker)
        
        users_for_dropdown = self.get_all_users()
        users_options = [ft.DropdownOption(f"{u['id']}|{u['full_name']}") for u in users_for_dropdown]
        users_dropdown = ft.Dropdown(
            label="Benutzer",
            options=users_options,
            width=300,
        )
        if users_options:
            try:
                users_dropdown.value = users_options[0].text
            except Exception:
                users_dropdown.value = f"{users_for_dropdown[0]['id']}|{users_for_dropdown[0]['full_name']}"

        def mark_paid_until(e):
            if not users_dropdown.value:
                self.show_snackbar("Bitte Benutzer auswählen", ft.Colors.RED)
                return

            selected_user_id = None
            val = users_dropdown.value
            try:
                selected_user_id = int(str(val).split("|")[0])
            except Exception:
                for u in users_for_dropdown:
                    if u['full_name'] == val or u['username'] == val:
                        selected_user_id = u['id']
                        break
            if selected_user_id is None:
                self.show_snackbar("Ungültiger Benutzer ausgewählt", ft.Colors.RED)
                return

            selected_date = None
            if hasattr(self, 'admin_selected_date') and self.admin_selected_date:
                selected_date = self.admin_selected_date.strftime("%Y-%m-%d")
            elif admin_date_picker.value:
                selected_date = admin_date_picker.value.strftime("%Y-%m-%d")

            if not selected_date:
                self.show_snackbar("Bitte zuerst ein Datum auswählen", ft.Colors.RED)
                return

            success, count = self.mark_drinks_paid_until_date(selected_date, selected_user_id)
            if success:
                self.show_snackbar(f"{count} Getränke bis {selected_date} als bezahlt markiert", ft.Colors.GREEN)
                self.show_admin_tab()
            else:
                self.show_snackbar("Fehler beim Markieren der Getränke", ft.Colors.RED)
        
        mark_paid_section = ft.Card(
            content=ft.Container(
                content=ft.Column([
                    ft.Text("Getränke als bezahlt markieren", size=20, weight=ft.FontWeight.BOLD),
                    ft.Divider(),
                    ft.Row([users_dropdown]),
                    ft.Row([admin_date_button]),
                    ft.ElevatedButton(
                        "Alle Getränke bis Stichtag als bezahlt markieren", 
                        icon=ft.Icons.CHECK_CIRCLE,
                        on_click=mark_paid_until,
                        style=ft.ButtonStyle(
                            bgcolor=ft.Colors.BLUE_700,
                            color=ft.Colors.WHITE
                        )
                    )
                ]),
                padding=20
            )
        )
        
        users_section = ft.Card(
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text("Benutzerverwaltung", size=20, weight=ft.FontWeight.BOLD),
                        ft.Divider(),
                        self.create_users_list(),
                    ],
                    spacing=10,
                ),
                padding=20,
            )
        )
        
        drinks_section = ft.Card(
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text("Getränkeverwaltung", size=20, weight=ft.FontWeight.BOLD),
                        ft.Divider(),
                        self.create_drinks_list(),
                    ],
                    spacing=10,
                ),
                padding=20,
            )
        )
        
        admin_column.controls.append(mark_paid_section)
        admin_column.controls.append(users_section)
        admin_column.controls.append(drinks_section)

        try:
            log_text = get_log_lines(300)
        except Exception:
            log_text = "Fehler beim Laden der Logs."

        logs_section = ft.Card(
            content=ft.Container(
                content=ft.Column([
                    ft.Text("Anwendungs-Logs", size=20, weight=ft.FontWeight.BOLD),
                    ft.Divider(),
                    ft.Container(
                        content=ft.ListView(
                            controls=[
                                ft.Text(log_text, size=12),
                                ],
                            expand=True,
                            auto_scroll=True,
                            ),
                        height=300,
                        padding=10,
                    ),
                ]),
                padding=10,
            )
        )

        admin_column.controls.append(logs_section)
        
        self.tab_content.content = ft.Container(
            content=admin_column,
            padding=20,
        )
        self.page.update()
    
    def toggle_admin(self, user_id, e):
        """Handler für Admin-Status-Toggle"""
        success, new_status = self.toggle_admin_status(user_id)
        if success:
            status_text = "Administrator" if new_status else "normaler Benutzer"
            self.show_snackbar(f"Benutzer ist jetzt {status_text}", ft.Colors.GREEN)
            self.show_admin_tab()
        else:
            self.show_snackbar("Fehler beim Ändern des Admin-Status", ft.Colors.RED)

    def create_users_list(self):
        """Erstellt die Benutzerliste für den Admin-Bereich"""
        users = self.get_all_users()
        users_list = ft.Column(spacing=10)
        
        if not users:
            users_list.controls.append(
                ft.Text("Keine Benutzer gefunden", color=ft.Colors.GREY_600)
            )
            return users_list
        
        for user in users:
            def make_delete_handler(user_id, username):
                def start_confirm(e):
                    self.pending_user_delete = user_id
                    self.show_admin_tab()

                def confirm_delete_inline(e):
                    if self.delete_user(user_id):
                        self.show_snackbar(f"Benutzer {username} gelöscht", ft.Colors.GREEN)
                    else:
                        self.show_snackbar("Fehler beim Löschen", ft.Colors.RED)
                    self.pending_user_delete = None
                    self.show_admin_tab()

                def cancel_delete_inline(e):
                    self.pending_user_delete = None
                    self.show_admin_tab()

                return {
                    'start': start_confirm,
                    'confirm': confirm_delete_inline,
                    'cancel': cancel_delete_inline
                }
            
            user_card = ft.Card(
                content=ft.Container(
                    content=ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text(user['full_name'], weight=ft.FontWeight.BOLD),
                                    ft.Text(f"@{user['username']}", size=12, color=ft.Colors.GREY_600),
                                    ft.Text(
                                        f"Admin: {'Ja' if user['is_admin'] else 'Nein'} • Erstellt: {user['created_at'][:10]}",
                                        size=10,
                                        color=ft.Colors.GREY_500
                                    ),
                                ],
                                expand=True,
                            ),
                            (lambda uid=user['id'], uname=user['username']: (
                                (ft.Row([
                                    ft.Text("Löschen?", size=12, color=ft.Colors.RED_700),
                                    ft.ElevatedButton("Ja", on_click=make_delete_handler(uid, uname)['confirm'], style=ft.ButtonStyle(bgcolor=ft.Colors.RED_700, color=ft.Colors.WHITE)),
                                    ft.TextButton("Nein", on_click=make_delete_handler(uid, uname)['cancel']),
                                ])) if self.pending_user_delete == uid else ft.Row([
                                    ft.IconButton(
                                        ft.Icons.ADMIN_PANEL_SETTINGS if user['is_admin'] else ft.Icons.PERSON_OUTLINE,
                                        icon_color=ft.Colors.BLUE_700 if user['is_admin'] else ft.Colors.GREY_700,
                                        tooltip="Admin-Status ändern",
                                        on_click=lambda e, u=user['id']: self.toggle_admin(u, e),
                                    ),
                                    ft.IconButton(
                                        ft.Icons.DELETE,
                                        icon_color=ft.Colors.RED_400,
                                        tooltip="Benutzer löschen",
                                        on_click=make_delete_handler(uid, uname)['start'],
                                    ),
                                ])
                            ))() if user['id'] != self.current_user['player_id'] else ft.Container(
                                content=ft.Text("Aktueller User", size=12, color=ft.Colors.GREY_500),
                                padding=10,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    padding=15,
                ),
            )
            users_list.controls.append(user_card)
        
        return users_list
    
    def create_drinks_list(self):
        """Erstellt die Getränkeliste für den Admin-Bereich"""
        drinks = self.get_all_drinks()
        drinks_list = ft.Column(spacing=10)
        
        if not drinks:
            drinks_list.controls.append(
                ft.Text("Keine Getränke-Einträge gefunden", color=ft.Colors.GREY_600)
            )
            return drinks_list
        
        for drink in drinks:
            def make_toggle_handler(drink_id, current_status):
                def toggle(e):
                    new_status = not current_status
                    if self.toggle_paid_status(drink_id, new_status):
                        self.show_snackbar(
                            f"Getränk als {'bezahlt' if new_status else 'offen'} markiert", 
                            ft.Colors.GREEN
                        )
                        self.show_admin_tab()
                    else:
                        self.show_snackbar("Fehler beim Aktualisieren", ft.Colors.RED)
                return toggle
            
            def make_delete_handler(drink_id):
                def delete(e):
                    if self.delete_drink(drink_id):
                        self.show_snackbar("Getränk gelöscht", ft.Colors.GREEN)
                        self.show_admin_tab()
                    else:
                        self.show_snackbar("Fehler beim Löschen", ft.Colors.RED)
                return delete
            
            status_color = ft.Colors.GREEN_700 if drink['paid'] else ft.Colors.ORANGE_700
            status_bg = ft.Colors.GREEN_100 if drink['paid'] else ft.Colors.ORANGE_100
            
            status_badge = ft.Container(
                content=ft.Text(
                    "Bezahlt" if drink['paid'] else "● Offen",
                    color=status_color,
                    size=12,
                    weight=ft.FontWeight.BOLD
                ),
                padding=ft.Padding(8, 4, 8, 4),
                bgcolor=status_bg,
                border_radius=10,
            )
            
            toggle_button = ft.ElevatedButton(
                text="Als bezahlt markieren" if not drink['paid'] else "Als offen markieren",
                icon=ft.Icons.CHECK if not drink['paid'] else ft.Icons.CLOSE,
                on_click=make_toggle_handler(drink['id'], drink['paid']),
                style=ft.ButtonStyle(
                    bgcolor=ft.Colors.GREEN_700 if not drink['paid'] else ft.Colors.ORANGE_700,
                    color=ft.Colors.WHITE,
                ),
            )
            
            drink_card = ft.Card(
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.LOCAL_DRINK, color=ft.Colors.BLUE_700, size=24),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                f"{drink['quantity']}x {drink['drink_type']}",
                                                weight=ft.FontWeight.BOLD
                                            ),
                                            ft.Text(
                                                f"von {drink['player_name']} • {drink['date']}",
                                                size=12,
                                                color=ft.Colors.GREY_600
                                            ),
                                        ],
                                        expand=True,
                                    ),
                                    status_badge,
                                ],
                            ),
                            ft.Text(
                                f"Preis: €{drink['price'] * drink['quantity']:.2f}",
                                size=14,
                                weight=ft.FontWeight.BOLD
                            ),
                            ft.Text(
                                drink['notes'],
                                size=12,
                                color=ft.Colors.GREY_700
                            ) if drink.get('notes') else ft.Container(height=0),
                            ft.Row(
                                [
                                    toggle_button,
                                    ft.IconButton(
                                        ft.Icons.DELETE,
                                        icon_color=ft.Colors.RED_400,
                                        tooltip="Eintrag löschen",
                                        on_click=make_delete_handler(drink['id']),
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.END,
                            ),
                        ],
                        spacing=8,
                    ),
                    padding=15,
                ),
            )
            drinks_list.controls.append(drink_card)
        
        return drinks_list


def main(page: ft.Page):
    """Einstiegspunkt der Anwendung"""
    init_database()
    
    HandballTrackerApp(page)

if __name__ == "__main__":
    ft.app(target=main)