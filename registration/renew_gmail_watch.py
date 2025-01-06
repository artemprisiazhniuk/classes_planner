import uuid
import json
import os
from datetime import datetime

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify"
]

PROJECT_ID = "TEMPLATE-PROJECT-ID" # GCP project ID
TOPIC_ID = "TEMPLATE-TOPIC-ID" # Pub/Sub topic ID
LABEL_ID = "TEMPLATE-LABEL-ID" # Gmail label ID, where the registration emails are stored
USER_ID = "TEMPLATE-USER-ID" # business gmail address


def main(local=False):
    '''
    Function to renew the Gmail watch as there is no automatic renewal.
    '''
    
    # sign in
    if local: # from file
        if os.path.exists("token_gmail.json"):
            creds = Credentials.from_authorized_user_file("token_gmail.json", SCOPES)
        else:
            raise FileNotFoundError("token_gmail.json not found")
    else: # from env
        creds = json.loads(os.getenv("CREDS"))
        if creds:
            creds = Credentials.from_authorized_user_info(creds, SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())

    # define services
    gmail_service = build("gmail", "v1", credentials=creds)
    
    # process (stop) current watches
    response = gmail_service.users().stop(userId=USER_ID).execute()
    
    # send renewal request
    num_tries = 0
    max_tries = 3
    
    while num_tries < max_tries:
        request_body = {
            "labelIds": [LABEL_ID],
            "topicName": f"projects/{PROJECT_ID}/topics/{TOPIC_ID}"
        }
        response = gmail_service.users().watch(userId=USER_ID, body=request_body).execute()
        print(f'Watch response (try {num_tries}):', response)
        
        # handle errors
        if 'historyId' in response:
            break
        
        num_tries += 1
        
if __name__ == "__main__":
    main(local=True)