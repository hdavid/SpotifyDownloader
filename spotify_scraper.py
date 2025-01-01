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
    track_number: int = None
    # state
    downloaded: bool = False
    skipped: bool = False
    failed: bool = False
    error: str = None
    in_album: bool = False
    
    def __init__(self, data = None):
        self.parse(data)
    
    def parse(self, data, album_cover=None, track_number=None):
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
        if album_cover is not None:
            self.cover = album_cover
        else:
            if 'cover' in data:
                self.cover = data["cover"]
        if 'releaseDate' in data:
            self.releaseDate = data["releaseDate"]
        if track_number is not None:
            self.track_number = track_number
        else:
            if 'trackNumber' in data:
                self.track_number = data["trackNumber"]
        
    @property
    def url(self):
         return f"https://open.spotify.com/track/{id}"
    
    @property
    def filename(self):
        return self.clean_filename(f"{self.name}.mp3")
        
    @property
    def name(self): 
        if self.in_album:
            if self.track_number is not None:
                if self.track_number<10:
                    return f"0{self.track_number} - {self.title}"
                else:
                    return f"{self.track_number} - {self.title}"
            else:
                return self.title
        else:
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
    
    
    def __init__(self, link, token, output_path):
        super().__init__()
        self.link = link
        self.tracks = []
        self.token = token
        self.output_path = output_path
        # enable debug is debug is present in url
        self.debug = "debug" in link
    
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

    def get_token_if_needed(self):
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
                self.progress_updated.emit(str(traceback.format_exc()))
                return False

          
    def run(self):
        try:
            album_cover = None
            # get item metadata
            if self.is_playlist(self.link) or self.is_album(self.link):
                entity_id = self.link.split('/')[-1].split('?')[0]

                if self.is_playlist(self.link):
                    entity_metadata_resp = self._call_downloader_api(f"/metadata/playlist/{entity_id}")
                    entity_metadata = entity_metadata_resp.json()
                    entity_type = "playlist"
                    if isinstance(entity_metadata["artists"], (list, tuple)):
                        artist=', '.join(entity_metadata["artists"])
                    else:
                        artist=entity_metadata["artists"]
                    entity_name = entity_metadata['title'] + " (" + artist + ")"
                else:
                    entity_metadata_resp = self._call_downloader_api(f"/metadata/album/{entity_id}")
                    entity_metadata = entity_metadata_resp.json()
                    entity_type = "album"
                    if isinstance(entity_metadata["artists"], (list, tuple)):
                        artist=', '.join(entity_metadata["artists"])
                    else:
                        artist=entity_metadata["artists"]
                    entity_name = artist + " - " + entity_metadata['title']
                    if 'cover' in entity_metadata:
                        album_cover = entity_metadata['cover']
                    
                self.progress_updated.emit(entity_type + ": " + entity_name)
                if not entity_metadata["success"]:
                    self.progress_updated.emit("not a valid "+ entity_name + ", Spotify api return error message: " + playlist_metadata["message"])
                    return                

                entity_name = entity_name.replace("/", "-")
                self.output_path = Path(self.output_path + "/" + entity_name)
                
                self.progress_updated.emit("Getting tracks metadata")
                self.get_tracks_to_download(entity_type, entity_id, album_cover=album_cover)
                self.progress_updated.emit(f"\nDownloading {len(self.tracks)} tracks:")
                
            elif self.is_track(self.link):
                self.output_path = Path(self.output_path)
                self.progress_updated.emit("Single track")
                entity_type = "track"
                entity_id = self.link.split('/')[-1].split('?')[0]
                track_resp = self._call_downloader_api( f"/metadata/track/{entity_id}").json()
                self.tracks = [SpotifySong(track_resp)]
                
            else:
                self.progress_updated.emit("Error: Invalid url")
                return
        
            if not os.path.exists(self.output_path):
                os.makedirs(self.output_path)
            
            self.download_all_tracks(entity_type)
            if entity_type == "track":
                self.track_scrape_report()
            else:
                self.playlist_scrape_report()
                
        except Exception as e:
            self.progress_updated.emit("Error while downloading" + str(e))
            if self.debug:
                self.progress_updated.emit(str(traceback.format_exc()))
    
    
    def get_tracks_to_download(self, entity_type: str, entity_id: str, album_cover=None) -> list:
        if entity_type in ["playlist", "album"]:
            tracks_resp = self._call_downloader_api(f"/trackList/{entity_type}/{entity_id}").json()
            track_number = 1
            if tracks_resp.get('trackList'):
                self.tracks = []
                for track_resp in tracks_resp['trackList']:
                    self.add_track(track_resp, album_cover, track_number, entity_type)
                    track_number += 1

                while next_offset := tracks_resp.get('nextOffset'):
                    tracks_resp = self._call_downloader_api(f"/trackList/{entity_type}/{entity_id}?offset={next_offset}").json()
                    for data in tracks_resp['trackList']:
                        self.add_track(track_resp, album_cover, track_number, entity_type)
                        track_number += 1
                            
    def add_track(self, track_resp, album_cover, track_number, entity_type):
        track = SpotifySong(track_resp)
        if entity_type=="playlist":
            track.track_number = track_number
        if album_cover is not None:
            track.cover = album_cover
        if entity_type == "album":
            track.in_album = True
        self.tracks.append(track)
        
    
    def download_all_tracks(self, entity_type:str):
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
                            self.get_token_if_needed()
                            self.get_track_link(track)
                            self.download_track(track, entity_type)
                            self.progress_updated.emit('\tdone')
                        except Exception as exc:
                            self.progress_updated.emit(f"\terror while processing track: {str(exc)}")
                            if self.debug:
                                self.progress_updated.emit(str(traceback.format_exc()))
                            retries += 1
                            self.progress_updated.emit(f'\tretrying... attempt {retries} of {max_retries}')
                            sleep(retries)
                            if retries>max_retries:
                                raise exc
            except Exception as exc:
                self.progress_updated.emit(f"\terror while processing track: {str(exc)}")
                track.failed=True
                if self.debug:
                    self.progress_updated.emit(str(traceback.format_exc()))
                 
            self.counts.emit(self.track_count(), self.downloaded_track_count(), self.skipped_track_count(), self.failed_track_count())
    
    
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
    
        
    def download_track(self, track:SpotifySong, entity_type:str):
        self.progress_updated.emit(f"\tdownload audio")
        if track.link is None:
            raise RuntimeError(f"no download link for '{track.name}")
        
        filename = self.output_path/f"{track.filename}"

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
        hdrs['Host'] = track.link.split('/')[2]
        audio_dl_resp = requests.get(track.link, headers=hdrs)
        if not audio_dl_resp.ok:
            error = f"Bad download response for track '{track.title}' ({track.id}): {audio_dl_resp.status_code}: {audio_dl_resp.content}"
            raise RuntimeError(error)
        
        self.progress_updated.emit(f"\tsaving file")
        with open(filename, 'wb') as track_mp3_fp:
            track_mp3_fp.write(audio_dl_resp.content)

        if not os.path.exists(filename):
              raise Exception("download failed")          
        if os.path.getsize(filename) == 0:
              raise Exception("downloaded failed. File is zero byte.")
              os.remove(filename) 
        
        # tags
        self.progress_updated.emit(f"\tadding tags")
        mp3_file = eyed3.load(filename)
        if (mp3_file.tag == None):
            mp3_file.initTag()
        mp3_file.tag.album = track.album
        mp3_file.tag.artist = track.artist
        mp3_file.tag.title = track.title
        mp3_file.tag.recording_date = track.releaseDate
        mp3_file.tag.track_num = track.track_number
          
        # cover art
        if cover_art_url := track.cover:
            hdrs['Host'] = cover_art_url.split('/')[2]
            cover_resp = requests.get(cover_art_url,headers=hdrs)
            mp3_file.tag.images.set(ImageFrame.FRONT_COVER, cover_resp.content, 'image/jpeg')
        # save tags
        mp3_file.tag.save(version=ID3_V2_3)
        
        # update track state
        track.downloaded=True
        track.failed=False
        
    
    def playlist_scrape_report(self):
        details = ""   
        
        # failed tracks    
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
        
        # extraneous tracks
        for filename in directory_files:
            if filename not in playlist_filenames and filename != ".DS_Store" and not filename.startswith(".syncthing.") and not filename.endswith(".stem.m4a"):
                in_folder_not_in_playlist.append(filename)
        if len(in_folder_not_in_playlist):
            details += "\nTracks in folder but not in playlist:"
            for track in in_folder_not_in_playlist:
                details += "\n" + track
            details += "\n"
        
        # missing tracks
        in_playlist_not_in_folder = []
        for track in self.tracks:
            if track.filename not in directory_files:
                 in_playlist_not_in_folder.append(track.name)
        if len(in_playlist_not_in_folder)>0:
            details += "\nTracks in playlist but not in folder:"
            for track in in_playlist_not_in_folder:
                details += "\n" + track
            details += "\n"
        
        if len(in_playlist_not_in_folder)==0 and len(in_folder_not_in_playlist)==0 and self.failed_track_count()==0:
            details += "All downloads completed sucessfully!"
        
        self.progress_updated.emit("")
        self.progress_updated.emit(details)
        
    
    def track_scrape_report(self):
        details = ""
        if self.failed_track_count()>0:
            details += "\nFailed track download:"
            for track in self.failed_tracks():
                details += "\n" + track
            details += "\n"
        else:
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



      




      



