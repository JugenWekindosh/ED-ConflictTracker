import discord
from discord.ext import tasks, commands
import zmq
import zmq.asyncio
import zlib
import json
import os
import io
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo

from core import get_connection, setup_db, upsert_conflict, get_active_conflicts, cleanup_old_conflicts, print_active_conflicts
from core import extract_relevant_conflicts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
ICONS_PATH = os.path.join(BASE_DIR, 'icons')
load_dotenv(ENV_PATH)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.getenv("DISCORD_CHANNEL_ID")

if not DISCORD_TOKEN or not CHANNEL_ID_RAW:
    raise ValueError("ERROR: Variables DISCORD_TOKEN or DISCORD_CHANNEL_ID missing in .env file")
DISCORD_CHANNEL_ID = int(CHANNEL_ID_RAW)

# ---- CONFIGURATIONS ----
TARGET_FACTIONS = ["MCC 445 Services", "Expanders Corp"]
relayEDDN = "tcp://eddn.edcd.io:9500"
timeoutEDDN = 600000

# Bot class
class FactionBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='>', intents=intents)

        # Initialize database at start
        self.db_conn = get_connection(os.path.join(BASE_DIR, 'data', 'conflicts.db'))
        setup_db(self.db_conn)
        self.first_run = True

    async def on_ready(self):
        print(f'Bot online as {self.user}')
        if self.first_run:
            await self.refresh_status_channel()
            await self.send_conflicts_status()
            self.first_run = False

    async def setup_hook(self):
        self.eddn_listener.start()
        self.daily_cleanup.start()




    # EDDN Listener
    @tasks.loop()
    async def eddn_listener(self):
        """Background task listening to ZMQ messages flux from EDDN relay"""
        print(f'Listening conflicts for: {TARGET_FACTIONS}')
        ctx = zmq.asyncio.Context()
        subscriber = ctx.socket(zmq.SUB)
        subscriber.setsockopt(zmq.SUBSCRIBE, b"")
        subscriber.setsockopt(zmq.RCVTIMEO, timeoutEDDN)

        subscriber.connect(relayEDDN)
        print(f"Connected to {relayEDDN}")

        while True:
            try:
                # La ricezione è awaitable, quindi Discord non si blocca
                message = await subscriber.recv()
                content = zlib.decompress(message)
                data = json.loads(content)

                # Handle Journal schema message
                if data.get('$schemaRef') == "https://eddn.edcd.io/schemas/journal/1":
                    conflicts = extract_relevant_conflicts(data, TARGET_FACTIONS)
                    
                    for c in conflicts:
                        # Passa al DB e controlla se è un evento degno di nota
                        result = upsert_conflict(
                            self.db_conn, c['system'], c['faction_1'], c['faction_2'],
                            c['war_type'], c['status'], c['f1_days_won'], c['f2_days_won'], 
                            c['stake1'], c['stake2'], c['timestamp'], "LIVE"
                        )
                        
                        if result in ["NEW", "REACTIVATED", "SCORE_CHANGE"]:
                            await self.delete_previous_system_messages(c['system'])
                            await self.send_discord_alert(c, result)
            except zmq.error.Again:
                print("Timeout ZMQ, continuing to listen...")
            except zlib.error:
                print("Error during message decompression")
            except json.JSONDecodeError:
                print("Error during JSON reading")
            except Exception as e:
                print(f"Unexpected error in EDDN listener: {e}")

    # Daily cleanup
    @tasks.loop(hours=12)
    async def daily_cleanup(self):
        try:
            deleted_systems = cleanup_old_conflicts(self.db_conn, days=7)
            if deleted_systems:
                for system in deleted_systems:
                    await self.delete_previous_system_messages(system)
        except Exception as e:
            print(f"Unexcpected error in Daily cleanup: {e}")





