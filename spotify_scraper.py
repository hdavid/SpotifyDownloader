import asyncio
import json
import re
import requests
import signal
import sys
import traceback
from pathlib import Path
from time import sleep
import os
import platform
from unidecode import unidecode
from dataclasses import dataclass
import random

from PyQt6.QtCore import pyqtSignal, QThread

import eyed3
from eyed3.id3 import ID3_V2_3
from eyed3.id3.frames import ImageFrame

# Suppress warnings about CRC fail for cover art
import logging
logging.getLogger('eyed3.mp3.headers').warning = logging.debug

from token_grabber import main as get_token


class SpotifySong:
    # meta data
    id: str
    title: str = None
    artist: str = None
    album: str = None
    cover: str = None
    releaseDate: str = None
    link: str = None
    #
    downloaded: bool = False
    skipped: bool = False
    failed: bool = False
    error: str = None
    
    def __init__(self, data = None):
        self.parse(data)
    
    def parse(self, data):
        if 'id' in data:
            self.id=data['id']
        if 'title' in data:
            self.title=data['title']
        if 'artists' in data:
            if isinstance(data["artists"], (list, tuple)):
                self.artist=', '.join(data["artists"])
            else:
                self.artist=data["artists"]
        if 'album' in data:
            self.album=data['album']
        if 'cover' in data:
            self.cover = data["cover"]
        if 'releaseDate' in data:
            self.releaseDate = data["releaseDate"]
        
    @property
    def url(self):
         return f"https://open.spotify.com/track/{id}"
    
    @property
    def filename(self):
        return self.clean_filename(f"{self.artist} - {self.album} - {self.title}.mp3")
        
    @property
    def name(self): 
        return f"{self.artist} - {self.album} - {self.title}"
        
    def clean_filename(self, fn):
        validchars = "-_.() '',"
        out = ""
        for c in fn:
          if str.isalpha(c) or str.isdigit(c) or (c in validchars):
            out += c
          else:
            out += "-"
        return unidecode(out)


class SpotifyScraperThread(QThread):
    
    counts = pyqtSignal(int, int, int, int)
    token_updated = pyqtSignal(str)
    progress_updated = pyqtSignal(str)
    
    def __init__(self, link, token, output_path, debug=False):
        super().__init__()
        self.link = link
        self.tracks = []
        self.token = token
        self.output_path = output_path
        self.debug = debug
    
    def is_album(self, url):
        return "/album/" in url 
          
    def is_playlist(self, url):
        return "/playlist/" in url
    
    def is_track(self, url):
        return "/track/" in url
    
        
    def track_count(self):
        return len(self.tracks)
        
    def downloaded_track_count(self):
        return len([track for track in self.tracks if track.downloaded])
    
    def skipped_track_count(self):
        return len([track for track in self.tracks if track.skipped])
        
    def failed_track_count(self):
        return len([track for track in self.tracks if track.failed])
    
    def failed_tracks(self):
        return [track for track in self.tracks if track.failed]
    
    def random_track_id(self):
        return random.choice(self.tracks).id
        
        
