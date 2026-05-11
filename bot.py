import discord
from discord.ext import tasks, commands
import zmq
import zmq.asyncio
import zlib
import json
import os
from dotenv import load_dotenv

from core import get_connection, setup_db, upsert_conflict, get_active_conflicts, print_all_conflicts
from core import extract_relevant_conflicts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
load_dotenv(ENV_PATH)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.getenv("DISCORD_CHANNEL_ID")

if not DISCORD_TOKEN or not CHANNEL_ID_RAW:
    raise ValueError("ERROR: Variables DISCORD_TOKEN or DISCORD_CHANNEL_ID missing in .env file")
DISCORD_CHANNEL_ID = int(CHANNEL_ID_RAW)

# ---- CONFIGURATIONS ----
TARGET_FACTIONS = ["MCC 445 Services", "Galileo Corporation"]
relayEDDN = "tcp://eddn.edcd.io:9500"
timeoutEDDN = 600000

# Bot class
class FactionBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

        #ToDo -> solve related error
        #self.add_command(self.update_dumps)

        # Initialize database at start
        self.db_conn = get_connection(os.path.join(BASE_DIR, 'data', 'conflicts.db'))
        setup_db(self.db_conn)
        self.first_run = True

    async def on_ready(self):
        print(f'Bot online as {self.user}')

        if self.first_run:
            await self.refresh_status_channel()
            self.first_run = False

    async def setup_hook(self):
        # Start listening to EDDN relay as asynchronous background task when bot boots
        self.eddn_listener.start()
        print(f'Listening conflicts for: {TARGET_FACTIONS}')

    # ToDo->solve TypeError
    # discord.ext.commands.errors.CommandInvokeError: 
    # Command raised an exception: 
    # TypeError: FactionBot.update_dumps() missing 1 required positional argument: 'ctx'
    """
    @commands.command(name="update_dumps")
    @commands.is_owner()
    async def update_dumps(self, ctx):
        await ctx.send("Dump analisys... operation may require some minutes.")
        try:
            import asyncio
            from scripts.import_dump import process_dump
            await asyncio.to_thread(process_dump) 
            await ctx.send("Dumps updated!")
        except Exception as e:
            await ctx.send(f"Error while updating dumps: {e}")
            print(f"Error while running process_dump in bot.py: {e}")
    """

    async def refresh_status_channel(self):
        """Pulisce il canale e invia lo stato attuale del DB"""
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            print(f"Error: Channel {DISCORD_CHANNEL_ID} not found.")
            return

        print("Pulizia canale e invio stato attuale...")

        try:
            await channel.purge(limit=100) # Rimuove fino a 100 messaggi recenti
        except Exception as e:
            print(f"Errore durante la pulizia: {e}")

        conflicts = get_active_conflicts(self.db_conn)

        if not conflicts:
            print("Database vuoto, nessun messaggio inviato.")
            return

        for c in conflicts:
            # Converting sqlite3.Row object as dict
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
                'last_updated': c['last_updated'],
                'timestamp': c['timestamp']
            }
            await self.send_discord_alert(conflict_data, "STARTUP_LOAD")
        
        # Stampa a console il contenuto del database
        print_all_conflicts(self.db_conn)



    # EDDN Listener
    @tasks.loop()
    async def eddn_listener(self):
        """Background task listening to ZMQ messages flux from EDDN relay"""
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
                            c['war_type'], c['status'], c['f1_days'], c['f2_days'], 
                            c['stake1'], c['stake2'], c['timestamp'], "LIVE"
                        )
                        
                        if result in ["NEW", "REACTIVATED", "SCORE_CHANGE"]:
                            await self.send_discord_alert(c, result)
            except zmq.error.Again:
                print("Timeout ZMQ, continuing to listen...")
            except zlib.error:
                print("Error during message decompression")
            except json.JSONDecodeError:
                print("Error during JSON reading")
            except Exception as e:
                print(f"Unexpected error: {e}")



    async def send_discord_alert(self, conflict, event_type):
        """Crea e invia l'Embed su Discord"""
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            print(f"Error: Channel {DISCORD_CHANNEL_ID} not found.")
            return

        titles = {
            "NEW": " NUOVA GUERRA RILEVATA",
            "REACTIVATED": "⚠️ GUERRA RIATTIVATA (Dati LIVE)",
            "SCORE_CHANGE": "⚔️AGGIORNAMENTO PUNTEGGIO",
            "STARTUP_LOAD": " STATO ATTUALE CONFLITTO"
        }
        if event_type == "STARTUP_LOAD": color = discord.Color.blue()
        elif event_type == "SCORE_CHANGE": color = discord.Color.orange()
        else: color = discord.Color.red()

        embed = discord.Embed(
            title=f"{titles.get(event_type, 'Conflitto')} a {conflict['system']}", 
            color=color
        )
        
        embed.add_field(
            name="Tipo e Stato", 
            value=f"{conflict['war_type'].capitalize()} ({conflict['status']})", 
            inline=False
        )
        embed.add_field(
            name=conflict['faction_1'], 
            value=f"Giorni vinti: {conflict['f1_days_won']}",
            inline=True
        )
        embed.add_field(
            name="Assetto in perdita:",
            value=f"{conflict['stake1']}",
            inline=False
            )
        embed.add_field(
            name=conflict['faction_2'], 
            value=f"Giorni vinti: {conflict['f2_days_won']}",
            inline=True
        )
        embed.add_field(
            name="Assetto in perdita:",
            value=f"{conflict['stake2']}",
            inline=False
        )
        embed.add_field(
            name="Timestamp Messaggio da EDDN",
            value=f"{conflict['timestamp']}"
        )
        embed.set_footer(text="Fonte: EDDN Tracker")

        await channel.send(embed=embed)# Avvio




if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN not found. Be sure to have a local environment .env file")
    else:
        bot = FactionBot()
        bot.run(DISCORD_TOKEN)
