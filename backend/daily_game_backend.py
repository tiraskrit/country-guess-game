from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
import requests
import random
import cv2
import numpy as np
import base64
from io import BytesIO
from PIL import Image
import json
import os
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
CORS(app)

class DailyCountryGame:
    def __init__(self):
        self.current_country = None
        self.blurred_flag = None
        self.last_reset_date = None
        self.country_pool = []
        self.cached_country = None
        self.cached_date = None
        
    def _get_current_date(self):
        """Get current UTC date string"""
        return datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    def _load_cache(self):
        """Load cached country data if it exists"""
        current_date = self._get_current_date()
        if self.cached_date == current_date and self.cached_country:
            return self.cached_country
        return None
    
    def _save_cache(self, country_data):
        """Save country data to cache"""
        self.cached_date = self._get_current_date()
        self.cached_country = country_data
    
    def _fetch_country_pool(self):
        try:
            response = requests.get('https://restcountries.com/v3.1/all')
            if response.status_code == 200:
                countries = response.json()
                # Filter out very small countries or territories
                self.country_pool = [
                    country for country in countries 
                    if country.get('population', 0) > 500000
                    and country.get('cca2')  # Ensure country code exists
                ]
                
                # Generate list of country names for autocomplete with error handling
                try:
                    country_names = [country['name']['common'] for country in self.country_pool]
                    # Sort the country names alphabetically
                    country_names.sort()
                    with open('country_names.json', 'w', encoding='utf-8') as f:
                        json.dump(country_names, f, ensure_ascii=False)
                except Exception as write_error:
                    print(f"Error writing country names: {write_error}")
                
                # Shuffle the pool using today's date as seed
                today = self._get_current_date()
                random.seed(today)
                random.shuffle(self.country_pool)
                return
            print("Failed to fetch countries")
            # self._load_backup_countries()
        except Exception as e:
            print(f"Error fetching country pool: {str(e)}")
            # self._load_backup_countries()

    def _process_images(self):
        """Process and blur flag image while maintaining original dimensions"""
        try:
            flag_url = self.current_country.get('flag_url')
            
            if not flag_url:
                raise ValueError("No flag URL available")
            
            response = requests.get(flag_url, stream=True, verify=True, timeout=10)
            
            if response.status_code != 200:
                raise ValueError(f"Failed to fetch image. Status code: {response.status_code}")
                
            # Read image content and create PIL Image
            img = Image.open(BytesIO(response.content))
            
            # Convert to RGB while preserving original size
            img = img.convert('RGB')
            
            # Convert to numpy array while maintaining dimensions
            img_array = np.array(img)
            
            # Create blurred version while maintaining size
            blurred = cv2.GaussianBlur(img_array, (99, 99), 0)
            
            def img_to_base64(img_array):
                # Create PIL Image while preserving dimensions
                img = Image.fromarray(img_array.astype('uint8'))
                buffered = BytesIO()
                # Save with original size
                img.save(buffered, format="PNG", optimize=True)
                return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"
            
            # Store both versions
            self.current_country['blurred_image'] = img_to_base64(blurred)
            self.current_country['unblurred_image'] = img_to_base64(img_array)
            
        except Exception as e:
            self._use_placeholder_image()


    # def _load_backup_countries(self):
    #     """Load backup country data in case API fails"""
    #     self.country_pool = [
    #         {
    #             'name': {'common': 'United States'},
    #             'flags': {'png': 'https://flagcdn.com/w320/us.png'},
    #             'capital': ['Washington, D.C.'],
    #             'region': 'Americas',
    #             'population': 331002651
    #         }
    #     ]
    
    def get_daily_country(self):
        """Get or generate country for current day"""
        current_date = self._get_current_date()
        
        # Check if we need to reset
        if self.last_reset_date != current_date:
            # Try to load from cache first
            cached_country = self._load_cache()
            if cached_country:
                self.current_country = cached_country
                self._process_images()  # Ensure images are processed for cached data
            else:
                # Fetch new country data
                if not self.country_pool:
                    self._fetch_country_pool()
                
                # if not self.country_pool:
                #     self._load_backup_countries()
                
                if self.country_pool:
                    country = self.country_pool[0]
                    self.current_country = {
                        'name': country['name']['common'],
                        'flag_url': country['flags']['png'],
                        'capital': country['capital'][0] if country.get('capital') else 'N/A',
                        'continent': country.get('region', 'Unknown'),
                        'population': country.get('population', 0)
                    }
                    self._process_images()
                    self._save_cache(self.current_country)
                else:
                    print("Error: No country available in pool or backup.")
                    return None
                    
            self.last_reset_date = current_date
        
        return self.current_country

    def _use_placeholder_image(self):
        """Use placeholder if image processing fails"""
        self.current_country['blurred_image'] = "placeholder_base64"
        self.current_country['unblurred_image'] = "placeholder_base64"

    def get_next_reset_time(self):
        """Get time until next reset"""
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int((tomorrow - now).total_seconds())
    
    def daily_check(self):
        """Check if the cache needs to be reset and load new data if required."""
        current_date = self._get_current_date()
        if self.last_reset_date != current_date:
            self.current_country = None  # Invalidate current country data
            new_country = self._load_cache()  # Attempt to load or refresh cache
            if not new_country:
                # Force a refresh of the country pool
                self.country_pool = []
                self._fetch_country_pool()
                self.get_daily_country()  # This will generate and cache new country

