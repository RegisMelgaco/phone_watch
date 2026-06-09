#!/usr/bin/env python3
"""
Phone number test using Twilio.

Steps:
  1. Connect to Twilio
  2. Call the test number (records incoming audio)
  3. Check if the call completes
  4. Check if recording duration >= 15 s
  5. If failed or audio too short → call the plantonist
  6. Save all results to call_results.csv
"""

import csv
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────────
ACCOUNT_SID       = os.environ["TWILIO_ACCOUNT_SID"]
AUTH_TOKEN        = os.environ["TWILIO_AUTH_TOKEN"]
FROM_NUMBER       = os.environ["TWILIO_FROM_NUMBER"]      # Twilio number
TEST_NUMBER       = os.environ["TEST_NUMBER"]              # Number to test
PLANTONIST_NUMBER = os.environ["PLANTONIST_NUMBER"]        # On-call number

MIN_AUDIO_DURATION = 15    # minimum acceptable recording duration (seconds)
CSV_FILE = os.environ.get("CSV_FILE", "call_results.csv")
POLL_INTERVAL      = 5     # seconds between call-status polls
CALL_TIMEOUT       = 90    # max seconds to wait for the call to end
RECORDING_WAIT     = 10    # seconds to wait before fetching recordings

COMPLETED_STATUSES = {"completed"}
FAILED_STATUSES    = {"failed", "busy", "no-answer", "canceled"}

CSV_FIELDS = [
    "timestamp",
    "test_number",
    "call_sid",
    "call_status",
    "call_duration_s",
    "recording_sid",
    "recording_duration_s",
    "audio_ok",
    "result",
    "plantonist_called",
    "plantonist_call_sid",
    "notes",
]

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── CSV helper ────────────────────────────────────────────────────────────────

def write_result(row: dict) -> None:
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
    log.info("Result saved → %s", CSV_FILE)


# ── Step functions ────────────────────────────────────────────────────────────

def step1_connect() -> Client:
    """Authenticate and verify the Twilio connection."""
    log.info("Step 1 – Connecting to Twilio …")
    client = Client(ACCOUNT_SID, AUTH_TOKEN)
    client.api.accounts(ACCOUNT_SID).fetch()  # raises on bad credentials
    log.info("Step 1 – Connected ✓")
    return client


def step2_make_call(client: Client):
    """
    Place a call to TEST_NUMBER.
    TwiML records whatever the remote party plays/says for up to 60 s.
    """
    log.info("Step 2 – Calling %s …", TEST_NUMBER)

    twiml = VoiceResponse()
    twiml.record(max_length=30, timeout=5, play_beep=False)
    twiml.hangup()

    call = client.calls.create(
        to=TEST_NUMBER,
        from_=FROM_NUMBER,
        twiml=str(twiml),
        timeout=30,  # ring timeout in seconds
    )
    log.info(
        "Step 2 – Call SID: %s  |  Initial status: %s",
        call.sid,
        call.status,
    )
    return call


def step3_wait_for_completion(client: Client, call_sid: str):
    """Poll call status until completed / failed / timed-out."""
    log.info("Step 3 – Waiting for call to finish …")
    elapsed = 0

    while elapsed < CALL_TIMEOUT:
        call = client.calls(call_sid).fetch()
        log.info("  status: %-12s (%d s elapsed)", call.status, elapsed)

        if call.status in COMPLETED_STATUSES:
            log.info("Step 3 – Call completed ✓")
            return call, True

        if call.status in FAILED_STATUSES:
            log.warning("Step 3 – Call ended with status: %s", call.status)
            return call, False

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    # Timeout reached
    call = client.calls(call_sid).fetch()
    log.warning(
        "Step 3 – Timed out waiting (final status: %s)", call.status
    )
    return call, call.status in COMPLETED_STATUSES


