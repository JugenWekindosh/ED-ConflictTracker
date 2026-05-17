# ED-ConflictTracker
This repository contains the source code for my personal Discord Bot, designed as a **third-party** tool for the videogame [Elite: Dangerous](https://www.elitedangerous.com). While the bot is privately hosted and currently considered a **completed** project, contributions to add new functionalities are welcome. 

The main purpose of the bot is to track active conflicts in-game for a specific list of minor factions. To achieve this, it implements a message listener that filters specific schemas from the [EDDN](https://github.com/EDCD/EDDN) network.

## Repository Content

### /data folder
This folder contains the SQLite database storing conflict information for the factions of interest. By running [import_dump.py](link) from the `/scripts` folder, the database will be initialized and populated with data dumps sourced from [edgalaxydata](https://edgalaxydata.space/). SQLite was chosen for its simplicity since the bot is the sole service reading and writing to the file, avoiding potential database locking issues from concurrent access.

### /core folder
This folder contains the core methods imported by `bot.py` to manage database operations and parse EDDN messages.

### /scripts folder
This folder contains standalone scripts used to test the methods defined in the `/core` directory independently from the main bot execution.

## How it Works
1. **Initialization**: The `import_dump.py` script is used to create a fresh database and populate it with historical data from dumps.
2. **Real-time Tracking**: Once active, the bot monitors current conflicts by listening to EDDN network messages for real-time updates.
3. **Cleanup**: To keep the database relevant, any conflict older than 7 days (based on the message timestamp) is automatically deleted.