# Token    
    async def _fetch_token(self):
        self.progress_updated.emit("\tGetting new token")
        try:
            self.token = await get_token()
            if self.token:
                self.token_updated.emit(self.token)
                self.progress_updated.emit("\tToken fetched successfully!")
            else:
                self.progress_updated.emit("\tFailed to fetch token")
        except Exception as e:
            self.progress_updated.emit(f"\tFailed to fetch token: {str(e)}")

    def get_token(self):
        if not self.token_is_valid():
            asyncio.run(self._fetch_token())
        
    def token_is_valid(self):
        if len(self.token)<10:
            return False
        else:
            try:
                resp = self._call_downloader_api(f"/download/{self.random_track_id()}?token={self.token}")
                resp_json = resp.json()
                return resp_json['success']
            except Exception as e:
                self.progress_updated.emit("error while checking token" + str(e))
                self.progress_updated.emit(traceback.format_exc())
                return False
          
     
          
    def run(self):
        try:
            #reset progress bar
            self.counts.emit(100, 0, 0, 0)
            
            if self.is_playlist(self.link) or self.is_album(self.link):
                entity_id = self.link.split('/')[-1].split('?')[0]
                entity_metadata_resp = self._call_downloader_api(f"/metadata/playlist/{entity_id}")
                entity_metadata = entity_metadata_resp.json()
                if self.is_playlist(self.link):
                    entity_type = "playlist"
                    entity_name = entity_metadata['title'] + " (" + entity_metadata['artists'] + ")"
                else:
                    entity_type = "album"
                    entity_name = entity_metadata['artists']+ " - " + entity_metadata['title']
                    
                self.progress_updated.emit(entity_type + ": " + entity_name + "\n")
            
                if not entity_metadata["success"]:
                    self.progress_updated.emit("not a valid "+ entity_name + ", Spotify api return error message: " + playlist_metadata["message"])
                    return                

                entity_name = entity_name.replace("/", "-")
                self.output_path = Path(self.output_path + "/" + entity_name)
            
            elif self.is_track(self.link):
                self.output_path = Path(self.output_path)
                self.progress_updated.emit("Single track")
                entity_type = "track"
                entity_id = self.link.split('/')[-1].split('?')[0]
            else:
                self.progress_updated.emit("Error: Invalid url")
                return
        
        
            if not os.path.exists(self.output_path):
                os.makedirs(self.output_path)
       
            self.get_tracks_to_download(entity_type, entity_id)
            
            self.download_all_tracks()
            if entity_type == "track":
                self.track_scrape_report()
            else:
                self.playlist_scrape_report()
        except Exception as e:
            self.progress_updated.emit("error in scrape" + str(e))
            self.progress_updated.emit(traceback.format_exc())
            
             
        
    def playlist_scrape_report(self):
        details = ""   
                 
        if self.failed_track_count()>0:
            details += "\nFailed track downloads:"
            for track in self.failed_tracks():
                details += "\n" + track.name +   ((": "+ track.error) if track.error!=None else "")
            details += "\n"
        
        directory_files = os.listdir(self.output_path)
        in_folder_not_in_playlist = []
        playlist_filenames = []
        for track in self.tracks:
            playlist_filenames.append(track.filename)
            
        for filename in directory_files:
            if filename not in playlist_filenames and filename != ".DS_Store" and not filename.startswith(".syncthing.") and not filename.endswith(".stem.m4a"):
                in_folder_not_in_playlist.append(filename)
        if len(in_folder_not_in_playlist):
            details += "\nTracks in folder but not in playlist:"
            for track in in_folder_not_in_playlist:
                details += "\n" + track
            details += "\n"
                
        in_playlist_not_in_folder = []
        for track in self.tracks:
            if track.filename not in directory_files:
                 in_playlist_not_in_folder.append(track.name)
        if len(in_playlist_not_in_folder):
            details += "\nTracks in playlist but not in folder:"
            for track in in_playlist_not_in_folder:
                details += "\n" + track
            details += "\n"
        
        if len(in_playlist_not_in_folder)==0 and len(in_folder_not_in_playlist)==0 and self.failed_track_count==0:
            details += "All downloads completed sucessfully!"
            
        self.progress_updated.emit(details)
        
    
    def track_scrape_report(self):
        details = ""
        if self.failed_track_count()>0:
            details += "\nFailed track download:"
            for track in self.failed_tracks():
                details += "\n" + track
            details += "\n"
        
        if self.failed_track_count==0:
            details += "Download completed sucessfully!"
            
        self.progress_updated.emit(details)
    

    def _call_downloader_api(self, endpoint: str, **kwargs) -> requests.Response:
        DOWNLOADER_URL = "https://api.spotifydown.com"
        # Clean browser heads for API
        DOWNLOADER_HEADERS = {
            'Host': 'api.spotifydown.com',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip',
            'Referer': 'https://spotifydown.com/',
            'Origin': 'https://spotifydown.com',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'Sec-GPC': '1',
            'TE': 'trailers'
        }
        try:
            resp = requests.get(DOWNLOADER_URL + endpoint, headers=DOWNLOADER_HEADERS,**kwargs)
        except Exception as exc:
            raise RuntimeError("ERROR: ", exc)
        return resp
        


    def get_tracks_to_download(self, entity_type: str, entity_id: str) -> list:
        if entity_type == "track":
            track_resp = self._call_downloader_api( f"/metadata/track/{entity_id}").json()
            self.tracks.append(SpotifySong(track_resp))

        elif entity_type in ["playlist", "album"]:
            tracks_resp = self._call_downloader_api(f"/trackList/{entity_type}/{entity_id}").json()
            if tracks_resp.get('trackList'):
                self.tracks = []
                for track_resp in tracks_resp['trackList']:
                    self.tracks.append(SpotifySong(track_resp))
       
                while next_offset := tracks_resp.get('nextOffset'):
                    tracks_resp = self._call_downloader_api(f"/trackList/{entity_type}/{entity_id}?offset={next_offset}").json()
                    for data in tracks_resp['trackList']:
                        self.tracks.append(SpotifySong(track_resp))

    def get_track_link(self, track):
        self.progress_updated.emit(f"\tget track link")
        resp = self._call_downloader_api(f"/download/{track.id}?token={self.token}")    
        resp_json = resp.json() 
        if not resp_json['success']:
            self.progress_updated.emit("Could not get track link for "+track.name)
            self.progress_updated.emit(resp_json)
            track.failed = True
            if resp_json["statusCode"]==403:
                self.token_error.emit()
                track.error = resp_json["message"]
            else:
                track.error = resp_json["message"]
        else:
            track.link = resp_json["link"]
    
        

    def download_track(self, track:SpotifySong):
        self.progress_updated.emit(f"\tdownload audio")
        # Clean browser heads for API
        hdrs = {
            #'Host': 'cdn[#].tik.live', # <-- set this below
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip',
            'Referer': 'https://spotifydown.com/',
            'Origin': 'https://spotifydown.com',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-GPC': '1'
        }

        if track.link is None:
            raise RuntimeError(f"no link for '{track.name}")

        # For audio
        hdrs['Host'] = track.link.split('/')[2]
        audio_dl_resp = requests.get(track.link, headers=hdrs)
        
        if not audio_dl_resp.ok:
            error = f"Bad download response for track '{track.title}' ({track.id}): {audio_dl_resp.status_code}: {audio_dl_resp.content}"
            raise RuntimeError(error)
        
        self.progress_updated.emit(f"\tsave file")
        with open(self.output_path/track.filename, 'wb') as track_mp3_fp:
            track_mp3_fp.write(audio_dl_resp.content)
            
        if not os.path.exists(self.output_path/track.filename) or os.path.getsize(self.output_path/track.filename) == 0:
              raise Exception("downloaded file is zero byte.")  
                

        self.progress_updated.emit(f"\tadding tags")
        #tags
        mp3_file = eyed3.load(self.output_path/track.filename)
        if (mp3_file.tag == None):
            mp3_file.initTag()
        mp3_file.tag.album = track.album
        mp3_file.tag.artist = track.artist
        mp3_file.tag.title = track.title
        mp3_file.tag.recording_date = track.releaseDate
          
        # cover art
        if cover_art_url := track.cover:
            hdrs['Host'] = cover_art_url.split('/')[2]
            cover_resp = requests.get(cover_art_url,headers=hdrs)
            mp3_file.tag.images.set(ImageFrame.FRONT_COVER, cover_resp.content, 'image/jpeg')
        #save tags
        mp3_file.tag.save(version=ID3_V2_3) 


    def download_all_tracks(self):
       
        for track in self.tracks:
            
            full_filename = self.output_path / track.filename
            try:
                if os.path.exists(full_filename) and os.path.getsize(full_filename) != 0:
                    track.skipped=True
                    self.progress_updated.emit(f"file exists, skipping: {track.name}")
                else:
                    self.progress_updated.emit(f"{track.name}")
                    retries = 0
                    max_retries = 3
                    while not track.downloaded:
                        try:
                            self.get_token()
                            self.get_track_link(track)
                            self.download_track(track)
                            track.downloaded=True
                            track.failed=False
                            self.progress_updated.emit('\tdone')
                        except Exception as exc:
                            self.progress_updated.emit("\terror while processing track: "+ str(exc))
                            if self.debug:
                                self.progress_updated.emit(traceback.format_exc())
                            retries += 1
                            self.progress_updated.emit('\tretrying... attempt {retries} of {max_retries}')
                            sleep(retries*0.5)
                            if errors>max_retries:
                                raise exc
            except Exception as exc:
                self.progress_updated.emit("\terror while processing track: "+ str(exc))
                track.failed=True
                if self.debug:
                    self.progress_updated.emit(traceback.format_exc())
                
                
            self.counts.emit(self.track_count(), self.downloaded_track_count(), self.skipped_track_count(), self.failed_track_count())



      




      