def step4_check_audio(
    client: Client, call_sid: str
) -> tuple[str, int, bool]:
    """
    Fetch recordings for the call and verify the duration >= MIN_AUDIO_DURATION.
    Returns (recording_sid, duration_seconds, audio_ok).
    """
    log.info(
        "Step 4 – Waiting %d s for recording to be processed …",
        RECORDING_WAIT,
    )
    time.sleep(RECORDING_WAIT)

    recordings = client.recordings.list(call_sid=call_sid)

    if not recordings:
        log.warning("Step 4 – No recordings found for call %s", call_sid)
        return "", 0, False

    # Pick the longest recording in case there are multiple
    rec = max(recordings, key=lambda r: int(r.duration or 0))
    duration = int(rec.duration or 0)
    ok = duration >= MIN_AUDIO_DURATION

    log.info(
        "Step 4 – Recording %s: %d s  |  audio_ok=%s  (min %d s)",
        rec.sid,
        duration,
        ok,
        MIN_AUDIO_DURATION,
    )
    return rec.sid, duration, ok


def step5_call_plantonist(client: Client, reason: str) -> str:
    """Call the on-call person with a voice alert message."""
    log.warning(
        "Step 5 – Alerting plantonist (%s): %s",
        PLANTONIST_NUMBER,
        reason,
    )

    twiml = VoiceResponse()
    twiml.say(
        f"Alert! The monitored phone number test has failed. "
        f"Reason: {reason}. Please investigate immediately.",
        voice="alice",
        language="en-US",
    )

    call = client.calls.create(
        to=PLANTONIST_NUMBER,
        from_=FROM_NUMBER,
        twiml=str(twiml),
    )
    log.info("Step 5 – Plantonist call SID: %s", call.sid)
    return call.sid


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_test() -> None:
    # Initialise result row with safe defaults
    row: dict = {f: "" for f in CSV_FIELDS}
    row.update(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        test_number=TEST_NUMBER,
        audio_ok=False,
        result="fail",
        plantonist_called=False,
    )

    # ── Step 1: Connect ────────────────────────────────────────────────────
    try:
        client = step1_connect()
    except (TwilioRestException, KeyError) as exc:
        row["notes"] = f"Twilio connection failed: {exc}"
        log.error(row["notes"])
        write_result(row)
        return

    # ── Step 2: Make call ──────────────────────────────────────────────────
    try:
        call = step2_make_call(client)
        row["call_sid"] = call.sid
    except TwilioRestException as exc:
        row["notes"] = f"Call creation failed: {exc}"
        log.error(row["notes"])
        write_result(row)
        return

    # ── Step 3: Wait for completion ────────────────────────────────────────
    call, call_completed = step3_wait_for_completion(client, call.sid)
    row["call_status"]    = call.status
    row["call_duration_s"] = call.duration or 0

    # ── Step 4: Check audio ────────────────────────────────────────────────
    rec_sid, rec_duration, audio_ok = "", 0, False
    if call_completed:
        rec_sid, rec_duration, audio_ok = step4_check_audio(
            client, call.sid
        )

    row.update(
        recording_sid=rec_sid,
        recording_duration_s=rec_duration,
        audio_ok=audio_ok,
    )

    # ── Evaluate ───────────────────────────────────────────────────────────
    if call_completed and audio_ok:
        row["result"] = "pass"
        log.info("✅  TEST PASSED")
    else:
        if not call_completed:
            reason = f"Call did not complete (status={call.status})"
        elif rec_duration == 0:
            reason = "No audio was recorded"
        else:
            reason = (
                f"Audio too short "
                f"({rec_duration}s < {MIN_AUDIO_DURATION}s required)"
            )

        row["notes"] = reason
        log.warning("❌  TEST FAILED – %s", reason)

        # ── Step 5: Call plantonist ────────────────────────────────────────
        try:
            plantonist_sid = step5_call_plantonist(client, reason)
            row["plantonist_called"]   = True
            row["plantonist_call_sid"] = plantonist_sid
        except TwilioRestException as exc:
            extra = f" | Plantonist alert also failed: {exc}"
            row["notes"] += extra
            log.error(extra.strip())

    write_result(row)


if __name__ == "__main__":
    run_test()