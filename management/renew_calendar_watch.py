import uuid
import json
import os

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.cloud import firestore


SCOPES = [
    "https://www.googleapis.com/auth/calendar.events.owned",
    "https://www.googleapis.com/auth/datastore"
]

ENDPOINT_URL = "TEMPLATE-ENDPOINT-URL"
SUFFIX = "TEMPLATE-SUFFIX"
WEBHOOK_URL = f"{ENDPOINT_URL}/{SUFFIX}"

PROJECT_ID = "TEMPLATE-PROJECT-ID"
DATABASE_ID = "TEMPLATE-DATABASE-ID"
COLLECTION_ID = "TEMPLATE-COLLECTION-ID"

CALENDAR_ID = "TEMPLATE-CALENDAR-ID"


def main(local=False):
    '''
    Function to renew the calendar watch.
    '''
    
    # sign in
    if local:
        if os.path.exists("token_cal.json"):
            creds = Credentials.from_authorized_user_file("token_cal.json", SCOPES)
        else:
            raise FileNotFoundError("token.json not found")
    else:
        creds = json.loads(os.getenv("CREDS"))
        if creds:
            creds = Credentials.from_authorized_user_info(creds, SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())

    # define services
    calendar_service = build("calendar", "v3", credentials=creds)
    db = firestore.Client(PROJECT_ID, creds, DATABASE_ID)
    
    # process current watches
    watches_ref = db.collection(COLLECTION_ID)
    watches = watches_ref.stream()
    
    # if watches exist, stop all
    for watch in watches:
        watch_data = watch.to_dict()

        channel_id = watch_data.get('id')  # Channel ID of the watch
        resource_id = watch_data.get('resourceId')  # Resource ID from watch creation response

        # Create stop request body
        try:
            stop_body = {
                'id': channel_id,         # Channel ID
                'resourceId': resource_id  # Resource ID from the watch
            }
            response = calendar_service.channels().stop(body=stop_body).execute()
        except HttpError:
            pass

        # Delete the watch record from Firestore
        watch.reference.delete()
        
    num_tries = 0
    max_tries = 3
    
    while num_tries < max_tries:
        # send renewal request
        watcher_id = str(uuid.uuid4())
        request_body = {
            'id': watcher_id,
            'type': 'webhook',
            'address': WEBHOOK_URL,
            'params': {
                'ttl': "604800"  # 7 days in seconds, maximum value
            }
        }
        response = calendar_service.events().watch(calendarId=CALENDAR_ID, body=request_body).execute()
        print(f'Watch response (try {num_tries}):', response)
        
        # handle errors
        if 'id' in response and 'resourceId' in response:
            break
        
        num_tries += 1
    
    # add new watch to db
    watches_ref.add(response)
    
    
if __name__ == "__main__":
    main(local=True)