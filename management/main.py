import json
import os
import re
import base64
import uuid
import sys
import logging
from datetime import datetime, timedelta
from collections import defaultdict

from flask import Flask, request, jsonify
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.cloud import storage
from googleapiclient.errors import HttpError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import pandas as pd

from templates import *

# Flask app setup
app = Flask(__name__)

logging.basicConfig(stream=sys.stdout, level=logging.INFO)


SERVICE_ACCOUNT_FILE = "token.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly", 
    "https://www.googleapis.com/auth/calendar.events.owned", 
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/devstorage.read_write"
]

PROJECT_ID = "TEMPLATE-PROJECT-ID"
BUCKETNAME = "TEMPLATE-BUCKETNAME"
CONTACTS_SPREADSHEET_ID = "TEMPLATE-SPREADSHEET-ID"
SPREADSHEET_RANGE = "A:C" # name, email, whatsapp
ADMIN_CALENDAR_ID = "TEMPLATE-CALENDAR-ID"
CALENDAR_ID_MAPPING = {}
SENDER_EMAIL = "TEMPLATE-EMAIL"

PER_TAG = True

# Initialize Google services
credentials = Credentials.from_authorized_user_file(SERVICE_ACCOUNT_FILE, SCOPES)
calendar_service = build("calendar", "v3", credentials=credentials)
sheets_service = build("sheets", "v4", credentials=credentials)
spreadsheets = sheets_service.spreadsheets()
gmail_service = build("gmail", 'v1', credentials=credentials)
storage_client = storage.Client(project=PROJECT_ID, credentials=credentials)
bucket = storage_client.get_bucket(BUCKETNAME)


#region helper functions
def update_calendar_mapping():
    global CALENDAR_ID_MAPPING
    
    # get calendar ids from calendar bucket
    blob = bucket.get_blob('calendar_mapping.json')
    if blob:
        calendar_mapping = json.loads(blob.download_as_string())
    else:   
        calendar_mapping = dict()
        
    new_in_bucket = set(calendar_mapping.keys()) - set(CALENDAR_ID_MAPPING.keys())
    
    for key in new_in_bucket:
        CALENDAR_ID_MAPPING[key] = calendar_mapping[key]
    
    # update bucket
    if blob:
        blob.upload_from_string(json.dumps(CALENDAR_ID_MAPPING), content_type='application/json')
    else:
        blob = bucket.blob('calendar_mapping.json')
        blob.upload_from_string(json.dumps(CALENDAR_ID_MAPPING), content_type='application/json')

#endregion

#region email functions
def create_email_message(sender, to, subject, body):
    """Create a message for an email."""
    message = MIMEMultipart()
    message['to'] = to
    message['from'] = sender
    message['subject'] = subject
    msg = MIMEText(body)
    message.attach(msg)
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {'raw': raw_message}

def send_email(sender, to, subject, body):
    """Send an email message."""
    try:
        if isinstance(to, list):
            to = ', '.join(to)
        message = create_email_message(sender, to, subject, body)
        message = gmail_service.users().messages().send(userId="me", body=message).execute()
        logging.info(f'Sent message to {to} Message Id: {message["id"]}')
    except HttpError as error:
        logging.info(f'An error occurred: {error}')
#endregion

#region calendar functions
def fetch_all_events(calendar_id, days=60):
    events_result = calendar_service.events().list(
        calendarId=calendar_id,
        timeMin=datetime.now().astimezone().isoformat(),
        timeMax=(datetime.now() + timedelta(days=days)).astimezone().isoformat(),
        singleEvents=True,
        orderBy='updated'
    ).execute()

    events = events_result.get('items', [])

    if events: 
        events.sort(key=lambda x: x['created'], reverse=True)

    return events

def fetch_events_history(tag=None):
    events_history = None
    
    if tag and tag != 'Admin':
        filename = f'events_history_{tag}.json'
    else:
        filename = 'events_history.json'
    
    blob = bucket.get_blob(filename)
    if blob:
        events_history = json.loads(blob.download_as_string())

    return events_history

def fetch_old_events(calendar_id, days=60):
    events_result = calendar_service.events().list(
        calendarId=calendar_id,
        timeMin=(datetime.now() - timedelta(days=days)).astimezone().isoformat(),
        timeMax=(datetime.now() - timedelta(days=7)).astimezone().isoformat(),
        singleEvents=True,
        orderBy='updated'
    ).execute()

    events = events_result.get('items', [])

    return events
#endregion

