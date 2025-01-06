import re
import os
import json
import base64
import functions_framework
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google.cloud import storage
from googleapiclient.errors import HttpError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets", 
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/devstorage.read_write"
]

PROJECT_ID = "TEMPLATE-PROJECT-ID" # project ID
CONTACTS_SPREADSHEET_ID = "TEMPLATE-SHEET-ID" # contacts spreadsheet ID
SPREADSHEET_RANGE = "A:C" # fields to be stored: name, email, whatsapp
SENDER_EMAIL = "TEMPLATE-EMAIL" # email to send responses
BUCKETNAME = "TEMPLATE-BUCKETNAME" # bucket to store courses info
CALENDAR_BUCKETNAME = "TEMPLATE-BUCKETNAME" # bucket to store calendar current history (for calendar updates management)


#region utils
def get_deny_registration_template(company_name="Template Company"):
    return """Liebe(r) {name},\n\nLeider können wir Ihre Anmeldung nicht akzeptieren. Es gibt zu viel BesucherInnen. Sie können für andere Kurse anmelden. \n\nMit freundlichen Grüßen,\n""" + company_name

def get_accept_registration_template(company_name="Template Company"):
    return """Liebe(r) {name},\n\nIhre Anmeldung wurde akzeptiert. Wir freuen uns auf Ihren Besuch.\n\n{info}\n\nMit freundlichen Grüßen,\n""" + company_name

def check_deny_condition(contacts, limit=20):
    return len(contacts) - 1 > limit

def get_tag_mapping(bucket):
    blob = bucket.get_blob("tag_mapping.json")
    if blob:
        return json.loads(blob.download_as_string())
    return {}

def get_tag_info(bucket, tag_id):
    blob = bucket.get_blob(f"{tag_id}.txt")
    if blob:
        return blob.download_as_string().decode()
    return None

def get_registration_message(bucket, accepted):
    response_type = "accept" if accepted else "deny"
    
    blob = bucket.get_blob(f"{response_type}.txt")
    if blob:
        return blob.download_as_string().decode()
    return None
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

def send_email(gmail_service, sender, to, subject, body):
    """Send an email message."""
    try:
        if isinstance(to, list):
            to = ', '.join(to)
        message = create_email_message(sender, to, subject, body)
        message = gmail_service.users().messages().send(userId="me", body=message).execute()
        print(f'Sent message to {to} Message Id: {message["id"]}')
    except HttpError as error:
        print(f'An error occurred: {error}')
        
def get_message_by_history_id(gmail_service, start_history_id):
    # Step 1: Fetch the history of changes starting from the provided history ID
    response = gmail_service.users().history().list(
        userId='me',
        startHistoryId=start_history_id,
        # historyTypes=['messageAdded'],  # We're looking for any change
        maxResults=1  # Get only one message change, because we process one message at a time
    ).execute()
    
    # Step 2: Extract the message ID from the history response
    if 'history' in response:
        for history_record in response['history']:
            message_id = history_record['messages'][0]['id']
                
            # Step 3: Fetch the full message using the message ID
            message = gmail_service.users().messages().get(userId='me', id=message_id, format='full').execute()
            return message
    else:
        print("No messages found for the given history ID.")
        return None
#endregion

#region registration functions
def extract_registration_info(text):
    # pattern specified by customer, registration email text
    pattern = re.compile(r"Von:\s*(?P<name>.+)\s+E-Mail:\s*(?P<email>.+)\s+Telefon:\s*(?P<phone>.+)\s+Gewünschter Kurs:\s*(?P<course>.+)\s+Nachrichtentext:\s*(?P<message>.+)\s*(--|$)", re.DOTALL)
    
    info = pattern.search(text)
    if info:
        return info.groupdict()
    return None

def form_spreadsheet_entry(info_dict):
    return [info_dict['name'], info_dict['email'], info_dict['phone']]
#endregion

@functions_framework.cloud_event
def process(cloud_event):
    '''Function to be run in Cloud Run to process registration emails.'''
    
    # sign in
    creds = json.loads(os.getenv("CREDS"))
    if creds:
        creds = Credentials.from_authorized_user_info(creds, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
    
    # define services
    sheets_service = build("sheets", "v4", credentials=creds)
    spreadsheets = sheets_service.spreadsheets()
    gmail_service = build('gmail', 'v1', credentials=creds)
    
    storage_client = storage.Client(project=PROJECT_ID, credentials=creds)
    bucket = storage_client.get_bucket(BUCKETNAME)
    calendar_bucket = storage_client.get_bucket(CALENDAR_BUCKETNAME)
    
    # get data from Cloud Run call (gmail watch)
    response = base64.b64decode(cloud_event.data["message"]["data"]).decode("utf-8")
    response = json.loads(response)
    
    message_info = get_message_by_history_id(gmail_service, response['historyId'])
    
    # check if this is a registration message
    headers = message_info['payload']['headers']
    for header in headers:
        if header['name'] == "subject":
            subject = header['value']
        if header['name'] == "from":
            sender = header['value']
    if subject != "Kontaktformularanfrage":
        print("Other message")
        return
    
    content = message_info['snippet']
    # parse content
    registration_info = extract_registration_info(content)
    
    # check conditions for registration
    ## get contacts info
    email = registration_info['email']
    tag = registration_info['course'] 
    tag2id = get_tag_mapping(calendar_bucket)
    if tag2id:
        tag = tag2id.get(tag, tag)
    tag_info = get_tag_info(bucket, tag)
    
    contacts = spreadsheets.values().get(spreadsheetId=CONTACTS_SPREADSHEET_ID, range=f"{tag}!{SPREADSHEET_RANGE}").execute()
    if check_deny_condition(contacts): # deny registration
        message = get_deny_registration_template().format(name=registration_info['name'], course=tag)
    else: # accept registration
        info = tag_info if tag_info else ""
        message = get_accept_registration_template().format(name=registration_info['name'], course=tag, info=info)
        
        # values to add to spreadsheet
        body = {
            'values': [
                [registration_info['name'], registration_info['email'], registration_info['phone']]
            ]
        }
        # log changes to spreadsheet
        result = spreadsheets.values().append(
            spreadsheetId=CONTACTS_SPREADSHEET_ID,
            range=tag,
            valueInputOption='RAW',
            body=body
        ).execute()
    
    # send answer letter
    send_email(gmail_service, sender=SENDER_EMAIL, to=email, subject=str(tag), body=message)
