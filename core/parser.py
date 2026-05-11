def extract_relevant_conflicts(message_data, target_factions):
    relevant_conflicts = []
    
    if 'message' not in message_data:
        return relevant_conflicts

    msg = message_data['message']
    system_name = msg.get('StarSystem')
    conflicts = msg.get('Conflicts', [])
    time_stamp = msg.get('timestamp')

    if not system_name or not conflicts or not time_stamp:
        return relevant_conflicts

    for c in conflicts:
        f1_name = c['Faction1']['Name']
        f2_name = c['Faction2']['Name']
        
        if f1_name in target_factions or f2_name in target_factions:
            relevant_conflicts.append({
                'system': system_name,
                'faction_1': f1_name,
                'faction_2': f2_name,
                'war_type': c.get('WarType', 'unknown'),
                'status': c.get('Status', 'unknown'),
                'f1_days_won': c['Faction1'].get('WonDays', 0),
                'f2_days_won': c['Faction2'].get('WonDays', 0),
                'stake1': c['Faction1'].get('Stake', ''),
                'stake2': c['Faction2'].get('Stake', ''),
                'timestamp': time_stamp
            })
            
    return relevant_conflicts