#region notification functions
def notify(events, note_type="schedule", notify_tag=None): # schedule, update, delete
    events_per_tag = defaultdict(list)
    for event in events:
        title = event['summary']
        tag = re.search(r'\[(.*)\]', title)
        tag = tag.group(1) if tag else None
        
        events_per_tag[str(tag)].append(event)
    
    for tag, events in events_per_tag.items():
        if notify_tag and tag != str(notify_tag): continue # notify only specific tag
        
        # get contacts by tag
        if tag != str(None):
            result = spreadsheets.values().get(spreadsheetId=CONTACTS_SPREADSHEET_ID, range=f"{tag}!{SPREADSHEET_RANGE}").execute()
        else:
            result = spreadsheets.values().get(spreadsheetId=CONTACTS_SPREADSHEET_ID, range=f"{SPREADSHEET_RANGE}").execute()
        
        values = result.get('values', [])
        logging.info("Values: %s", values)
        
        # sort events by date
        events.sort(key=lambda x: x['start'].get('dateTime', x['start'].get('date')))
        
        events_by_day = defaultdict(list)
        for event in events:
            start_date = event['start'].get('dateTime', event['start'].get('date'))
            end_date = event['end'].get('dateTime', event['end'].get('date'))
            
            event['startTime'] = datetime.fromisoformat(start_date).strftime('%H:%M')
            event['endTime'] = datetime.fromisoformat(end_date).strftime('%H:%M')
            
            start_date = datetime.fromisoformat(start_date).strftime('%d.%m.%Y')
            
            events_by_day[start_date].append(event)
        
        # create text schedule
        schedule_ = []
        for day, day_events in events_by_day.items():
            schedule_.append(
                f"{day}\n" + '\n'.join([
                    f"{event['summary']}: {event['startTime']} - {event['endTime']}" 
                    for event in day_events
                ])
            )
        
        schedule = '\n\n'.join(schedule_)
        if note_type == "schedule":
            text = get_schedule_template().format(tag=tag, period="zwei nächste Wochen", schedule=schedule)
        elif note_type == "update":
            text = get_update_template().format(tag=tag, schedule=schedule)
        elif note_type == "delete":
            text = get_update_template().format(tag=tag, schedule=schedule)
        
        # get preferred contact method
        df = pd.DataFrame(values[1:], columns=values[0])

        emails = df.loc[df['Preference'] == 'email', "E-mail"].tolist()
        whatsapps = df.loc[df['Preference'] == 'whatsapp', "Whatsapp"].tolist()
        
        send_email(sender=SENDER_EMAIL, to=emails, subject=f"[{tag}] Salsa Kurs", body=text)
        # plans to add whatsapp notifications were postponed


def process_events(events_dict, events_history):
    created = set(events_dict.keys()) - set(events_history.keys())
    possibly_updated = set(events_dict.keys()) & set(events_history.keys())
    deleted = set(events_history.keys()) - set(events_dict.keys())
    
    # on create - pass, save in history
    # on update: notify only if datetime changed
    to_notify_updated = []
    for id_ in possibly_updated:
        new_event = events_dict[id_]
        old_event = events_history[id_]
        
        if 'date' in new_event['start']:
            new_event_start = datetime.fromisoformat(new_event['start']['date'])
        elif 'dateTime' in new_event['start']:
            new_event_start = datetime.fromisoformat(new_event['start']['dateTime'])
            
        if 'date' in old_event['start']:
            old_event_start = datetime.fromisoformat(old_event['start']['date'])
        elif 'dateTime' in old_event['start']:
            old_event_start = datetime.fromisoformat(old_event['start']['dateTime'])
        
        if new_event_start != old_event_start:
            to_notify_updated.append(new_event)
    # on delete: notify if event is in the future
    to_notify_deleted = []
    for id_ in deleted:
        event = events_history[id_]
        
        if 'date' in event['end']:
            event_end = datetime.fromisoformat(event['end']['date']).date()
            if event_end >= datetime.now().astimezone().date():
                to_notify_deleted.append(event)
        elif 'dateTime' in event['end']:
            event_end = datetime.fromisoformat(event['end']['dateTime'])
            if event_end > datetime.now().astimezone():
                to_notify_deleted.append(event)
                
    return created, to_notify_updated, to_notify_deleted


def update_history(events_dict, tag=None):
    if events_dict:
        if tag == "Admin":
            calendar_id = ADMIN_CALENDAR_ID
        else:
            calendar_id = CALENDAR_ID_MAPPING.get(tag, None)
        if calendar_id:
            # delete old events from admin history
            old_events_list = fetch_old_events(
                calendar_id
            )
            old_events_ids = set([event['id'] for event in old_events_list])
            
            events_dict = {k: v for k, v in events_dict.items() if k not in old_events_ids}
    else:
        events_dict = dict()
        
    if tag and tag != 'Admin':
        filename = f'events_history_{tag}.json'
    else:
        filename = 'events_history.json'

    blob = bucket.blob(filename)
    blob.upload_from_string(json.dumps(events_dict), content_type='application/json')


