import datetime
from info import sql_host,sql_port,sql_user,sql_pw,sql_db,api_key
import pymysql.cursors
import requests
import json

current_semester = '2191' # Set the current semester for fetching points and updating the database

# Sets the time to only get tournaments made in the last week
current_time = datetime.datetime.now()
last_saturday = str(current_time.date()
    - datetime.timedelta(days=current_time.weekday())
    + datetime.timedelta(days=5, weeks=-1))

def get_tour():
    base_url = "https://api.challonge.com/v1/" # The basis of getting results from challonge
    key = api_key # Secret
	
    games = {132596:"sfv", 39782:"t7", 55777:"bbcf", 139550:"bbtag", 125058:"unist", 121822:'xrd', 129078:'dbfz'} # For translation of tournament game_ids from challonge to game names for the database
    players = [] # Sets an empty array for players to reference later
    
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
    payload = {'api_key':key, 'state':'ended', 'created_after':last_saturday, 'subdomain':'ritfgc'}
	
    # Send a HTTP GET request to poll the Challonge API for all the tournaments within the timeframe
    r = requests.get(base_url + "tournaments.json", params=payload)
    # Check to make sure there is a successful response
    if '200' in str(r.status_code):
        # Try because of database connection
        try:
            # Loop through all the tournaments
            for t in r.json():
                # The structure of the json is weird and tournament data is not at the root
                # Set the data root as the variable
                t = t['tournament']
				
                # Get all the current players from the database to remove the possibility for duplicates
                with conn.cursor() as cursor:
                    sql = 'SELECT player_Handle FROM players'
                    cursor.execute(sql)
                    for row in cursor:
                        players.append(row['player_Handle'])
                # Filter tournament results to ensure only the proper tournaments are being selected  
                if 'rit fgc '+current_semester in t['name'].lower() and 'extra' not in t['name'].lower():
                    # Referencing the games dict to get the game name from the tournament game_id
                    if t['game_id'] in games.keys():
                        g_Name = games.get(t['game_id'])
                    else:
                        break
                    
                    # Empty dict to store participant name and final rank
                    parts = {}
					
                    # Empty array of places to calculate points
                    places = []
					
                    # Send an HTTP GET request to poll for all the participant for a tournament
                    x = requests.get(base_url + "tournaments/" + str(t['id']) + "/participants.json", params={'api_key':key})
                    # Check to make sure there is a successful response
                    if '200' in str(x.status_code):
                        # Loop through all participants
                        for p in x.json():
                            # The structure of the json is weird and tournament data is not at the root
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
                        final_rank = parts[part]
						
                        # Get player points from the points dict using the player's final rank as a key
                        p_points = points[final_rank]
						
                        with conn.cursor() as cursor:
                            # Check to see if the player already has points in the database
                            sql = "SELECT ranbat_score FROM results INNER JOIN semesters ON semesters.semester_ID = results.semester_ID INNER JOIN games ON games.game_ID = results.game_ID INNER JOIN players ON players.player_ID = results.player_ID WHERE semesters.semesterNum = %s AND games.game_Name = %s AND players.player_Handle = %s"
                            cursor.execute(sql, (current_semester, g_Name, p_Handle,))
                            result = cursor.fetchone()

                            # Check the results for points
                            if not result:
                                # Check to see if player already exists in the database 
                                if p_Handle not in players:
                                    # Add player handle into the database
                                    sql = "INSERT INTO players (player_Handle) VALUES (%s)"
                                    cursor.execute(sql, (p_Handle,))
                                # Add points into the database for player and game
                                sql = "INSERT INTO results (semester_ID, player_ID, game_ID, ranbat_score) VALUES ((SELECT semester_ID FROM semesters WHERE semesterNum = %s), (SELECT player_ID FROM players WHERE player_Handle = %s), (SELECT game_ID FROM games WHERE game_Name = %s), %s)"
                                cursor.execute(sql, (current_semester, p_Handle, g_Name, p_points))
                            else:
                                # Add new points to existing point total for player
                                sql = "UPDATE results SET ranbat_score = %s WHERE semester_ID = (SELECT semester_ID FROM semesters WHERE semesterNum = %s) AND player_ID = (SELECT player_ID FROM players WHERE player_Handle = %s) AND game_ID = (SELECT game_ID FROM games WHERE game_Name = %s)"
                                cursor.execute(sql, (result['ranbat_score']+p_points, current_semester, p_Handle, g_Name))
        # Close the database connection
        finally:
            conn.close()
    else:
        # Print the bad result if there is a failed GET request
        print(r)
 
def calc_points(places):
    # Create a dictionary from the array
    points = dict.fromkeys(places)
	
    # Dictionaries cannot contain duplicate keys
    # Store the unique values to have a way to loop
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