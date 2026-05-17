# ED-ConflictTracker
This repository contains the source code for my personal Discord Bot used as a third-part tool for the videogame [Elite: Dangerous](https://www.elitedangerous.com). The bot is hosted privately and currently it's considered as finished project, but if you want to contribute, you can start from this repo to add more functionalities . The main functionality is to keep track of the ongoing active conflicts in the videogame for a list of specific minor factions. In order to do this, it's implemented a message listener filtering specific schemas from the [EDDN](https://github.com/EDCD/EDDN) network.

## Contents of this repo.

### /data folder
This folder contains a database file with information about conflicts from listed factions of interest. If you run separately from the */scripts* folder [import_dump.py](https://github.com/JugenWekindosh/ED-ConflictTracker/blob/main/scripts/import_dump.py), the database will be created and conflicts will be imported from data dumps sourced from [edgalaxydata](https://edgalaxydata.space/). The database is created using SQLite library because only the bot should read and write its content, so there's no problem of database locking due to concurrent services trying to access simultaneusly.

### /core folder
This folder contains main methods imported in [bot.py](https://github.com/JugenWekindosh/ED-ConflictTracker/blob/main/bot.py) to manage the database and to parse messages from EDDN.

### /scripts folder
This folder contains scripts that should be executed separately from the bot. They are used to test the correct execution of methods contained in the source codes present in */core* folder.

## How the bot works
First, we use [import_dump.py](https://github.com/JugenWekindosh/ED-ConflictTracker/blob/main/scripts/import_dump.py) script to make a fresh database and fill it with data from dumps. Then the bot keeps track of the current conflicts in the database by listening from EDDN network's messages for real-time activity tracking. When the conflict gets older of 7 days from the message timestamp, it gets deleted from the database