# ---- FUNCTIONS
    def _format_timestamp(self, ts_string):
        """Converte il timestamp EDDN nel formato Giorno Mese Anno Ore:Minuti (Roma)"""
        try:
            # Rimuove la 'Z' finale o parti extra se presenti e parsa l'ISO format
            ts_obj = datetime.fromisoformat(ts_string.replace('Z', '+00:00'))
            # Converte al fuso orario di Roma
            roma_tz = ZoneInfo("Europe/Rome")
            ts_roma = ts_obj.astimezone(roma_tz)
            return ts_roma.strftime("%d/%m/%Y %H:%M")
        except Exception as e:
            print(f"Errore formattazione data: {e}")
            return ts_string



    def _create_conflict_embed(self, conflict, event_type=None):
        """Crea un embed coerente per ogni tipo di aggiornamento"""
        
        status_map = {
            "NEW": (":red_circle: NUOVA GUERRA RILEVATA", discord.Color.red()),
            "REACTIVATED": (":red_circle: GUERRA RIATTIVATA (Dati LIVE)", discord.Color.red()),
            "SCORE_CHANGE": (":orange_circle: AGGIORNAMENTO PUNTEGGIO", discord.Color.orange()),
            "DATABASE": (":blue_circle: STATO CONFLITTO (Database)", discord.Color.blue())
        }

        title, color = status_map.get(event_type, status_map["DATABASE"])

        embed = discord.Embed(
            title=f"{title} a {conflict['system']}", 
            color=color,
            timestamp=datetime.now()
        )

        if conflict['war_type'] in ['war', 'civilwar']:
            img_path = os.path.join(ICONS_PATH, 'war_civilwar_logo.png')
            icon_file = discord.File(img_path, filename="image.png")
            embed.set_thumbnail(url="attachment://image.png")
        elif conflict['war_type'] in ['election']:
            img_path = os.path.join(ICONS_PATH, 'election_logo.png')
            icon_file = discord.File(img_path, filename="image.png")
            embed.set_thumbnail(url="attachment://image.png")
        else:
            file = None
        
        formatted_date = self._format_timestamp(conflict['timestamp'])

        embed.add_field(
            name="Tipo e Stato", 
            value=f"{conflict['war_type'].capitalize()} ({conflict['status']})", 
            inline=False
        )
        embed.add_field(
            name=f":triangular_flag_on_post:{conflict['faction_1']}", 
            value=f"Giorni vinti: **{conflict['f1_days_won']}**\nAssetto: *{conflict.get('stake1', '----')}*",
            inline=True
        )
        embed.add_field(
            name=f":flag_black:{conflict['faction_2']}", 
            value=f"Giorni vinti: **{conflict['f2_days_won']}**\nAssetto: *{conflict.get('stake2', '----')}*",
            inline=True
        )

        embed.add_field(name="", value="", inline=False) # Spazio vuoto per formattazione
        
        embed.add_field(name="Ultimo Update EDDN", value=formatted_date, inline=True)
        embed.add_field(name="Fonte", value=conflict.get('source', 'LIVE'), inline=True)
        
        embed.set_footer(text="Elite Dangerous Data Network | NovaCorp BGS Bot")
        return embed, icon_file


