#!/usr/bin/python3

import sys
import os
import string
import re
import traceback
import json

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QLabel, QFileDialog, QListWidget, QMessageBox, QTextEdit, QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QSize
from PyQt6.QtGui import QIcon, QTextCursor


from spotify_scraper import SpotifyScraperThread


class SpotifyDownGUI(QWidget):
    
    def __init__(self):
        super().__init__()
        self.settings = QSettings('SpotifyDownloader', 'SpotifyDownloader')
        
        self.spotify_url_input = None
        self.last_spotify_url = None
        
        self.output_path_input = None
        self.last_output_path = None
        
        self.log_output = None
        
        self.token = ''
        self.scraper_thread = None
        
        self.load_config()
    
        self.initUI()
      
      
    def load_config(self):
        self.token = self.settings.value('token', '')
        self.last_output_path = self.settings.value('output_path', os.path.expanduser("~/Music"))
        self.last_spotify_url = self.settings.value('spotify_url', "")
         
    def save_config(self):
        self.settings.setValue('token', self.token)
        self.settings.setValue('output_path', self.output_path_input.text().strip())
        self.settings.setValue('spotify_url', self.spotify_url_input.text().strip())
        self.settings.sync()


    def initUI(self):
        self.setWindowTitle('SpotifyDown')
        self.setMinimumSize(650, 600)
            
        self.main_layout = QVBoxLayout()     
        self.setup_spotify_section()
        self.setup_output_section()
        self.setup_progress_section()
        self.setLayout(self.main_layout)    
        
    def setup_spotify_section(self):
        spotify_layout = QHBoxLayout()
        spotify_label = QLabel('Spotify URL:')
        spotify_label.setFixedWidth(110)
        spotify_layout.addWidget(spotify_label)
        
        self.spotify_url_input = QLineEdit()
        self.spotify_url_input.setText(self.last_spotify_url)
        self.spotify_url_input.setPlaceholderText("https://open.spotify.com/playlist/{id}")
        self.spotify_url_input.setClearButtonEnabled(True)
        self.spotify_url_input.returnPressed.connect(self.scrape)
        spotify_layout.addWidget(self.spotify_url_input)
        
        self.main_layout.addLayout(spotify_layout)
    
    def setup_output_section(self):
        output_layout = QHBoxLayout()
        output_label = QLabel('Output Directory:')
        output_label.setFixedWidth(110)
        output_layout.addWidget(output_label)
        
        self.output_path_input = QLineEdit()
        self.output_path_input.setText(self.last_output_path)
        self.output_path_input.textChanged.connect(self.save_config)
        output_layout.addWidget(self.output_path_input)
        
        output_browse = QPushButton('Browse')
        output_browse.clicked.connect(self.browse_output)
        output_layout.addWidget(output_browse)
        
        self.main_layout.addLayout(output_layout)
             
    def setup_progress_section(self):
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.main_layout.addWidget(self.log_output)
        
        self.progress_bar = QProgressBar()
        self.main_layout.addWidget(self.progress_bar)
              
    def browse_output(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory", directory=os.path.expanduser("~"))
        if directory:
            self.output_path_input.setText(directory)
    
    
    def clear(self):
        self.progress_percent_updated(100, 0, 0, 0)
        self.log_output.clear()
    
    def scrape(self):
        if self.scraper_thread is not None:
            return
        try:
            self.clear()
            self.save_config()
            self.scraper_thread = SpotifyScraperThread(self.spotify_url_input.text(), self.token, self.output_path_input.text())    
            self.scraper_thread.finished.connect(self.thread_finished)
            self.scraper_thread.token_updated.connect(self.token_updated)
            self.scraper_thread.progress_updated.connect(self.progress_updated)
            self.scraper_thread.counts.connect(self.progress_percent_updated)
            self.scraper_thread.start()
        except ValueError as e:
            update_progress(str(e))
            update_progress(traceback.format_exc())
    
    
    def thread_finished(self):
        self.scraper_thread.deleteLater()
        self.scraper_thread = None
        self.progress_updated("Scraping Completed.")

    def progress_updated(self, message):
        self.log_output.append(message)
        self.log_output.moveCursor(QTextCursor.MoveOperation.End)
            
    def progress_percent_updated(self, track_count, downloaded, skipped, failed):
        self.progress_bar.setValue(int(100.0 * (downloaded + skipped + failed) / track_count))
           
    def token_updated(self, token):
        self.token = token
        self.save_config()
          

# Main
if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = SpotifyDownGUI()
    ex.show()
    sys.exit(app.exec())

