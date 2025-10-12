from datetime import date, timedelta # Import utilities to work get "Last Saturday"
import re # Import regex utilities to ensure that we aren't injecting random symbols into the database
from json import dumps as j_dumps

import pymysql.cursors # Import database utilities
from requests import (
    get as req_get, # Import HTTP get function from requests library
    HTTPError,
)

# Run-time secrets
# TODO: Move to AWS SecretsManager
from info import (
    SQL_HOST,
    SQL_PORT,
    SQL_USER,
    SQL_PW,
    SQL_DB,
    CHALLONGE_USER,
    CHALLONGE_API_KEY
 )

CURRENT_SEMESTER = "2251" # Set the current semester for fetching points and updating the database
PATTERN = re.compile("[\W_]+") # Regex to remove special characters

# Sets time to last Saturday to limit tournaments to only the last week
LAST_SATURDAY = str(date.today() # Get today's date
    - timedelta(days=date.today().weekday()) # Subtract date.weekday to get to the beginning of the week
    + timedelta(days=5, weeks=-1) # Add -2 days to get to Friday
) # Outputs YYYY-MM-DD

"""
Create and maintain Database operations
"""
class DBConn(pymysql.connections.Connection):
    """
    Create database connection
    """
    def __init__(self, host, port, user, password, database):
        super().__init__(
            host=host,      # Host to connect to
            port=port,      # Port to connect to on the host
            user=user,      # SQL user to use to connect
            password=password,    # Password for the SQL user
            db=database,          # Database on the host to use
            charset='utf8mb4',  # Database charset; don't change
            autocommit=True,    # Commit as soon as executes run
            cursorclass=pymysql.cursors.DictCursor # pymysql Cursor class that makes SELECTs return dictionary formats
        )

    """
    Get a dictionary of game IDs mapped to Challonge game IDs
    Return Dict()
    """
    def get_game_ids(self):
        try:
            with self.cursor() as cursor:
                sql = 'SELECT game_ID, challonge_game_id FROM games'
                cursor.execute(sql)
                return {row["game_ID"]: row["challonge_game_id"] for row in cursor}
        except Exception as e:
            raise Exception(e) from e

    """
    Get a list of all players
    Return List(Player)
    """
    def get_players(self):
        try:
            with self.cursor() as cursor:
                sql = 'SELECT player_ID, player_Handle FROM players'
                cursor.execute(sql)
                return [Player(row["player_Handle"], player_id=row["player_ID"]) for row in cursor]
        except Exception as e:
            raise Exception(e) from e

    """
    Add player to the `players` table
    Return Player
    """
    def insert_player(self, player_handle):
        try:
            with self.cursor() as cursor:
                sql = "INSERT INTO players (player_Handle) VALUES (%s)"
                cursor.execute(sql, (player_handle,))
                return Player(player_handle, player_id=cursor.lastrowid)
        except Exception as e:
            raise Exception(e) from e

    """
    Add or update ranbat score and placements in the `placements` table
    """
    def upsert_ranbat_score(self, semester, game_id, player_id, ranbat_num, final_rank, points):
        try:
            with self.cursor() as cursor:
                sql = f"""
                INSERT INTO placements (
                    semester_ID,
                    game_ID,
                    player_ID,
                    tour_{ranbat_num},
                    ranbat_score
                )
                VALUES
                (
                    (SELECT semester_ID FROM semesters WHERE semesterNum = %s),
                    (SELECT game_ID FROM games WHERE challonge_game_id = %s),
                    %s, 
                    %s,
                    %s
                )
                ON DUPLICATE KEY UPDATE
                    tour_{ranbat_num} = VALUES(tour_{ranbat_num}),
                    ranbat_score = ranbat_score + VALUES(ranbat_score)
                """
                cursor.execute(sql, (semester, game_id, player_id, final_rank, points))
        except Exception as e:
            raise Exception(e) from e

"""
Create requests to Challonge's API and manage data objects from Challonge's API
"""
class Challonge:
    """
    Instantiate tournaments and populate their participant attribute
    """
    def __init__(self, game_ids, after=LAST_SATURDAY):
        self._base_url = "https://api.challonge.com/v1/"
        self._semester = CURRENT_SEMESTER
        self._auth = (CHALLONGE_USER, CHALLONGE_API_KEY)
        self._all_tournaments = self._get_all_tournaments(after)
        self.tournaments = [tournament for tournament in self._all_tournaments if tournament.validate(self._semester, game_ids)]

        for tournament in self.tournaments:
            tournament.participants = self._get_tournament_participants(tournament.get_id())
            tournament.create_point_calculation(tournament.get_places())

    """
    Get all completed Challonge tournaments for the RITFGC org based on created_after filter if provided
    Return List(Tournament)
    """
    def _get_all_tournaments(self, after):
        payload = {'state':'ended', 'subdomain':'ritfgc'}
        if after and after is not None:
            payload["created_after"] = after

        tournaments_get = req_get(f"{self._base_url}/tournaments.json", params=payload, headers={"User-Agent":"RITFGC"}, auth=self._auth)
        try:
            tournaments_get.raise_for_status()
            tournaments = []
            for tournament in tournaments_get.json():
                tournaments.append(Tournament(tournament["tournament"]["id"], tournament["tournament"]["name"], tournament["tournament"]["game_id"]))
            return tournaments
        except Exception as e:
            raise HTTPError(e) from e

    """
    Get all participants for given tournament_id
    Return List(Participant)
    """
    def _get_tournament_participants(self, tournament_id):
        participants_get = req_get(f"{self._base_url}/tournaments/{tournament_id}/participants.json", headers={"User-Agent":"RITFGC"}, auth=self._auth)
        try:
            participants_get.raise_for_status()
            participants = []
            for participant in participants_get.json():
                participants.append(Participant(participant["participant"]["name"], participant["participant"]["final_rank"]))
            return participants
        except HTTPError as e:
            raise HTTPError(e) from e