game = DailyCountryGame()

@app.route('/api/game-state', methods=['GET'])
def get_game_state():
    country = game.get_daily_country()
    next_reset = game.get_next_reset_time()
    
    return jsonify({
        'blurred_image': country['blurred_image'],
        'game_id': hash(country['name']),
        'next_reset': next_reset,
        'current_date': game.last_reset_date,
    })

@app.route('/api/player-names', methods=['GET'])
def get_country_names():
    try:
        with open('country_names.json', 'r') as f:
            country_names = json.load(f)
        return jsonify(country_names)
    except FileNotFoundError:
        # Create the file with an empty list if it doesn't exist
        with open('country_names.json', 'w') as f:
            json.dump([], f)
        return jsonify([])

@app.route('/api/guess', methods=['POST'])
def check_guess():
    data = request.get_json()
    guess = data.get('guess', '').lower()
    current_hint_level = data.get('hint_level', 0)
    
    if not game.current_country:
        return jsonify({'error': 'No active game'}), 400
    
    correct = guess == game.current_country['name'].lower()
    
    response = {
        'correct': correct,
        'hint_level': current_hint_level,
        'next_reset': game.get_next_reset_time(),
        'hint_text': None,
        'hint_image': None,
        'player_name': None
    }
    
    # Show original flag and country name for game over scenarios
    if correct or current_hint_level >= 4:
        response['hint_image'] = None
        response['image_url'] = game.current_country['flag_url']
        response['player_name'] = game.current_country['name']
        
        resp = make_response(jsonify(response))
        return resp
    
    # Hint progression
    if not correct and current_hint_level < 4:
        if current_hint_level == 0:
            response['hint_text'] = "Unblurred Flag"
            response['hint_image'] = game.current_country['unblurred_image']
        elif current_hint_level == 1:
            response['hint_text'] = f"Population: {game.current_country['population']:,}"
            response['hint_image'] = game.current_country['unblurred_image']
        elif current_hint_level == 2:
            response['hint_text'] = f"Continent: {game.current_country['continent']}"
            response['hint_image'] = game.current_country['unblurred_image']
        elif current_hint_level == 3:
            response['hint_text'] = f"Capital: {game.current_country['capital']}"
            response['hint_image'] = game.current_country['flag_url']
    
    return jsonify(response)

if __name__ == '__main__':
    scheduler = BackgroundScheduler()
    scheduler.add_job(game.daily_check, 'interval', days=1, 
                     start_date='2024-11-09 00:00:00',
                     timezone=timezone.utc)
    scheduler.start()
    app.run()