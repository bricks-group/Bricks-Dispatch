# This file will contain SMS notification logic for the Dispatch service.

from twilio.rest import Client as TwilioClient
import os

# It's good practice to load environment variables or have them configured.
# The sms.py utility in Booking backend already handles loading them.
# For this file, we assume TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER are set.

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

def send_dispatch_sms(to: str, body: str) -> bool:
    """Sends an SMS message for dispatch notifications."""
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
        print("Twilio credentials not fully configured. Skipping dispatch SMS.")
        return False
    
    client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    try:
        message = client.messages.create(
            body=body,
            from_=TWILIO_PHONE_NUMBER,
            to=to
        )
        print(f"Dispatch SMS sent to {to} with status: {message.status}")
        return message.status in ('queued', 'sent', 'delivered')
    except Exception as e:
        print(f"Error sending dispatch SMS: {e}")
        return False

# Placeholder functions for actual notification triggering

def notify_job_assigned_to_vendor(vendor_phone: str, job_details: dict) -> bool:
    """Sends SMS when a job is assigned to a vendor."""
    body = f"New job assigned to you: {job_details.get('job_id', 'N/A')}. Details: {job_details.get('description', 'N/A')}"
    return send_dispatch_sms(to=vendor_phone, body=body)

def notify_job_status_change(vendor_phone: str, job_details: dict, new_status: str) -> bool:
    """Sends SMS when a job's status changes."""
    body = f"Job {job_details.get('job_id', 'N/A')} status updated to: {new_status}."
    return send_dispatch_sms(to=vendor_phone, body=body)
