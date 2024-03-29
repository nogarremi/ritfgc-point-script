import datetime
from info import sql_host,sql_port,sql_user,sql_pw,sql_db,chal_user,api_key
import pymysql.cursors
import requests
import json
import re

current_semester = '2225' # Set the current semester for fetching points and updating the database
pattern = re.compile('[\W_]+') # Regex to remove special characters

# Sets the time to only get tournaments made in the last week
current_time = datetime.datetime.now()
last_saturday = str(current_time.date()
    - datetime.timedelta(days=current_time.weekday())
    + datetime.timedelta(days=5, weeks=-1))

def get_tour():
    base_url = "https://api.challonge.com/v1/" # The basis of getting results from challonge
    key = api_key # Secret

    games = [] # Sets an empty list for games to reference later
    players = {} # Sets an empty dictionary for players to reference later

    # Opens database connection
    conn = pymysql.connect(
        host=sql_host,
        port=sql_port,
        user=sql_user,
        password=sql_pw,
        db=sql_db,
        charset='utf8mb4',
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor)

    # Parameters for getting the right tournaments
    payload = {'state':'ended', 'created_after':last_saturday, 'subdomain':'ritfgc'}

    # Send a HTTP GET request to poll the Challonge API for all the tournaments within the timeframe
    r = requests.get(base_url + "tournaments.json", params=payload, headers={"User-Agent":"RITFGC"}, auth=(chal_user, api_key))
    # Check to make sure there is a successful response
    if '200' in str(r.status_code):
        # Try because of database connection
        try:
            # Get all the challonge IDs for the games
            with conn.cursor() as cursor:
                sql = 'SELECT challonge_game_id FROM games'
                cursor.execute(sql)
                for row in cursor:
                    games.append(row['challonge_game_id'])
            # Loop through all the tournaments
            for t in r.json():
                # The structure of the json is weird and tournament data is not at the root
                # Set the data root as the variable
                t = t['tournament']

                # Get all the current players from the database to remove the possibility for duplicates
                # Now it puts the players into a dictionary to remove the possibility for human errors by removing special characters
                with conn.cursor() as cursor:
                    sql = 'SELECT player_Handle FROM players'
                    cursor.execute(sql)
                    for row in cursor:
                        players[pattern.sub('', row['player_Handle'].lower())] = row['player_Handle']
                # Filter tournament results to ensure only the proper tournaments are being selected  
                if ('rit fgc' in t['name'].lower() and str(current_semester) in t['name'].lower()) and 'extra' not in t['name'].lower():
                    # Referencing the games list to get make sure the game is in the database
                    if t['game_id'] in games:
                        challongeID = t['game_id']
                    else:
                        continue
                    # Get the week number
                    ranbat_num = t['name'][-2:].strip("#")

                    # Empty dict to store participant name and final rank
                    parts = {}

                    # Empty array of places to calculate points
                    places = []

                    # Send an HTTP GET request to poll for all the participant for a tournament
                    x = requests.get(base_url + "tournaments/" + str(t['id']) + "/participants.json", headers={"User-Agent":"RITFGC"}, auth=(chal_user, api_key))
                    # Check to make sure there is a successful response
                    if '200' in str(x.status_code):
                        # Loop through all participants
                        for p in x.json():
                            # The structure of the json is weird and participant data is not at the root
                            # Set the data root as the variable
                            p = p['participant']

                            # Fill in the empty array/dict
                            places.append(p['final_rank'])
                            parts.update({p['name']:p['final_rank']})
                    else:
                        # Print the bad result if there is a failed GET request
                        print(x)

                    # Send the array of places sorted descending to the point generator
                    points = calc_points(sorted(places,reverse=True))
                    
                    # Loop through the parts dict created earlier to add player results to database
                    for part in parts:
                        # Pull the information from the dict to simplify using the info
                        p_Handle = part
                        handle_stripped = pattern.sub('', p_Handle.lower()) # Stripped name for ease of access later
                        final_rank = parts[part]

                        # Get player points from the points dict using the player's final rank as a key
                        p_points = points[final_rank]

                        with conn.cursor() as cursor:
                            # Check to see if player already exists in the database 
                            if handle_stripped not in players:
                                #Add player to the player dictionary
                                players[handle_stripped] = p_Handle
                                # Add player handle into the database
                                sql = "INSERT INTO players (player_Handle) VALUES (%s)"
                                cursor.execute(sql, (players[handle_stripped],))

                            # Check to see if the player already has points in the database
                            sql = "SELECT ranbat_score FROM placements INNER JOIN semesters ON semesters.semester_ID = placements.semester_ID INNER JOIN games ON games.game_ID = placements.game_ID INNER JOIN players ON players.player_ID = placements.player_ID WHERE semesters.semesterNum = %s AND games.challonge_game_id = %s AND players.player_Handle = %s"
                            cursor.execute(sql, (current_semester, challongeID, players[handle_stripped],))
                            result = cursor.fetchone()

                            # Check the results for points
                            if result:
                                # Add placement for a new tournament for an existing player and update points
                                sql = "UPDATE placements SET tour_" + ranbat_num + " = %s, ranbat_score = %s WHERE placement_ID = (SELECT placement_ID FROM placements WHERE semester_ID = (SELECT semester_ID FROM semesters WHERE semesterNum = %s) AND game_ID = (SELECT game_ID FROM games WHERE challonge_game_id = %s) AND player_ID = (SELECT player_ID FROM players WHERE player_Handle = %s))"
                                cursor.execute(sql, (final_rank, result['ranbat_score']+p_points, current_semester, challongeID, players[handle_stripped]))
                            else:
                                # Add a player into the system with points
                                sql = "INSERT INTO placements (semester_ID, game_ID, player_ID, tour_" + ranbat_num + ", ranbat_score) VALUES ((SELECT semester_ID FROM semesters WHERE semesterNum = %s), (SELECT game_ID FROM games WHERE challonge_game_id = %s), (SELECT player_ID FROM players WHERE player_Handle = %s), %s, %s)"
                                cursor.execute(sql, (current_semester, challongeID, players[handle_stripped], final_rank, p_points))
        # Close the database connection
        finally:
            conn.close()
    else:
        # Print the bad result if there is a failed GET request
        print(r)
 
def calc_points(places):
    # Create a dictionary from the array since
    # dictionaries cannot contain duplicate keys
    points = dict.fromkeys(places)
    
    # Store the unique values to loop over
    places = list(points)

    # Loop to set up a dictionary based on final placements
    for x in range(len(places)):
        # Points should be equal to 2 + the farther a player made it in the tournament
        points[places[x]] = 2 + x

        # Quick check to give top 3 more points
        #    3rd = 4th + 2
        #    2nd = 3rd + 2
        #    1st = 2nd + 2
        if places[x] == 1:
            points[places[x]] += 3
        elif places[x] == 2:
            points[places[x]] += 2
        elif places[x] == 3:
            points[places[x]] += 1
    # Return the dictionary of points
    return points

def lambda_handler(event, context):
    get_tour()
    return {
        'statusCode': 200,
        'body': json.dumps('Success')
    }