"""
Create and manager attributes related to individual tournaments
"""
class Tournament:
    """
    Create tournament data
    """
    def __init__(self, tournament_id, name, game_id):
        self._id = tournament_id
        self.name = name.lower()
        self._game_id = game_id
        self.ranbat_number = name[-2:].strip("#")
        self.participants = []
        self.placement_points = None

    """
    Define Tournament str formatting
    Return str
    """
    def __str__(self): return str(self.__dict__)

    """
    Define Tournament str formatting when printing from a List(Tournament)
    Return str
    """
    def __repr__(self): return str(self)

    """
    Tournament ID Getter
    Return int
    """
    def get_id(self): return self._id

    """
    Define reverse sorted list of unique placements
    Return List(int)
    """
    def get_places(self): return sorted({participant.place for participant in self.participants},reverse=True)

    """
    Ensure a tournament is a valid ranbats tournament
    Return bool
    """
    def validate(self, semester, game_ids):
        return (    'rit fgc' in self.name and
                    semester in self.name and
                    'extra' not in self.name and
                    'monthly' not in self.name and
                    self._game_id in game_ids
        )

    """
    Determine how many points to distribute based on placements
    return Dict()
    """
    def create_point_calculation(self, places):
        points = dict.fromkeys(places)

        # Loop to set up a dictionary based on final placements
        for i, place in enumerate(places):
            # Points should be equal to 2 + the farther a player made it in the tournament
            points[place] = 2 + i

            # Quick check to give top 3 more points
            #    3rd = 4th + 2
            #    2nd = 3rd + 2
            #    1st = 2nd + 2
            if place == 1:
                points[place] += 3
            elif place == 2:
                points[place] += 2
            elif place == 3:
                points[place] += 1

        self.placement_points = points

"""
Wrapper for Player objects for search
"""
class Players:
    """
    Instantiate list of players
    """
    def __init__(self, players):
        self._players = players

    """
    Define Players str formatting
    Return str
    """
    def __str__(self): return str(self.__dict__)

    """
    Define Players str formatting when printing from Players
    Return str
    """
    def __repr__(self): return str(self)

    """
    Find user using their lowered and stripped name
    Return Player
    """
    def find_player_by_safe_handle(self, safe_handle):
        for player in self._players:
            if player.safe_handle == safe_handle:
                return player

    """
    Add player to the Players list
    """
    def add_player(self, player): self._players.append(player)

"""
Create and manager Player attributes
"""
class Player:
    """
    Define Player attributes
    """
    def __init__(self, handle, player_id=None):
        self._id = player_id
        self.handle = handle
        self.lowered_handle = handle.lower()
        self.safe_handle = PATTERN.sub("",self.lowered_handle)

    """
    Define Player str formatting
    Return str
    """
    def __str__(self): return str(self.__dict__)

    """
    Define Player str formatting when printing from Players or a List(Player)
    Return str
    """
    def __repr__(self): return str(self)

    """
    Player ID Getter
    Return int
    """
    def get_id(self): return self._id

    """
    Player ID Setter
    """
    def set_id(self, player_id): self._id = player_id

"""
Create and mangae a Participant, an extension of a Player (tied to a Tournament)
"""
class Participant(Player):
    """
    Create a Player iwth a place and points
    """
    def __init__(self, handle, place):
        super().__init__(handle)
        self.place = place
        self.points = 0

    """
    Participants points Setter
    """
    def set_points(self, points): self.points = points

"""
Main logic section
"""
def main():
    conn = None
    try:
        # Opens database connection
        conn = DBConn(
            host=SQL_HOST,      # Host to connect to
            port=SQL_PORT,      # Port to connect to on the host
            user=SQL_USER,      # SQL user to use to connect
            password=SQL_PW,    # Password for the SQL user
            database=SQL_DB     # Database on the host to use
        )
        game_ids = conn.get_game_ids() # Get all the valid game_ids
        players = Players(conn.get_players()) # Get list of all players that are played before

        # Start getting tournament data
        # By default, uses LAST_SATURDAY
        challonge = Challonge(game_ids.values())

        # Loop over each tournament to check their participants
        for tournament in challonge.tournaments:
            # Loop over each tournament participant to calculate and update their ranbat score
            for participant in tournament.participants:
                # Find the participant's Player to get the id
                player = players.find_player_by_safe_handle(participant.safe_handle)
                if player is None:
                    # If participant not found, add them to the database and to the Players list
                    player = conn.insert_player(participant.handle)
                    players.add_player(player)
                # Set participant ID to the player ID
                participant.set_id(player.get_id())
                # offer player up for garabage collection since it contains duplicate info
                del player

                # Set participant's ranbats points for the week
                participant.set_points(tournament.placement_points[participant.place])

                # Insert or update participant's ranbat score for given tournament
                conn.upsert_ranbat_score(CURRENT_SEMESTER, tournament._game_id, participant.get_id(), tournament.ranbat_number, participant.place, participant.points)
    except Exception as e:
        # Ensure we are catching the errors
        # TODO: Generate Discord Alert
        raise Exception(e) from e
    finally:
        if conn:
            # Ensure database connection closes to prevent hanging connections
            conn.close()

# How Lambda calls this stuff
def lambda_handler(event, context):
    main()
    return {
        'statusCode': 200,
        'body': j_dumps('Success')
    }
