import discord
from discord.ext import tasks, commands
import zmq
import zmq.asyncio
import zlib
import json
import os
from dotenv import load_dotenv

from core import get_connection, setup_db, upsert_conflict, get_active_conflicts, print_all_conflicts, cleanup_old_conflicts
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
TARGET_FACTIONS = ["MCC 445 Services", "Galileo Corporation", "Expanders Corp"]
relayEDDN = "tcp://eddn.edcd.io:9500"
timeoutEDDN = 600000

# Bot class
class FactionBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        # Initialize database at start
        self.db_conn = get_connection(os.path.join(BASE_DIR, 'data', 'conflicts.db'))
        setup_db(self.db_conn)
        self.first_run = True

    async def on_ready(self):
        print(f'Bot online as {self.user}')
        if self.first_run:
            cleanup_old_conflicts(self.db_conn, days=7)
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
                            c.append({'source:' "LIVE"})
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
    @tasks.loop(hours=24)
    async def daily_cleanup(self):
        cleanup_old_conflicts(self.db_conn, days=7)





    async def refresh_status_channel(self):
        """Pulisce il canale e stampa il DB sulla console"""
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            print(f"Error: Channel {DISCORD_CHANNEL_ID} not found.")
            return

        print("Pulizia canale...")

        try:
            await channel.purge(limit=100) # Rimuove fino a 100 messaggi recenti
            print("Pulizia canale completata!")
        except Exception as e:
            print(f"Errore durante la pulizia del canale: {e}")

        print_all_conflicts(self.db_conn)

    async def send_discord_alert(self, conflict, event_type):
        """Notify conflict update from EDDN listener sending embedded message"""
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            print(f"Error in send_discord_alert: Channel {DISCORD_CHANNEL_ID} not found.")
            return

        titles = {
            "NEW": "NUOVA GUERRA RILEVATA",
            "REACTIVATED": "GUERRA RIATTIVATA (Dati LIVE)",
            "SCORE_CHANGE": "AGGIORNAMENTO PUNTEGGIO"
        }
        if event_type == "SCORE_CHANGE": color = discord.Color.orange()
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
            inline=False
        )
        embed.add_field(
            name="Assetto in perdita:",
            value=f"{conflict['stake1']}",
            inline=False
            )
        embed.add_field(
            name=conflict['faction_2'], 
            value=f"Giorni vinti: {conflict['f2_days_won']}",
            inline=False
        )
        embed.add_field(
            name="Assetto in perdita:",
            value=f"{conflict['stake2']}",
            inline=False
        )
        embed.add_field(
            name="Timestamp Messaggio da EDDN",
            value=f"{conflict['timestamp']}",
            inline=False
        )
        embed.add_field(
            name="Fonte Messaggio",
            value=f"{conflict['source']}",
            inline=False
        )
        await channel.send(embed=embed)
        print("Discord alert sent")

    async def send_conflicts_status(self):
        channel = self.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            print(f"Error in send_conflicts_status: Channel {DISCORD_CHANNEL_ID} not found.")
            return

        conflicts = get_active_conflicts(self.db_conn)

        if not conflicts:
            print("[DB] empty 'conflicts' table.")
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
                'timestamp': c['timestamp'],
                'source': c['source'],
                'is_active': c['is_active']
            }
            if conflict_data['is_active']:
                embed = discord.Embed(
                    title=f"CONFLITTO IN DATABASE ATTIVO", 
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="Tipo e Stato", 
                    value=f"{conflict_data['war_type'].capitalize()} ({conflict_data['status']})", 
                    inline=False
                )
                embed.add_field(
                    name=conflict_data['faction_1'], 
                    value=f"Giorni vinti: {conflict_data['f1_days_won']}",
                    inline=False
                )
                embed.add_field(
                    name="Assetto in perdita:",
                    value=f"{conflict_data['stake1']}",
                    inline=False
                    )
                embed.add_field(
                    name=conflict_data['faction_2'], 
                    value=f"Giorni vinti: {conflict_data['f2_days_won']}",
                    inline=False
                )
                embed.add_field(
                    name="Assetto in perdita:",
                    value=f"{conflict_data['stake2']}",
                    inline=False
                )
                embed.add_field(
                    name="Timestamp Messaggio da EDDN",
                    value=f"{conflict_data['timestamp']}",
                    inline=False
                )
                embed.add_field(
                    name="Fonte Messaggio",
                    value=f"{conflict_data['source']}",
                    inline=False
                )
                await channel.send(embed=embed)
        print("Conflicts status sent")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN not found. Be sure to have a local environment .env file")
    else:
        bot = FactionBot()
        bot.run(DISCORD_TOKEN)