# ---- ASYNC FUNCTIONS
    async def send_discord_alert(self, conflict, event_type):
        """Invia alert in tempo reale"""
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            print(f"Error in send_discord_alert: Channel {DISCORD_CHANNEL_ID} not found.")
            return
        try:
            embed, icon_file = self._create_conflict_embed(conflict, event_type)
            await channel.send(embed=embed, file=icon_file)
            print("Discord alert sent")
        except Exception as e:
            print(f"Errore durante l'invio dell'alert discord: {e}")



    async def send_conflicts_status(self):
        """Invia lo stato attuale dal database sul canale testuale"""
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            print(f"Error in send_conflicts_status: Channel {DISCORD_CHANNEL_ID} not found.")
            return

        conflicts = get_active_conflicts(self.db_conn)
        if not conflicts:
            print("[DB] Tabella conflitti vuota.")
            return
        
        try:
            for c in conflicts:
                # Mappatura chiavi DB -> Formato atteso dall'embed builder
                conflict_data = {
                    'system': c['system_name'],
                    'war_type': c['war_type'],
                    'status': c['status'],
                    'faction_1': c['faction_1'],
                    'stake1': c['stake1'],
                    'f1_days_won': c['f1_days_won'],
                    'faction_2': c['faction_2'],
                    'f2_days_won': c['f2_days_won'],
                    'stake2': c['stake2'],
                    'timestamp': c['timestamp'],
                    'source': c['source'],
                    'is_active': c['is_active']
                }
                if conflict_data['is_active']:
                    embed, icon_file = self._create_conflict_embed(conflict_data, "DATABASE")
                    await channel.send(embed=embed, file=icon_file)
            print("Conflicts status sent")
        except Exception as e:
            print(f"Errore in send_conflicts_status durante l'invio dello status dei conflitti: {e}")



    async def refresh_status_channel(self):
        """Pulisce il canale"""
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            print(f"Error in refresh_status_channel: Channel {DISCORD_CHANNEL_ID} not found.")
            return

        print("Pulizia canale...")

        try:
            await channel.purge(limit=100) # Rimuove fino a 100 messaggi recenti
            print("Completata!")
        except Exception as e:
            print(f"Errore in refresh_status_channel durante la pulizia del canale: {e}")



    async def delete_previous_system_messages(self, system_name):
        """Cerca e cancella i messaggi precedenti relativi a un sistema specifico nel canale"""
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            print(f"Error in delete_previous_system_messages: Channel {DISCORD_CHANNEL_ID} not found.")
            return

        try:
            async for message in channel.history(limit=100):
                # Assicuriamoci che il messaggio sia del bot e contenga un embed
                if message.author == self.user and message.embeds:
                    embed = message.embeds[0]
                    # I titoli sono generati come f"{title} a {conflict['system']}"
                    # Verifichiamo se il titolo termina con il nome del sistema
                    if embed.title and embed.title.endswith(f" a {system_name}"):
                        await message.delete()
        except Exception as e:
            print(f"Errore in delete_previous_messages durante la cancellazione del vecchio messaggio per {system_name}: {e}")



#---- MAIN 
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN not found. Be sure to have a local environment .env file")
    else:
        bot = FactionBot()



        #--- COMMANDS
        @bot.command(name="print_database", help="Stampa il contenuto del database dei conflitti attivi in un file .txt.")
        async def print_database(ctx):
            channel = bot.get_channel(DISCORD_CHANNEL_ID)
            if not channel:
                print(f"Error in print_database command: Channel {DISCORD_CHANNEL_ID} not found")
                return

            try:
                cursor = bot.db_conn.cursor()
                cursor.execute("""
                            SELECT
                                system_name AS Sistema,
                                faction_1 AS Fazione_1,
                                faction_2 AS Fazione_2,
                                status AS Stato,
                                war_type AS Tipo,
                                f1_days_won AS Giorni_Vinti_1,
                                f2_days_won AS Giorni_Vinti_2
                            FROM conflicts
                            WHERE is_active = 1
                            ORDER BY is_active DESC, last_updated DESC
                        """)
                rows = cursor.fetchall()

                if not rows:
                    await channel.send("[DB] 'conflicts' table empty!")
                    return

                headers = [description[0] for description in cursor.description]
                
                try:
                    from tabulate import tabulate
                    table = (tabulate(rows, headers=headers, tablefmt="grid"))
                except ImportError:
                    await channel.send("Error while executing command!")
                    print(f"In print_database command ImportError tabulate")

                title = "--- CONTENUTO DATABASE CONFLITTI ATTIVI ---\n"
                end_title = f"\n--- Totale record: {len(rows)} ---\n"
                msg_string = title + table + end_title

                file_buffer = io.BytesIO(msg_string.encode('utf-8'))
                discord_file = discord.File(fp=file_buffer, filename="database_conflitti.txt")

                await channel.send(content="Here's your database content :wink:", file=discord_file)
            except Exception as e:
                print(f"Errore nel comando print_database durante l'invio del messaggio: {e}")

        bot.run(DISCORD_TOKEN)