def update_calendar(events_dict, tag=None):
    if not events_dict:
        events_dict = dict()
        
    calendar_id = CALENDAR_ID_MAPPING.get(tag, None)
    if calendar_id:
        for event in events_dict.values():
            calendar_service.events().insert(calendarId=calendar_id, body=event).execute()


def compare_and_notify(events_dict, tag=None, log=False):
    events_history = fetch_events_history(tag)
    to_notify_updated = []
    to_notify_deleted = []
    created = []
    
    if not tag:
        tag = 'Admin'

    # find differences
    if not events_dict:
        events_dict = dict()
    if not events_history:
        events_history = dict()
    
    created, to_notify_updated, to_notify_deleted = process_events(events_dict, events_history)
    
    # log
    if log:
        logging.info("Created: %s", list(created))
        logging.info("To notify updated: %s", [x['id'] for x in to_notify_updated])
        logging.info("Deleted: %s", [x['id'] for x in to_notify_deleted])
    
    # notify schedule
    send_created = False
    # urgent notifications
    send_updated = True
    send_deleted = True
    # for demo purposes
    send_history = True

    if send_history:
        update_history(events_dict, tag=tag)
        if tag and tag != 'Admin':
            update_calendar(events_dict, tag=tag)
            
        if send_created and created: # temporary TODO delete
            notify(list(events_dict.values()), note_type="schedule")
        if send_updated and to_notify_updated:
            notify(to_notify_updated, note_type="update")
        if send_deleted and to_notify_deleted:
            notify(to_notify_deleted, note_type="delete")
#endregion


@app.route('/notifications', methods=['POST'])
def notifications():
    '''Receive and processes updates from Google Calendar'''
    notification = request.headers
    # Handle case where Content-Type might be None
    
    logging.info('Notification received: %s', notification)
    
    # Extract necessary headers
    resource_state = request.headers.get('X-Goog-Resource-State')
    resource_id = request.headers.get('X-Goog-Resource-Id')
    calendar_uri = request.headers.get('X-Goog-Resource-Uri')

    if resource_state == 'exists': # Calendar exists
        # get events list from admin calendar
        events_list = fetch_all_events(ADMIN_CALENDAR_ID, days=60)
        
        # general
        if not PER_TAG:
            events_dict = dict()
            for event in events_list:
                events_dict[event['id']] = event
                
            compare_and_notify(events_dict, tag='Admin', log=True) # general
        else:
            events_dicts = defaultdict(dict)
            tags = set()
            for event in events_list:
                tag = re.search(r'\[(.*)\]', event['summary']).group(1)
                
                tags.add(tag)
                events_dicts[tag][event['id']] = event
                
            for tag in tags:         
                # list sheet names for CONTACS_SPREADSHEET_ID
                sheet_names = spreadsheets.get(spreadsheetId=CONTACTS_SPREADSHEET_ID).execute()['sheets']
                if tag in [sheet['properties']['title'] for sheet in sheet_names]: # check if tag exists in sheets
                    if tag not in CALENDAR_ID_MAPPING: # create calendar if not exists
                        calendar = {
                            'summary': tag,
                            'timeZone': 'Europe/Vienna'
                        }
                        created_calendar = calendar_service.calendars().insert(body=calendar).execute()
                        CALENDAR_ID_MAPPING[tag] = created_calendar['id']
                        
                        update_calendar_mapping()
                 
                compare_and_notify(events_dicts[tag] , tag=tag, log=False)
    
    return 'OK', 200


@app.route('/schedule', methods=['POST'])
def notify_schedule():
    events_list = fetch_all_events(ADMIN_CALENDAR_ID, days=60)
        
    request_json = request.get_json()
    
    tag = request_json.get('tag', None)
    
    start_date = request_json.get('start_date', None)
    end_date = request_json.get('end_date', None)
    if start_date and end_date:
        start_date = datetime.fromisoformat(start_date).date()
        end_date = datetime.fromisoformat(end_date).date()
        
    # general
    if True:
        events_dict = dict()
        for event in events_list:
            if start_date and end_date:    
                if 'date' in event['start']:
                    event_start = datetime.fromisoformat(event['start']['date']).date()
                elif 'dateTime' in event['start']:
                    event_start = datetime.fromisoformat(event['start']['dateTime']).date()
                    
                if event_start >= start_date and event_start <= end_date:
                    events_dict[event['id']] = event
            
                events_dict[event['id']] = event
            else:
                events_dict[event['id']] = event
            
        notify(list(events_dict.values()), note_type="schedule", notify_tag=tag)
        
    return 'OK', 200


@app.teardown_appcontext # on exit
def close_services(exception):
    if calendar_service:
        calendar_service.close()
    if sheets_service:
        sheets_service.close()
    if gmail_service:
        gmail_service.close()

if __name__ == '__main__':
    # Run the Flask app
    app.run(host='0.0.0.0', port=8080, debug=True)
