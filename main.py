# -*- coding: utf-8 -*-
import os, sys
os.environ.setdefault("PYTHONUNBUFFERED", "1")
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

import csv
import requests
import time
import telebot
from telebot import types
import threading
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
import socket
import re

BASE_DIR = os.environ.get("BOT_DATA_DIR", "./data")
os.makedirs(BASE_DIR, exist_ok=True)

# ... (rest of your bot logic and all improvements from previous messages)